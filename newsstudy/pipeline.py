import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from . import analysis, cctv, delivery, report
from .settings import ROOT, TZ, now, atomic_json, load_config, load_secrets, validate_config
from .state import State

@contextmanager
def single_instance(root):
    p = root / 'data/run.lock'
    p.parent.mkdir(parents=True, exist_ok=True)
    f = p.open('a+b')
    f.seek(0)
    try:
        if not f.read(1):
            f.write(b'0'); f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise RuntimeError('另一个任务正在运行，本次退出以避免重复操作') from None
    try:
        yield
    finally:
        f.close()

def due_dates(config, current=None):
    current = (current or now()).astimezone(TZ)
    h, m = map(int, config['run_time'].split(':'))
    latest = current.date() if (current.hour, current.minute) >= (h, m) else current.date()-timedelta(days=1)
    first = date.fromisoformat(config['start_date'])
    if (latest-first).days > 3660:
        raise ValueError('开始日期过早，请选择近十年内日期')
    return [(first+timedelta(days=i)).isoformat() for i in range(max(0, (latest-first).days+1))]

def log_line(message, root=ROOT):
    line = f'{now().strftime("%Y-%m-%d %H:%M:%S")} {message}'
    print(line, flush=True)
    folder = root / 'logs'
    folder.mkdir(exist_ok=True)
    with (folder / (now().strftime('%Y-%m') + '.log')).open('a', encoding='utf-8') as f:
        f.write(line+'\n')

def collect(day, root=ROOT, log=print):
    folder = root / 'data' / day
    p = folder / 'source.json'
    if p.exists():
        return json.loads(p.read_text(encoding='utf-8'))
    raw = cctv.fetch_day(day, log)
    atomic_json(p, raw)
    atomic_json(folder / 'speech-candidates.json', analysis.speech_candidates(raw))
    return raw

def make_reading(day, config, root=ROOT, log=print):
    with single_instance(root):
        raw = collect(day, root, log)
        dest = root / 'output/pdf' / f'{day}-reading.pdf'
        report.render(raw, analysis.reading_copy(raw), dest, config['font_path'])
        log(f'原文阅读版已生成（未做AI分析，不会上传或推送）：{dest}')
        return dest

def push_pending(state, config, secrets, log):
    if not config['push_enabled']:
        return
    if not secrets.get('serverchan_sendkey'):
        log('待配置微信 SendKey，已保留本地报告和上传记录')
        return
    eligible = [r for r in reversed(state.all()) if r['status'] == 'uploaded' and r['notified'] == 0]
    # Small combined notifications: backfills don't create one message per news item.
    for start in range(0, len(eligible), 3):
        batch = eligible[start:start+3]
        body = []
        usable = []
        for row in batch:
            # COS 链接带过期时间，临期则跳过等重签；仓库托管链接 expires 为空=长期有效。
            expired = bool(row['expires']) and datetime.fromisoformat(row['expires']) <= now()+timedelta(minutes=10)
            if expired:
                log(row['day'] + ' 下载链接已过期，等待下一轮重新签名')
                continue
            result = json.loads((Path(row['pdf']).parent / 'analysis.json').read_text(encoding='utf-8'))
            raw = json.loads((Path(row['pdf']).parent / 'source.json').read_text(encoding='utf-8'))
            titles = {a['id']: a['title'] for a in raw['articles']}
            starts = {a['id']: a.get('start_offset') for a in raw['articles']}
            def _with_pos(aid):
                sec = starts.get(aid)
                return titles[aid] + (f'（视频约 {sec // 60:02d}:{sec % 60:02d} 起）' if isinstance(sec, int) else '')
            highlights = '\n'.join('- ' + _with_pos(i['id']) for i in result['items'][:3])
            expiry = (f'链接有效至 {row["expires"][:16]}（北京时间）。' if row['expires']
                      else 'PDF 链接由仓库长期提供。')
            repo_base = (config.get('repo_pdf_base_url') or '').rstrip('/')
            backup = ''
            if repo_base and row['url'] != repo_base + f'/public/pdf/{row["day"]}.pdf':
                backup = f'\n\n[备用直连（较慢）]({repo_base}/public/pdf/{row["day"]}.pdf)'
            body.append(f'## {row["day"]} 新闻联播\n{highlights}\n\n[下载备考PDF]({row["url"]}){backup}\n\n[央视视频]({raw["video_url"]})\n\n{expiry}考试关联仅供复习。')
            usable.append(row)
        if not usable:
            continue
        days = [r['day'] for r in usable]
        # Persist ambiguity BEFORE network. If process dies after server accepted,
        # user resolves status; do not silently duplicate external messages.
        cur = state.db.execute('INSERT INTO outbox(days,status,updated) VALUES(?,?,?)',
                               (json.dumps(days), 'uncertain', now().isoformat()))
        oid = cur.lastrowid
        for d in days:
            state.db.execute('UPDATE days SET notified=2 WHERE day=?', (d,))
        state.db.commit()
        try:
            delivery.send_wechat(f'新闻联播备考日报 | {days[-1]}' + (f' 等{len(days)}期' if len(days)>1 else ''),
                                 '\n\n---\n\n'.join(body), secrets['serverchan_sendkey'])
        except Exception:
            log('推送结果待核对：请查看手机或Server酱后台，再在界面选择“已收到”或“允许重试”。批次 ' + str(oid))
            continue
        state.db.execute('UPDATE outbox SET status=?,updated=? WHERE id=?', ('sent', now().isoformat(), oid))
        for d in days:
            state.db.execute('UPDATE days SET notified=1 WHERE day=?', (d,))
        state.db.commit()
        log('微信服务已接受推送：' + '、'.join(days))

def resolve_notifications(root, received):
    state = State(root)
    try:
        status = 'sent' if received else 'retry-approved'
        with state.db:
            state.db.execute('UPDATE days SET notified=? WHERE notified=2', (1 if received else 0,))
            state.db.execute('UPDATE outbox SET status=?,updated=? WHERE status=?', (status, now().isoformat(), 'uncertain'))
    finally:
        state.close()

def run(root=ROOT, day=None, log=None):
    log = log or (lambda s: log_line(s, root))
    config, secrets = load_config(root), load_secrets(root)
    validate_config(config)
    if not all((config['model_base_url'], config['model_name'], secrets.get('model_api_key'))):
        log('尚未配置模型API，自动任务未启动。可先在界面生成“原文阅读版”。')
        return False
    with single_instance(root):
        state = State(root)
        try:
            dates = [date.fromisoformat(day).isoformat()] if day else due_dates(config)
            if day and day > now().date().isoformat():
                raise ValueError('不能处理未来日期')
            work = []
            for d in dates:
                row = state.get(d)
                needs = not row or row['status'] not in ('rendered', 'uploaded')
                if row and row['status'] == 'rendered' and config['upload_enabled']:
                    needs = True
                if row and row['status'] == 'uploaded' and row['notified'] == 0 and config['push_enabled']:
                    # COS 链接过期需重新签名；仓库托管链接永久有效，无需重发。
                    needs = not row['url'] or (bool(row['expires']) and
                           datetime.fromisoformat(row['expires']) <= now()+timedelta(minutes=10))
                if row and row['pdf'] and not Path(row['pdf']).is_file():
                    needs = True
                if needs:
                    work.append(d)
            if len(work) > int(config['max_days_per_run']):
                log(f'待补跑 {len(work)} 天，本轮处理最早 {config["max_days_per_run"]} 天，其余下轮继续')
            failed = False
            for d in work[:int(config['max_days_per_run'])]:
                try:
                    raw = collect(d, root, log)
                    folder = root / 'output/pdf' / d
                    result_path = folder / 'analysis.json'
                    if result_path.exists():
                        result = json.loads(result_path.read_text(encoding='utf-8'))
                        analysis.validate_result(result, raw['articles'], result['profile'])
                    else:
                        result = analysis.analyze(raw, config, secrets, log)
                        atomic_json(result_path, result)
                    atomic_json(folder / 'source.json', raw)
                    pdf = folder / 'report.pdf'
                    if not pdf.exists():
                        report.render(raw, result, pdf, config['font_path'])
                    state.put(d, status='rendered', pdf=str(pdf), error='')
                    log(d + ' PDF 已保存')
                    if config['upload_enabled']:
                        if config.get('pdf_host') == 'repo':
                            url = delivery.publish_repo_pdf(pdf, d, config, root)
                            state.put(d, status='uploaded', url=url, error='')
                            log(d + ' PDF 已发布到仓库 public/ 目录')
                        else:
                            url = delivery.upload_pdf(pdf, d, config, secrets)
                            expires = now()+timedelta(hours=int(config['link_hours']))
                            state.put(d, status='uploaded', url=url, expires=expires.isoformat(), error='')
                            log(d + ' PDF 已上传到私有存储')
                except cctv.NotReady as e:
                    # 文字稿晚间陆续发布属正常现象：留给后续触发轮次，不算失败。
                    row = state.get(d)
                    state.put(d, status=(row['status'] if row and row['status'] in ('rendered', 'uploaded') else 'pending'),
                              error=str(e)[:240])
                    log(d + ' 文字稿尚未齐全，本轮跳过，等待稍后自动补跑')
                except Exception as e:
                    failed = True
                    # Avoid storing exception strings from third-party SDKs (credentials).
                    safe = str(e) if isinstance(e, (RuntimeError, ValueError)) else '处理异常：' + type(e).__name__
                    row = state.get(d)
                    state.put(d, status=(row['status'] if row and row['status'] in ('rendered', 'uploaded') else 'pending'), error=safe[:240])
                    log(d + ' 待重试：' + safe[:240])
            push_pending(state, config, secrets, log)
            log('本轮结束。' + ('部分日期待重试。' if failed else ''))
            return not failed
        finally:
            state.close()
