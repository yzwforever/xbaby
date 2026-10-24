import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from newsstudy import cctv, analysis, pipeline
from newsstudy.settings import DEFAULT, TZ, now, protect, validate_config, atomic_json
from newsstudy.state import State

TEST_TMP = Path(__file__).resolve().parent.parent / 'tmp' / 'tests'
TEST_TMP.mkdir(parents=True, exist_ok=True)

def sandbox_temp():
    tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
    assert Path(tmp.name).resolve().parent == TEST_TMP.resolve()
    return tmp

DAY = '2026-10-22'

def raw_news():
    return {'date': DAY, 'video_url': 'https://tv.cctv.com/2026/10/22/VIDEfull.shtml',
            'source_url': 'https://tv.cctv.com/lm/xwlb/day/20261022.shtml',
            'fetched_at': '2026-10-22T21:30:00+08:00', 'completeness': '测试数据',
            'video_total': 1801, 'timestamp_note': '视频位置按当日分条时长累计估算。',
            'articles': [{'id':'N01','broadcast_order':1,'title':'测试条目',
                          'url':'https://tv.cctv.com/2026/10/22/VIDEtest.shtml',
                          'duration':95,'start_offset':75,
                          'text':'习近平指出，要坚持人民至上。这是用于校验的模拟文字，不是真实报道。'}]}

def result():
    return {'date': DAY, 'mode':'ai','model':'fake-model','profile':analysis.load_profile(), 'items':[
        {'id':'N01','summary':['模拟摘要'], 'importance':80, 'reason':'模拟排序理由', 'study':[],
         'speeches':[{'speaker':'习近平','kind':'报道转述','excerpt':'习近平指出，要坚持人民至上。',
                      'topic_ids':['T05'],'analysis':'模拟分析'}]}]}

class ParsingTests(unittest.TestCase):
    def day_html(self):
        def a(url,title): return f'<a href="{url}" title="{title}"><img src="x"/></a>'
        full='https://tv.cctv.com/2026/10/22/VIDEfull.shtml'
        text = a(full, '《新闻联播》 20261022 19:00')
        for i in range(3):
            text += a(f'https://tv.cctv.com/2026/10/22/VIDE{i}.shtml',f'[视频]新闻{i}')*2
        return text
    def test_day_filters_and_deduplicates(self):
        html = self.day_html()+'<a href="https://evil.test/2026/10/22/a.shtml" title="坏链接"></a>'
        full, articles, total = cctv.parse_day(html, DAY)
        self.assertEqual(len(articles),3)
        self.assertIn('VIDEfull',full)
        self.assertEqual(articles[0]['title'],'新闻0')
        self.assertIsNone(total)
        self.assertTrue(all(a['duration'] is None for a in articles))
    def day_html_timed(self):
        def item(url, title, dur):
            return (f'<li><div class="image"><a href="{url}" title="{title}"></a>'
                    f'<span>{dur}</span></div>'
                    f'<a href="{url}" title="{title}">label</a></li>')
        full='https://tv.cctv.com/2026/10/22/VIDEfull.shtml'
        html = item(full, '《新闻联播》 20261022 19:00', '00:30:01')
        html += item('https://tv.cctv.com/2026/10/22/VIDE0.shtml', '[视频]新闻0', '00:01:00')
        html += item('https://tv.cctv.com/2026/10/22/VIDE1.shtml', '[视频]新闻1', '00:00:30')
        html += item('https://tv.cctv.com/2026/10/22/VIDE2.shtml', '[视频]新闻2', '00:00:20')
        return html
    def test_durations_parsed_and_offsets_accumulate(self):
        full, links, total = cctv.parse_day(self.day_html_timed(), DAY)
        self.assertEqual(total, 1801)
        self.assertEqual([l['duration'] for l in links], [60, 30, 20])
        article='<div id="content_area">'+'正文足够长。'*15+'</div>'
        with patch('newsstudy.cctv.get_text',
                   side_effect=[self.day_html_timed(), article, article, article, self.day_html_timed()]):
            raw = cctv.fetch_day(DAY, lambda s:None, pause=0)
        self.assertEqual([a['start_offset'] for a in raw['articles']], [0, 60, 90])
        self.assertIn('视频位置', raw['timestamp_note'])
    def test_wrong_date_not_published(self):
        with self.assertRaises(cctv.NotReady): cctv.parse_day(self.day_html(),'2026-10-21')
    def test_all_text_preserved_and_script_ignored(self):
        content='习近平指出，要坚持人民至上，扎实推进各项工作。'
        out=cctv.parse_article('<div id="content_area"><p>'+content+'</p><p>第二段&amp;来源。</p><script>evil()</script></div>')
        self.assertIn(content,out); self.assertIn('第二段&来源',out); self.assertNotIn('evil',out)
    def test_missing_fulltext_does_not_use_description(self):
        with self.assertRaises(cctv.NotReady): cctv.parse_article('<meta name="description" content="只有摘要">')
    def test_index_changes_fail_closed(self):
        original=self.day_html()
        changed=original+'<a href="https://tv.cctv.com/2026/10/22/VIDEnew.shtml" title="[视频]新条目"></a>'
        article='<div id="content_area">'+'正文足够长。'*15+'</div>'
        with patch('newsstudy.cctv.get_text',side_effect=[original,article,article,article,changed]):
            with self.assertRaises(cctv.NotReady): cctv.fetch_day(DAY,lambda s:None,pause=0)

class ValidationTests(unittest.TestCase):
    def test_valid_quote(self):
        analysis.validate_result(result(),raw_news()['articles'],analysis.load_profile())
    def test_quote_whitespace_difference_still_matches(self):
        r=result(); r['items'][0]['speeches'][0]['excerpt']='习近平指出， 要坚持人民至上。'
        analysis.validate_result(r,raw_news()['articles'],analysis.load_profile())
    def test_hallucinated_quote_rejected(self):
        r=result(); r['items'][0]['speeches'][0]['excerpt']='习近平指出，必定命中考试。'
        with self.assertRaises(ValueError): analysis.validate_result(r,raw_news()['articles'],analysis.load_profile())
    def test_duplicate_missing_and_unknown_articles(self):
        for items in ([],result()['items']*2,[{**result()['items'][0],'id':'N99'}]):
            with self.assertRaises(ValueError): analysis.validate_result({'items':items},raw_news()['articles'],analysis.load_profile())
    def test_unsupported_topic(self):
        r=result(); r['items'][0]['speeches'][0]['topic_ids']=['unverified']
        with self.assertRaises(ValueError): analysis.validate_result(r,raw_news()['articles'],analysis.load_profile())
    def test_quote_speaker_must_be_in_context(self):
        r=result(); r['items'][0]['speeches'][0]['speaker']='李强'
        with self.assertRaises(ValueError): analysis.validate_result(r,raw_news()['articles'],analysis.load_profile())
    def test_reading_is_explicit_not_ai(self):
        r=analysis.reading_copy(raw_news())
        self.assertEqual(r['mode'],'reading'); self.assertFalse(r['items'][0]['study'])
    def test_config_rejects_credential_url(self):
        for url in ('http://api.test/v1','https://secret@api.test/v1','https://api.test/v1?key=secret'):
            with self.assertRaises(ValueError): validate_config({**DEFAULT,'model_base_url':url})
    def test_push_requires_upload(self):
        with self.assertRaises(ValueError): validate_config({**DEFAULT,'push_enabled':True})
    def test_repo_host_requires_clean_base_url(self):
        with self.assertRaises(ValueError):
            validate_config({**DEFAULT,'upload_enabled':True,'pdf_host':'repo'})
        validate_config({**DEFAULT,'upload_enabled':True,'pdf_host':'repo',
                         'repo_pdf_base_url':'https://github.com/u/r/blob/HEAD'})

class SchedulingTests(unittest.TestCase):
    def test_before_and_after_time(self):
        c={**DEFAULT,'start_date':'2026-10-21'}
        self.assertEqual(pipeline.due_dates(c,datetime(2026,10,23,21,29,tzinfo=TZ)),['2026-10-21','2026-10-22'])
        self.assertEqual(pipeline.due_dates(c,datetime(2026,10,23,21,30,tzinfo=TZ))[-1],'2026-10-23')
    def test_midnight_backfill(self):
        c={**DEFAULT,'start_date':'2026-10-22'}
        self.assertEqual(pipeline.due_dates(c,datetime(2026,10,23,0,10,tzinfo=TZ)),['2026-10-22'])
    def test_start_in_future(self):
        c={**DEFAULT,'start_date':'2027-01-01'}
        self.assertEqual(pipeline.due_dates(c,datetime(2026,10,23,23,tzinfo=TZ)),[])
    def test_lock_blocks_parallel_run(self):
        with sandbox_temp() as d:
            with pipeline.single_instance(Path(d)):
                with self.assertRaises(RuntimeError):
                    with pipeline.single_instance(Path(d)): pass

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=sandbox_temp(); self.root=Path(self.temp.name)
        self.config={**DEFAULT,'start_date':DAY,'model_base_url':'https://fake.test/v1','model_name':'fake',
                     'upload_enabled':True,'push_enabled':True}
        atomic_json(self.root/'config.json',self.config)
        self.secrets={'model_api_key':'fake','serverchan_sendkey':'SCTfake'}
    def tearDown(self): self.temp.cleanup()
    def fake_render(self,raw,r,dest,font):
        dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(b'%PDF-test'); return dest
    def test_upload_failure_resumes_without_reanalysis(self):
        with patch('newsstudy.pipeline.load_secrets',return_value=self.secrets), \
             patch('newsstudy.pipeline.collect',return_value=raw_news()), \
             patch('newsstudy.analysis.analyze',return_value=result()) as model, \
             patch('newsstudy.report.render',side_effect=self.fake_render), \
             patch('newsstudy.delivery.upload_pdf',side_effect=[RuntimeError('上传失败'),'https://fake.test/report.pdf']), \
             patch('newsstudy.delivery.send_wechat',return_value=True) as send:
            self.assertFalse(pipeline.run(self.root,DAY,lambda s:None))
            self.assertTrue(pipeline.run(self.root,DAY,lambda s:None))
            pipeline.run(self.root,DAY,lambda s:None)
            self.assertEqual(model.call_count,1); self.assertEqual(send.call_count,1)
            state=State(self.root)
            self.assertEqual(state.get(DAY)['notified'],1); state.close()
    def test_uncertain_push_never_automatically_retried(self):
        with patch('newsstudy.pipeline.load_secrets',return_value=self.secrets), \
             patch('newsstudy.pipeline.collect',return_value=raw_news()), \
             patch('newsstudy.analysis.analyze',return_value=result()), \
             patch('newsstudy.report.render',side_effect=self.fake_render), \
             patch('newsstudy.delivery.upload_pdf',return_value='https://fake.test/report.pdf'), \
             patch('newsstudy.delivery.send_wechat',side_effect=TimeoutError) as send:
            pipeline.run(self.root,DAY,lambda s:None); pipeline.run(self.root,DAY,lambda s:None)
            self.assertEqual(send.call_count,1)
            state=State(self.root); self.assertEqual(state.get(DAY)['notified'],2); state.close()
            pipeline.resolve_notifications(self.root,False)
            pipeline.run(self.root,DAY,lambda s:None)
            self.assertEqual(send.call_count,2)
    def test_missing_api_does_not_fetch_or_mark_done(self):
        with patch('newsstudy.pipeline.load_secrets',return_value={}), patch('newsstudy.pipeline.collect') as fetch:
            self.assertFalse(pipeline.run(self.root,DAY,lambda s:None)); fetch.assert_not_called()
    def test_missing_source_retries_and_notifies_nothing(self):
        with patch('newsstudy.pipeline.load_secrets',return_value=self.secrets), \
             patch('newsstudy.pipeline.collect',side_effect=cctv.NotReady('尚未齐全')), \
             patch('newsstudy.delivery.send_wechat') as send:
            self.assertFalse(pipeline.run(self.root,DAY,lambda s:None)); send.assert_not_called()
            state=State(self.root); self.assertEqual(state.get(DAY)['status'],'pending'); state.close()
    def test_repo_hosting_permanent_link_and_push(self):
        self.config.update({'pdf_host':'repo','repo_pdf_base_url':'https://github.com/u/r/blob/HEAD'})
        atomic_json(self.root/'config.json',self.config)
        sent=[]
        def capture(title, body, sendkey):
            sent.append(body); return True
        with patch('newsstudy.pipeline.load_secrets',return_value=self.secrets), \
             patch('newsstudy.pipeline.collect',return_value=raw_news()), \
             patch('newsstudy.analysis.analyze',return_value=result()), \
             patch('newsstudy.report.render',side_effect=self.fake_render), \
             patch('newsstudy.delivery.upload_pdf') as cos_upload, \
             patch('newsstudy.delivery.send_wechat',side_effect=capture) as send:
            self.assertTrue(pipeline.run(self.root,DAY,lambda s:None))
            cos_upload.assert_not_called()
            self.assertEqual(send.call_count,1)
            self.assertIn('视频约 01:15 起',sent[0])
            self.assertIn('长期提供',sent[0])
            self.assertTrue((self.root/'public/pdf/2026-10-22.pdf').exists())
            state=State(self.root); row=state.get(DAY); state.close()
            self.assertEqual(row['url'],'https://github.com/u/r/blob/HEAD/public/pdf/2026-10-22.pdf')
            self.assertEqual(row['expires'],'')
            self.assertEqual(row['notified'],1)
            with patch('newsstudy.delivery.publish_repo_pdf') as republish:
                pipeline.run(self.root,DAY,lambda s:None)
                republish.assert_not_called()
            self.assertEqual(send.call_count,1)

if __name__ == '__main__': unittest.main()
