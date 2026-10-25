import argparse
import os
import sys
from .settings import ROOT, load_config

def main():
    parser = argparse.ArgumentParser(description='新闻联播备考日报')
    parser.add_argument('command', nargs='?', default='gui', choices=['gui', 'run', 'reading', 'status',
                        'prepare-state', 'sync-state', 'resolve'])
    parser.add_argument('--date', help='YYYY-MM-DD；run不指定则自动补跑')
    parser.add_argument('--remote-persist', action='store_true', help='发送前必须将安全状态保存到远端 main')
    parser.add_argument('--recipient', help='status 显示的完整接收人指纹；不填则处理指定日期的待核对记录')
    decision = parser.add_mutually_exclusive_group()
    decision.add_argument('--received', action='store_true')
    decision.add_argument('--retry', action='store_true')
    args = parser.parse_args()
    remote = args.remote_persist or os.environ.get('NEWSSTUDY_REMOTE_PERSIST') == '1'
    persist = None
    if remote:
        from .persistence import GitPersistence
        persist = GitPersistence(ROOT)
    if args.command == 'gui':
        from .ui import launch
        launch()
    elif args.command == 'status':
        from .state import State
        state = State(ROOT)
        for r in state.all():
            print(r['day'], r['status'], '通知状态', r['notified'], r['error'])
        for r in state.db.execute('SELECT day,recipient,status FROM recipients ORDER BY day,recipient'):
            print(r['day'], r['recipient'], r['status'])
        state.close()
    else:
        from .pipeline import run, make_reading, log_line
        try:
            if args.command in ('prepare-state', 'sync-state'):
                if not persist:
                    parser.error('远端状态命令必须指定 --remote-persist')
                from .pipeline import single_instance
                from .state import State
                with single_instance(ROOT):
                    state = State(ROOT)
                    try:
                        persist.prepare(state) if args.command == 'prepare-state' else persist(state)
                    finally:
                        state.close()
                return 0
            if args.command == 'resolve':
                if not args.date or not (args.received or args.retry):
                    parser.error('resolve 必须指定 --date 和 --received 或 --retry')
                from datetime import date
                date.fromisoformat(args.date)
                from .pipeline import resolve_notifications
                resolve_notifications(ROOT, args.received, args.date, args.recipient, persist)
                return 0
            if args.command == 'run':
                if os.environ.get('GITHUB_ACTIONS') == 'true' and not persist:
                    raise RuntimeError('Actions 必须启用远端持久化屏障，未发送微信')
                return 0 if run(day=args.date, persist=persist) else 1
            if not args.date:
                parser.error('reading 必须提供 --date YYYY-MM-DD')
            make_reading(args.date, load_config(), log=log_line)
        except Exception as e:
            log_line(str(e) if isinstance(e, (RuntimeError, ValueError)) else '运行失败：' + type(e).__name__)
            return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
