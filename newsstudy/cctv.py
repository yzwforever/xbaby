"""CCTV day/page extraction, adapted from DuckBurnIncense/xin-wen-lian-bo (MIT).
See THIRD_PARTY_NOTICES.md. No videos, cookies or third-party transcript mirrors.
"""
import re
import time
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from .net import get_text
from .settings import now

class NotReady(RuntimeError):
    pass

class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []
    def text(self):
        parts = []
        for c in self.children:
            if isinstance(c, str):
                parts.append(c)
            elif c.tag not in ('script', 'style'):
                parts.append(c.text() + ('\n' if c.tag in ('p', 'div', 'br', 'li') else ''))
        return ''.join(parts)
    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()

class DOM(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append(node)
    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, data):
        self.stack[-1].children.append(data)

def clean(text):
    return re.sub(r'[ \t\r\f\v\u3000]+', ' ', text).strip()

def _durations(nodes):
    """Map item URL -> seconds, from the day index's `<div class="image">` duration spans."""
    result = {}
    for node in nodes:
        if node.tag != 'div' or node.attrs.get('class') != 'image':
            continue
        link = next((c for c in node.children if isinstance(c, Node) and c.tag == 'a' and c.attrs.get('href')), None)
        span = next((c for c in node.children if isinstance(c, Node) and c.tag == 'span'), None)
        m = re.fullmatch(r'(\d{1,2}):(\d{2}):(\d{2})', clean(span.text())) if span is not None else None
        if link is None or m is None:
            continue
        u = urlsplit(urljoin('https://tv.cctv.com', link.attrs['href']))
        if u.hostname == 'tv.cctv.com' and u.path.endswith('.shtml'):
            h, mi, s = map(int, m.groups())
            result['https://tv.cctv.com' + u.path] = h * 3600 + mi * 60 + s
    return result

def parse_day(html, day):
    dt = date.fromisoformat(day)
    stamp = dt.strftime('%Y%m%d')
    path_date = dt.strftime('/%Y/%m/%d/')
    nodes = list(DOM(html).root.walk())
    durations = _durations(nodes)
    links, seen, videos = [], set(), []
    for node in nodes:
        if node.tag != 'a':
            continue
        url = urljoin('https://tv.cctv.com', node.attrs.get('href', ''))
        u = urlsplit(url)
        title = clean(node.attrs.get('title') or node.attrs.get('alt') or node.text())
        if u.hostname != 'tv.cctv.com' or path_date not in u.path or not u.path.endswith('.shtml'):
            continue
        url = 'https://tv.cctv.com' + u.path
        if url in seen:
            continue
        seen.add(url)
        if '新闻联播' in title and stamp in title:
            videos.append((title, url))
        elif title and '主要内容' not in title and '天气预报' not in title:
            links.append({'title': title.replace('[视频]', '').replace('完整版', '').strip(),
                          'url': url, 'duration': durations.get(url)})
    if not videos or len(links) < 3:
        raise NotReady('当日整期链接或新闻列表尚不齐全，将保留待重试')
    videos.sort(key=lambda x: '19:00' not in x[0])
    return videos[0][1], links, durations.get(videos[0][1])

def parse_article(html):
    nodes = list(DOM(html).root.walk())
    content = next((n for n in nodes if n.attrs.get('id') == 'content_area'), None)
    if not content:
        raise NotReady('央视正文结构未识别，未用简介冒充全文')
    text = '\n'.join(clean(s) for s in content.text().splitlines() if clean(s))
    if len(text) < 25:
        raise NotReady('新闻正文缺失或过短，稍后重试')
    return text

def fetch_day(day, log=print, pause=0.35):
    url = 'https://tv.cctv.com/lm/xwlb/day/' + day.replace('-', '') + '.shtml'
    video, links, total = parse_day(get_text(url), day)
    articles = []
    for i, item in enumerate(links, 1):
        log(f'采集 {day}：{i}/{len(links)} {item["title"]}')
        text = parse_article(get_text(item['url']))
        articles.append({'id': f'N{i:02}', 'broadcast_order': i, **item, 'text': text})
        time.sleep(pause)
    # Re-read index to catch a broadcast still being populated. A later publisher edit
    # cannot be guaranteed absent; report explicitly documents this source boundary.
    _, latest, _ = parse_day(get_text(url), day)
    if {a['url'] for a in links} != {a['url'] for a in latest}:
        raise NotReady('采集期间新闻目录发生变化，稍后重新获取以避免遗漏')
    timed = all(isinstance(a.get('duration'), int) for a in articles)
    if timed:
        # The day index lists each segment's own clip; start points are cumulative
        # approximations that exclude the opening titles and transitions.
        offset = 0
        for a in articles:
            a['start_offset'] = offset
            offset += a['duration']
    return {'date': day, 'source_url': url, 'video_url': video, 'video_total': total,
            'timestamp_note': ('视频位置按当日分条时长累计估算，未计入片头与转场，仅供参考定位。' if timed else
                               '当日分条时长未完整提供，本期无法标注视频定位。'),
            'fetched_at': now().isoformat(),
            'completeness': '本次央视目录中逐条正文均已获取；不保证发布方后续不补充或修改。',
            'articles': articles}
