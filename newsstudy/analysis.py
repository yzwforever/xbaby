import json
import re
from .net import post_json
from .settings import ROOT

TRACKS = {'考研公共政治', '701 马克思主义基本原理', '870 中国化的马克思主义', '省考', '事业编'}
REGIONS = {'全国通用', '江苏', '上海', '浙江', '安徽'}

def load_profile():
    return json.loads((ROOT / 'profile.json').read_text(encoding='utf-8'))

def text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('模型返回的文本为空、类型不符或过长')
    return value.strip()

def canon(value):
    """Collapse whitespace for quote matching: models reflow paragraphs and newlines."""
    return re.sub(r'\s+', '', value)

def validate_result(result, articles, profile):
    if not isinstance(result, dict) or not isinstance(result.get('items'), list):
        raise ValueError('模型未返回 items 列表')
    sources = {a['id']: a for a in articles}
    topic_ids = {t['id'] for t in profile['topics']}
    seen = set()
    for item in result['items']:
        aid = item.get('id')
        if aid not in sources or aid in seen:
            raise ValueError('模型返回重复或不存在的新闻编号')
        seen.add(aid)
        score = item.get('importance')
        if isinstance(score, bool) or not isinstance(score, (float, int)) or not 0 <= score <= 100:
            raise ValueError('重要性分值不合法')
        if not isinstance(item.get('summary'), list) or not 1 <= len(item['summary']) <= 6:
            raise ValueError('缺少逐条事实摘要')
        for s in item['summary']:
            text(s, 1500)
        text(item.get('reason'), 1500)
        if not isinstance(item.get('study'), list) or not isinstance(item.get('speeches'), list):
            raise ValueError('缺少考点或讲话字段')
        for s in item['study']:
            if s.get('track') not in TRACKS or s.get('topic_id') not in topic_ids:
                raise ValueError('模型使用了未配置的科目或知识主题')
            if s.get('region') not in REGIONS or s.get('relevance') not in ('高', '中', '低'):
                raise ValueError('地域或关联程度不合法')
            if s['track'] not in ('省考', '事业编') and s['region'] != '全国通用':
                raise ValueError('考研内容不应使用地方考试标签')
            for k in ('reason', 'practice'):
                text(s.get(k), 1500)
        for q in item['speeches']:
            excerpt = text(q.get('excerpt'), 1000)
            speaker = text(q.get('speaker'), 50)
            if canon(excerpt) not in canon(sources[aid]['text']):
                raise ValueError('讲话摘录无法在原文中逐字匹配（忽略空白后仍不一致），拒绝生成报告')
            if speaker not in excerpt:
                raise ValueError('讲话摘录缺少说话者上下文：excerpt 必须逐字包含 speaker 的姓名；'
                                 '扩大摘录范围到含姓名的句子，或删除该条 speeches 后重新输出')
            if q.get('kind') not in ('报道转述', '报道中的直接引语'):
                raise ValueError('讲话类型不合法')
            if not isinstance(q.get('topic_ids'), list) or any(t not in topic_ids for t in q['topic_ids']):
                raise ValueError('讲话原理标签不合法')
            text(q.get('analysis'), 2500)
    if seen != set(sources):
        raise ValueError('模型遗漏新闻条目，拒绝发布不完整日报')
    return result['items']

def analyze(raw, config, secrets, log=print):
    if not all((config['model_base_url'], config['model_name'], secrets.get('model_api_key'))):
        raise RuntimeError('尚未配置模型 API：已保留文字稿，请在配置界面填写后重试')
    profile = load_profile()
    system = (ROOT / 'prompts/analysis.txt').read_text(encoding='utf-8')
    all_items = []
    # Keep long speeches intact; small batches avoid silently dropping the end of a broadcast.
    size = max(1, int(config.get('model_batch_size', 3)))
    for i in range(0, len(raw['articles']), size):
        batch = raw['articles'][i:i+size]
        log(f'AI 分析：{i+1} 至 {i+len(batch)} / {len(raw["articles"])} 条')
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(
            {'date': raw['date'], 'profile': profile, 'articles': batch}, ensure_ascii=False)}]
        for attempt in range(3):
            response = post_json(config['model_base_url'].rstrip('/') + '/chat/completions', {
                'model': config['model_name'], 'messages': messages, 'temperature': 0.2,
                'max_tokens': int(config['model_max_tokens'])
            }, {'Authorization': 'Bearer ' + secrets['model_api_key']}, timeout=240)
            try:
                choice = response['choices'][0]
                if choice.get('finish_reason') not in (None, 'stop'):
                    raise ValueError('模型输出被截断或未完成')
                content = choice['message']['content']
                content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content.strip())
                all_items.extend(validate_result(json.loads(content), batch, profile))
                break
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                if attempt == 2:
                    raise RuntimeError('AI 输出未通过结构/引文校验，请重试或更换模型') from None
                log('AI 输出未通过校验（' + str(e)[:80] + '），要求重新生成这一组（会再次调用模型）')
                messages.append({'role': 'user', 'content': '上次输出未通过校验，请重做本组，严格遵守JSON和引用规则。错误：' + str(e)[:120]})
    validate_result({'items': all_items}, raw['articles'], profile)
    return {'date': raw['date'], 'mode': 'ai', 'model': config['model_name'],
            'profile': profile, 'items': sorted(all_items, key=lambda x: -x['importance'])}

def reading_copy(raw):
    """Explicitly NOT an AI analysis; useful before credentials are available."""
    return {'date': raw['date'], 'mode': 'reading', 'model': None, 'profile': load_profile(),
            'items': [{'id': a['id'], 'summary': [a['text'][:420] + ('…' if len(a['text']) > 420 else '')],
                       'importance': 0, 'reason': '未进行AI评分，按央视播出顺序排列。', 'study': [], 'speeches': []}
                      for a in raw['articles']]}

def speech_candidates(raw):
    """Archive exact source paragraphs, distinct from verified direct quotations."""
    pattern = re.compile(r'(习近平|李强|赵乐际|王沪宁|蔡奇|丁薛祥|李希|韩正).{0,30}(指出|强调|表示|讲话|致信|贺信|要求)')
    return [{'id': a['id'], 'excerpt': p, 'source_url': a['url']}
            for a in raw['articles'] for p in a['text'].splitlines() if pattern.search(p)]
