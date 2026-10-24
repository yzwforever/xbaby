from __future__ import annotations
import base64
import ctypes
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TZ = timezone(timedelta(hours=8))

def now():
    return datetime.now(TZ)

DEFAULT = {
    'start_date': '2026-10-23', 'run_time': '21:30', 'max_days_per_run': 7,
    'model_base_url': '', 'model_name': '', 'model_max_tokens': 14000, 'model_batch_size': 3,
    'cos_region': '', 'cos_bucket': '', 'link_hours': 72,
    'pdf_host': 'cos', 'repo_pdf_base_url': '', 'repo_cdn_base_url': '',
    'upload_enabled': False, 'push_enabled': False,
    'font_path': 'C:/Windows/Fonts/simhei.ttf',
}
SECRET_NAMES = ('model_api_key', 'cos_secret_id', 'cos_secret_key', 'serverchan_sendkey')

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def load_config(root=ROOT):
    config = deepcopy(DEFAULT)
    p = root / 'config.json'
    if p.exists():
        config.update(json.loads(p.read_text(encoding='utf-8-sig')))
    if os.environ.get('NEWSSTUDY_REPO_PDF_BASE_URL'):
        config['repo_pdf_base_url'] = os.environ['NEWSSTUDY_REPO_PDF_BASE_URL']
    if os.environ.get('NEWSSTUDY_REPO_CDN_BASE_URL'):
        config['repo_cdn_base_url'] = os.environ['NEWSSTUDY_REPO_CDN_BASE_URL']
    return config

def validate_config(c):
    datetime.strptime(c['start_date'], '%Y-%m-%d')
    datetime.strptime(c['run_time'], '%H:%M')
    if not 1 <= int(c['max_days_per_run']) <= 30:
        raise ValueError('单次补跑天数必须在 1 至 30 之间')
    if not 1 <= int(c.get('model_batch_size', 3)) <= 5:
        raise ValueError('单批分析条数必须在 1 至 5 之间')
    if not 1 <= int(c['link_hours']) <= 168:
        raise ValueError('链接有效期必须在 1 至 168 小时之间')
    if c['model_base_url']:
        from urllib.parse import urlsplit
        u = urlsplit(c['model_base_url'])
        if u.scheme != 'https' or not u.hostname or u.username or u.query or u.fragment:
            raise ValueError('模型 API 地址必须是 HTTPS，不能含密钥、查询参数或账号')
    if c['push_enabled'] and not c['upload_enabled']:
        raise ValueError('微信推送需要同时开启 PDF 上传（仓库托管或COS）')
    if c.get('pdf_host') not in ('cos', 'repo'):
        raise ValueError('PDF 托管方式只能是 cos 或 repo')
    if c.get('pdf_host') == 'repo' and c['upload_enabled']:
        from urllib.parse import urlsplit
        base = c.get('repo_pdf_base_url') or ''
        u = urlsplit(base) if base else None
        if not u or u.scheme != 'https' or not u.hostname or u.username or u.query or u.fragment:
            raise ValueError('仓库托管需要 https 开头、不含凭据或查询参数的 PDF 基础链接')

class Blob(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_ulong), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

def protect(data: bytes, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('本地密钥保存只支持 Windows DPAPI；其他系统请使用环境变量')
    buf = ctypes.create_string_buffer(data)
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    out = Blob()
    api = ctypes.windll.crypt32
    if decrypt:
        ok = api.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out))
    else:
        ok = api.CryptProtectData(ctypes.byref(src), 'NewsStudy', None, None, None, 1, ctypes.byref(out))
    if not ok:
        raise RuntimeError('Windows 密钥加密/解密失败；请用保存配置时的 Windows 账号运行')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)

def load_secrets(root=ROOT):
    values = {}
    path = root / 'secrets.dat'
    if path.exists():
        values = json.loads(protect(base64.b64decode(path.read_bytes()), True))
    for name in SECRET_NAMES:
        if os.environ.get('NEWSSTUDY_' + name.upper()):
            values[name] = os.environ['NEWSSTUDY_' + name.upper()]
    return values

def save_secrets(values, root=ROOT):
    raw = json.dumps(values, ensure_ascii=False).encode()
    path = root / 'secrets.dat'
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(base64.b64encode(protect(raw)))
    os.replace(tmp, path)
