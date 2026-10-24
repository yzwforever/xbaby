from html import escape
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, KeepTogether
from .analysis import speech_candidates

NAVY = colors.HexColor('#15354A')
TEAL = colors.HexColor('#087E85')

FALLBACK_FONTS = ('/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',   # GitHub Actions: apt fonts-wqy-microhei
                  'C:/Windows/Fonts/simhei.ttf', 'C:/Windows/Fonts/msyh.ttc')

def register_font(font_path):
    """Try configured/CJK TTF or TTC files, else ReportLab's built-in CID font (no file needed)."""
    for path in (font_path, *FALLBACK_FONTS):
        if path and Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont('Chinese', path, subfontIndex=0))
                return 'Chinese'
            except Exception:
                continue
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    return 'STSong-Light'

def mmss(seconds):
    return f'{seconds // 60:02d}:{seconds % 60:02d}'

def position_note(article):
    start, dur = article.get('start_offset'), article.get('duration')
    if not isinstance(start, int) or not isinstance(dur, int):
        return ''
    return f'视频约 {mmss(start)} 起 · 时长约 {mmss(dur)}'

def render(raw, result, output, font_path):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    font = register_font(font_path)
    styles = {
        'title': ParagraphStyle('title', fontName=font, fontSize=25, leading=34, textColor=NAVY, spaceAfter=14),
        'h1': ParagraphStyle('h1', fontName=font, fontSize=17, leading=25, textColor=NAVY, spaceBefore=14, spaceAfter=10, keepWithNext=True),
        'h2': ParagraphStyle('h2', fontName=font, fontSize=12, leading=19, textColor=TEAL, spaceBefore=9, spaceAfter=5, keepWithNext=True),
        'body': ParagraphStyle('body', fontName=font, fontSize=10.5, leading=17, spaceAfter=7, wordWrap='CJK', alignment=TA_LEFT),
        'small': ParagraphStyle('small', fontName=font, fontSize=8.3, leading=13, textColor=colors.HexColor('#526775'), spaceAfter=6, wordWrap='CJK'),
        'quote': ParagraphStyle('quote', fontName=font, fontSize=10, leading=17, leftIndent=10, rightIndent=8, borderPadding=8,
                                backColor=colors.HexColor('#F0F5F7'), spaceBefore=5, spaceAfter=12, wordWrap='CJK'),
    }
    story = []
    def p(value, style='body'):
        story.append(Paragraph(escape(str(value)).replace('\n', '<br/>'), styles[style]))
    def link(label, url):
        story.append(Paragraph(f'<link href="{escape(url, quote=True)}" color="#087E85">{escape(label)}</link>', styles['small']))
    reading = result['mode'] != 'ai'
    p('新闻联播 · 备考日报', 'title')
    p(raw['date'] + ('  |  原文阅读版 · 未经AI分析' if reading else '  |  AI辅助学习版'), 'h2')
    p('上海理工大学 · 2027入学\n701 马克思主义基本原理 / 870 中国化的马克思主义\n江苏 · 上海 · 浙江 · 安徽省考与事业编', 'body')
    p(f'本期 {len(raw["articles"])} 条新闻；采集时间 {raw["fetched_at"]}', 'small')
    if raw.get('timestamp_note'):
        p(raw['timestamp_note'], 'small')
    p(raw['completeness'], 'small')
    link('央视整期视频（仅观看链接，不下载）', raw['video_url'])
    p('阅读说明', 'h1')
    p('新闻事实以央视正文为依据。考试关联是复习建议，不是命题预测或真题。引文逐字匹配仅能保证与采集正文一致，不能替代人工核实讲话归属和完整语境。')
    p(result['profile']['scope_note'], 'small')
    if reading:
        p('当前未启用模型。本文件提供正文节选、讲话候选段落和来源索引；没有重要性判断或考试关联。配置模型后可生成完整分析版。', 'quote')
    else:
        p('今日优先阅读', 'h1')
        sources = {a['id']: a for a in raw['articles']}
        for i, item in enumerate(result['items'][:3], 1):
            p(f'{i}. {sources[item["id"]]["title"]}', 'h2')
            note = position_note(sources[item['id']])
            if note:
                p(note, 'small')
            p(item['reason'])
        p('排序参考政策层级、公共影响、新信息和长期意义；分值是模型给出的学习排序，不是官方权重。', 'small')
    story.append(PageBreak())
    p('01  新闻分点速读', 'h1')
    sources = {a['id']: a for a in raw['articles']}
    for i, item in enumerate(result['items'], 1):
        a = sources[item['id']]
        badge = '原文节选' if reading else ('重点' if item['importance'] >= 75 else '关注' if item['importance'] >= 50 else '速览')
        p(f'{i:02}  [{badge}] {a["title"]}', 'h2')
        meta = f'来源编号 {a["id"]} · 央视播出顺序 {a["broadcast_order"]}'
        note = position_note(a)
        if note:
            meta += f' · {note}'
        if not reading:
            meta += f' · 重要性 {item["importance"]}/100'
        p(meta, 'small')
        for s in item['summary']:
            p('• ' + s)
        if not reading:
            p('排序理由：' + item['reason'], 'small')
        link('查看央视原文', a['url'])
    story.append(PageBreak())
    p('02  讲话摘录与原理分析', 'h1')
    topics = {t['id']: t for t in result['profile']['topics']}
    count = 0
    for item in result['items']:
        for q in item['speeches']:
            count += 1
            p(f'{q["speaker"]} · {sources[item["id"]]["title"]}', 'h2')
            p(f'{q["kind"]} / 正文连续摘录 / 非讲话全文', 'small')
            p(q['excerpt'], 'quote')
            p('可能涉及：' + '；'.join(topics[t]['name'] for t in q['topic_ids']))
            p('分析：' + q['analysis'])
            link('讲话报道来源 ' + item['id'], sources[item['id']]['url'])
    if not count:
        p('本版没有经过AI分析的讲话条目。下方列出规则检出的原文候选段落供核对；不能视为已穷尽全部讲话。')
    # Retain every candidate paragraph independently of the model's selection.
    candidates = speech_candidates(raw)
    if candidates:
        p('原文候选段落存档', 'h2')
        p('以下由姓名与讲话动词规则检出，可能包含记者转述或出席报道。仅是来源摘录，不代表已确认的直接引语；完整正文另存于本地 source.json。', 'small')
        for q in candidates:
            p(q['id'] + '  ' + q['excerpt'], 'quote')
    story.append(PageBreak())
    p('03  考点关联与自拟练习', 'h1')
    if reading:
        p('待配置模型后生成。当前未提供押题或知识点判断。')
    else:
        books = {b['id']: b for b in result['profile']['books']}
        for item in result['items']:
            p(sources[item['id']]['title'], 'h2')
            if not item['study']:
                p('暂无充分依据建立直接考试关联。', 'small')
            for s in item['study']:
                topic = topics[s['topic_id']]
                p(f'[{s["relevance"]}相关] {s["track"]} / {s["region"]} / {topic["name"]}')
                p('分析依据：' + s['reason'])
                p('自拟练习（非真题）：' + s['practice'])
                p('复习书目主题：' + '；'.join(books[b]['title'] for b in topic['books']) + '。未定位教材章节或页码。', 'small')
        p('江苏、上海、浙江、安徽：全国性新闻可作为共同素材；地方标签不代表该地区独有考点。各年度和岗位考试范围须以官方大纲为准。', 'small')
    story.append(PageBreak())
    p('04  来源与复习范围', 'h1')
    for a in raw['articles']:
        label = a['id'] + ' · ' + a['title']
        note = position_note(a)
        link(label + (f'（{note}）' if note else ''), a['url'])
    p('书目与学校信息', 'h2')
    for b in result['profile']['books']:
        p(b['subject'] + ' · ' + b['title'] + '，' + b['edition'], 'small')
    for source in result['profile']['official_sources']:
        link(source['title'], source['url'])
    p('书目核验日期：' + result['profile']['verified_on'] + '。主题索引由本工具整理，不冒充教材目录或考试大纲。', 'small')
    p('生成方式：' + ('原文阅读版' if reading else 'AI辅助，模型：' + result['model']), 'small')
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#D4E1E8'))
        canvas.line(18*mm, 17*mm, 192*mm, 17*mm)
        canvas.setFont(font, 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(18*mm, 12*mm, raw['date'] + '  新闻联播备考日报' + (' · 阅读版' if reading else ''))
        canvas.drawRightString(192*mm, 12*mm, str(doc.page))
        canvas.restoreState()
    tmp = output.with_suffix('.tmp.pdf')
    doc = SimpleDocTemplate(str(tmp), pagesize=(210*mm, 297*mm), rightMargin=18*mm, leftMargin=18*mm,
                            topMargin=18*mm, bottomMargin=23*mm, title=f'{raw["date"]} 新闻联播备考日报', author='NewsStudy')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    tmp.replace(output)
    return output
