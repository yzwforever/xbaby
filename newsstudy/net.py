import json
import time
import urllib.error
import urllib.request

class NetworkError(RuntimeError):
    pass

def request(url, data=None, headers=None, attempts=3, timeout=45):
    """Only idempotent requests retry by default. Never log credential URLs/bodies."""
    headers = {'User-Agent': 'NewsStudy/0.1 (personal study)', **(headers or {})}
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(12_000_001)
                if len(raw) > 12_000_000:
                    raise NetworkError('响应过大，已停止读取')
                return raw
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == attempts - 1:
                raise NetworkError(f'服务返回 HTTP {e.code}，请检查配置或稍后重试') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if n == attempts - 1:
                raise NetworkError('网络连接失败或超时，请检查网络/代理后重试') from None
        time.sleep(2 ** n)

def get_text(url):
    return request(url).decode('utf-8-sig', errors='replace')

def post_json(url, obj, headers=None, timeout=180):
    raw = request(url, json.dumps(obj, ensure_ascii=False).encode(),
                  {'Content-Type': 'application/json', **(headers or {})}, attempts=1, timeout=timeout)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise NetworkError('服务未返回有效 JSON') from None
