import argparse
import sys
from .settings import ROOT, load_config

def main():
    parser = argparse.ArgumentParser(description='新闻联播备考日报')
    parser.add_argument('command', nargs='?', default='gui', choices=['gui', 'run', 'reading', 'status'])
    parser.add_argument('--date', help='YYYY-MM-DD；run不指定则自动补跑')
    args = parser.parse_args()
    if args.command == 'gui':
        from .ui import launch
        launch()
    elif args.command == 'status':
        from .state import State
        state = State(ROOT)
        for r in state.all():
            print(r['day'], r['status'], '通知状态', r['notified'], r['error'])
        state.close()
    else:
        from .pipeline import run, make_reading, log_line
        try:
            if args.command == 'run':
                return 0 if run(day=args.date) else 1
            if not args.date:
                parser.error('reading 必须提供 --date YYYY-MM-DD')
            make_reading(args.date, load_config(), log=log_line)
        except Exception as e:
            log_line(str(e) if isinstance(e, (RuntimeError, ValueError)) else '运行失败：' + type(e).__name__)
            return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
