import hashlib
import datetime as dt
import io
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from database import Session
from exporter import export_chats, parse_message
from fun_stats import chat_statistics, moments_statistics
from media import decode_dat, V2, wxgf_preview, valid_image, key_signature
from moments import export_moments, load_moments
from snapshot import snapshot, committed_wal, wal_checksum
from time_filter import parse_time_range, quick_range


def moment_xml(timestamp, body='', content=''):
    return (f"<SnsDataItem><TimelineObject><username>alice</username><createTime>{timestamp}</createTime>"
            f"<contentDesc><![CDATA[{body}]]></contentDesc><ContentObject>{content}</ContentObject>"
            "</TimelineObject></SnsDataItem>")


def moment_database(path, rows):
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE SnsTimeLine(tid INTEGER PRIMARY KEY, user_name TEXT, content TEXT)')
        con.executemany("INSERT INTO SnsTimeLine VALUES (?, 'alice', ?)", rows)
    con.close()


def moment_with_comments(timestamp, body, comments):
    return moment_xml(timestamp, body).replace(
        '</SnsDataItem>', f'<comment_user_list>{comments}</comment_user_list></SnsDataItem>')


def comment_xml(id32, username, content, stamp, extra=''):
    return (f'<user_comment><comment_id>{id32}</comment_id><username>{username}</username>'
            f'<create_time>{stamp}</create_time><content><![CDATA[{content}]]></content>'
            f'{extra}</user_comment>')


def check_desktop(window):
    import time
    from unittest.mock import patch
    chat, moments = window.pages['chat'], window.pages['moments']
    window.geometry('660x370')
    window.update()

    def check_footer(page):
        for button in (page.export_button, page.cancel_button, page.open_button):
            assert button.winfo_ismapped()
            assert button.winfo_width() >= button.winfo_reqwidth()
            assert button.winfo_rootx() + button.winfo_width() <= window.winfo_rootx() + window.winfo_width()
            assert button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
        assert page.body.canvas.winfo_height() > 0

    check_footer(chat)
    chat.chats = {'a':{'id':'a','name':'测试会话','username':'test','count':302,'last':1700000000}}
    chat.render_results()
    chat.select_visible()
    chat.search.set('不存在')
    chat.render_results()
    assert not chat.tree.get_children() and chat.selected == {'a'}
    for preset, button in chat.time_buttons.items():
        button.invoke()
        assert (chat.start.get(), chat.end.get()) == quick_range(preset)
    chat.start.set('2026-11-16')
    y = chat.export_button.winfo_rooty()
    chat.search_entry.event_generate('<MouseWheel>', delta=-120)
    window.update()
    assert chat.body.canvas.yview()[0] > 0 and chat.export_button.winfo_rooty() == y
    chat.body.canvas.yview_moveto(1)
    window.update()
    assert chat.output_entry.winfo_rooty() + chat.output_entry.winfo_height() \
        <= chat.body.canvas.winfo_rooty() + chat.body.canvas.winfo_height()

    window.nav['moments'].invoke()
    window.update()
    check_footer(moments)
    assert not chat.winfo_ismapped() and moments.start.get() == ''
    with tempfile.TemporaryDirectory() as directory:
        session = Session(scratch=directory)
        try:
            moment_database(session.cache/'sns.db', [(1, moment_with_comments(
                1700000000, '按钮验收', comment_xml(1, 'alice', '评论验收', 1700000001))),
                                                     (2, moment_xml(1700000001, '另一个动态'))])
            session.moment_count = 2
            session.moment_status = '本机朋友圈 2 条（筛选日期前）'
            moments.set_session((session, load_moments(session), ''))
            moments.search.set('验收')
            moments.render_results()
            assert len(moments.tree.get_children()) == 1
            assert moments.export_button.instate(['!disabled'])
            moments.root_dir.set(directory)
            assert moments.export_button.instate(['disabled'])
            moments.root_dir.set('')
            moments.output_dir.set(directory)
            moments.pictures.set(False)
            assert not moments.include_comments.get()
            moments.include_comments.set(True)
            moments.export_button.invoke()
            deadline = time.monotonic() + 5
            while window.busy and time.monotonic() < deadline:
                window.update()
                time.sleep(0.02)
            assert not window.busy
            assert '1 条朋友圈' in moments.status.get()
            assert '评论 1 条' in moments.status.get()
            assert (moments.last_output/'朋友圈.html').is_file()
            window.state('zoomed')
            window.update()
            check_footer(moments)
            window.nav['chat'].invoke()
            window.update()
            check_footer(chat)
            assert chat.start.get() == '2026-11-16' and chat.search.get() == '不存在'
        finally:
            session.close()
            moments.session = None

    class StatsTestSession(Session):
        def connect(self, root=None, *, scope='all'):
            assert scope == 'moments'
            moment_database(self.cache/'sns.db', [(1, moment_xml(1700000000, '统计测试'))])
            self.moment_count = 1

    window.state('normal')
    window.geometry('660x370')
    window.nav['stats'].invoke()
    window.update()
    stats = window.pages['stats']
    assert stats.run_button.winfo_ismapped() and stats.cancel_button.winfo_ismapped()
    assert stats.run_button.winfo_rooty() + stats.run_button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    stats.scope.set('只统计朋友圈')
    stats.refresh_buttons()
    assert stats.words_toggle.instate(['disabled'])
    with patch('desktop_ui.Session', StatsTestSession):
        stats.run_button.invoke()
        deadline = time.monotonic() + 5
        while window.busy and time.monotonic() < deadline:
            window.update()
            time.sleep(0.02)
    assert not window.busy
    assert stats.report['moments']['rank'] == [('alice', 1)]
    assert stats.body.canvas.yview()[0] > 0


class ExportTests(unittest.TestCase):
    def test_comment_export_opt_in_replies_and_notifications(self):
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory, closing(Session(scratch=directory)) as session:
            session.contacts = {'alice': {'name': '甲'}, 'bob': {'name': '乙'}}
            stamp = 1700000000
            comment_list = ''.join((
                comment_xml(10, 'alice', '一楼 <script>', stamp + 20),
                comment_xml(11, 'bob', '回复一楼', stamp + 30,
                            '<ref_comment_id>10</ref_comment_id><ref_username>alice</ref_username>'),
                comment_xml(12, 'alice', '', stamp + 40,
                            '<ref_comment_id>98</ref_comment_id><ref_username>bob</ref_username>'
                            '<comment_imageinfo_count>1</comment_imageinfo_count>'),
                comment_xml(14, 'bob', '已删除内容', stamp + 50, '<b_deleted>1</b_deleted>'),
                comment_xml(0, 'bob', '64 位 ID', stamp + 60,
                            '<comment_64id>18446744073709551610</comment_64id>'
                            '<ref_comment_id>11</ref_comment_id><ref_username>bob</ref_username>'),
            ))
            db = session.cache/'sns.db'
            moment_database(db, [
                (1, moment_with_comments(stamp, '要导出的动态', comment_list)),
                (2, moment_with_comments(stamp + 1, '另一条', comment_xml(10, 'bob', '不可混入', stamp))),
            ])
            with closing(sqlite3.connect(db)) as con, con:
                con.execute('CREATE TABLE SnsMessage_tmp3 (feed_id,type,from_username,from_nickname,'
                            'content,create_time,local_id,comment_id,del_status,serialized_ref_buf)')
                con.executemany('INSERT INTO SnsMessage_tmp3 VALUES (?,?,?,?,?,?,?,?,?,?)', [
                    (1, 2, 'bob', '冗余', '不得优先于时间线', stamp + 30, 8, 11, 0, b''),
                    (1, 2, 'bob', '', '通知补录', stamp + 70, 9, 13, 0, b'\x0a\x05alice'),
                    (1, 1, 'bob', '', '点赞', stamp + 70, 10, 15, 0, b''),
                    (1, 2, 'bob', '', '已删通知', stamp + 70, 11, 14, 1, b''),
                ])
            root = Path(directory)
            off, summary = export_moments(session, root, start=stamp, end=stamp + 1, include_images=False)
            off_post = json.loads((off/'朋友圈.json').read_text('utf8'))[0]
            self.assertNotIn('comments', off_post)
            self.assertEqual(summary['comments'], 0)
            self.assertNotIn('回复一楼', (off/'朋友圈.html').read_text('utf8'))
            out, summary = export_moments(session, root, start=stamp, end=stamp + 1,
                                          include_images=False, include_comments=True)
            records = json.loads((out/'朋友圈.json').read_text('utf8'))
            comments = records[0]['comments']
            self.assertEqual(summary['comments'], 5)
            self.assertEqual([row['id'] for row in comments], ['10', '11', '12', '18446744073709551610', '13'])
            self.assertEqual(comments[1]['reply_to']['parent_id'], '10')
            self.assertEqual(comments[1]['reply_to']['author'], '甲')
            self.assertIsNone(comments[2]['reply_to']['parent_id'])
            self.assertEqual(comments[2]['reply_to']['comment_id'], '98')
            self.assertEqual(comments[2]['image_count'], 1)
            self.assertEqual(comments[3]['reply_to']['parent_id'], '11')
            self.assertEqual(comments[4]['source'], 'notification')
            self.assertEqual(comments[4]['reply_to']['username'], 'alice')
            self.assertIsNone(comments[4]['reply_to']['parent_id'])
            self.assertEqual(dt.datetime.fromisoformat(comments[4]['time']).timestamp(), stamp + 70)
            html_view = (out/'朋友圈.html').read_text('utf8')
            self.assertIn('href="#m0-c0"', html_view)
            self.assertIn('&lt;script&gt;', html_view)
            self.assertIn('原评论缺失', html_view)
            self.assertNotIn('已删除内容', html_view)
            self.assertNotIn('不可混入', html_view)
            self.assertIn('回复 甲（评论 10）', (out/'朋友圈.txt').read_text('utf8'))

    def test_chat_statistics_ranges_ownership_and_rankings(self):
        from contextlib import closing
        from zstandard import ZstdCompressor
        t1 = int(dt.datetime(2024, 12, 31, 23, 45).timestamp())
        t2 = int(dt.datetime(2025, 1, 1, 0, 15).timestamp())
        edge = t2 + 3600
        packed = ZstdCompressor().compress('望远镜 hello [呲牙]'.encode())
        source = {
            'bob': [(1, 1, t1, 'hello hello [呲牙]', None), (1, 2, t1, 'hello 蓝蓝[呲牙]', None),
                    ((1 << 32) | 1, 1, t2, None, packed), (10002, 1, t2, '跳过系统消息', None),
                    (1, 2, edge, '边界之外', None)],
            'carol': [(3, 3, t2, None, None), (34, 1, t2, None, None)],
            'group@chatroom': [(1, 1, t2, 'alice:\nhello 投入[裂开]', None)],
            'gh_feed': [(1, 4, t2, '服务', None)],
        }
        with tempfile.TemporaryDirectory() as directory, closing(Session(scratch=directory)) as session:
            session.account = Path(directory)/'alice_abcd'
            contacts = session.cache/'contact.db'
            with closing(sqlite3.connect(contacts)) as con, con:
                con.execute('CREATE TABLE contact(username,nick_name,remark,verify_flag)')
                con.executemany('INSERT INTO contact VALUES (?,?,?,?)',
                                [('alice','Alice','',0), ('bob','Bob','',0), ('carol','Carol','',0),
                                 ('group@chatroom','群','',0), ('gh_feed','官方','',56)])
            db = session.cache/'message_0.db'
            with closing(sqlite3.connect(db)) as con, con:
                con.execute('CREATE TABLE Name2Id(user_name)')
                con.executemany('INSERT INTO Name2Id VALUES (?)', [(x,) for x in ('alice','bob','carol','gh_feed')])
                for user, rows in source.items():
                    table = 'Msg_' + hashlib.md5(user.encode()).hexdigest()
                    con.execute(f'CREATE TABLE {table}(local_type INTEGER,real_sender_id INTEGER,create_time INTEGER,'
                                'message_content,compress_content)')
                    con.executemany(f'INSERT INTO {table} VALUES (?,?,?,?,?)', rows)
            session.shards.append(db)
            session.index()
            result = chat_statistics(session, start=t1, end=edge)
            self.assertEqual(result['messages'], 7)
            self.assertEqual(result['owner'], 'alice')
            self.assertEqual(result['own_text_messages'], 3)
            self.assertEqual(result['private_rank'], [('Bob', 3), ('Carol', 2)])
            self.assertEqual([(item['year'],item['leaders']) for item in result['annual']],
                             [(2025, ['Carol']), (2024, ['Bob'])])
            self.assertEqual(dict(result['chars'])['蓝'], 2)
            self.assertEqual(dict(result['words'])['hello'], 5)
            self.assertEqual(dict(result['own_words'])['hello'], 4)
            self.assertNotIn('alice', dict(result['words']))
            self.assertEqual(dict(result['own_emojis']), {'[呲牙]': 2, '[裂开]': 1})
            session.account = Path(directory)/'unidentified'
            totals_only = chat_statistics(session, start=t1, end=edge, include_words=False)
            self.assertEqual(totals_only['private_rank'], result['private_rank'])
            self.assertEqual(totals_only['owner'], '')
            self.assertEqual(totals_only['own_emojis'], [])

    def test_moments_statistics_author_counts(self):
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory, closing(Session(scratch=directory)) as session:
            session.contacts = {'alice': {'name': '甲'}, 'bob': {'name': '乙'}}
            moment_database(session.cache/'sns.db', [
                (1, moment_xml(1700000000)),
                (2, moment_xml(1700000001)),
                (3, moment_xml(1700000002).replace('alice', 'bob')),
                (4, moment_xml(1700000003).replace('alice', 'bob')),
                (5, moment_xml(0)),
            ])
            session.moment_count = 5
            result = moments_statistics(session, start=1700000000, end=1700000003)
            self.assertEqual(result['rank'], [('甲', 2), ('乙', 1)])
            self.assertEqual(result['count'], 3)
            self.assertEqual(result['unknown_time'], 1)

    def test_workspace_database_scopes(self):
        from contextlib import closing
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = root/'account'/'db_storage'
            for name in ('contact', 'sns', 'message'):
                (storage/name).mkdir(parents=True)
            moment_database(storage/'sns'/'sns.db', [(1, moment_xml(1700000000))])
            with closing(sqlite3.connect(storage/'contact'/'contact.db')) as con, con:
                con.execute('CREATE TABLE contact(username, nick_name, remark)')
                con.execute("INSERT INTO contact VALUES ('alice', 'Alice', '发布者')")
            table = 'Msg_' + hashlib.md5(b'alice').hexdigest()
            with closing(sqlite3.connect(storage/'message'/'message_0.db')) as con, con:
                con.execute(f'CREATE TABLE {table}(create_time)')
                con.execute(f'INSERT INTO {table} VALUES (1700000000)')

            def copy_plain(source, destination, material, check):
                return snapshot(source, destination, None, check)

            with (patch('database.native.MemoryKeys.candidates', side_effect=lambda: {(b'fixture', None)}),
                  patch('database.native._verify_enc_key', return_value=True) as verify,
                  patch('database.snapshot', side_effect=copy_plain) as copy):
                with closing(Session(scratch=root)) as chat:
                    self.assertEqual(chat.connect(storage.parent, scope='chat'), 1)
                    self.assertEqual({call.args[0].name for call in copy.call_args_list},
                                     {'contact.db', 'message_0.db'})
                    self.assertEqual(verify.call_count, 2)
                    self.assertFalse((chat.cache/'sns.db').exists())
                copy.reset_mock()
                verify.reset_mock()
                with closing(Session(scratch=root)) as moments:
                    self.assertEqual(moments.connect(storage.parent, scope='moments'), 1)
                    self.assertEqual({call.args[0].name for call in copy.call_args_list},
                                     {'contact.db', 'sns.db'})
                    self.assertEqual(verify.call_count, 2)
                    self.assertFalse(moments.shards)
                    self.assertEqual(load_moments(moments).posts[0]['author'], '发布者')

    def test_moments_keyword_reuses_timeline(self):
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory, closing(Session(scratch=directory)) as session:
            path = session.cache/'sns.db'
            moment_database(path, [
                (1, moment_xml(1700000000, '【TEST】')),
                (2, moment_xml(1700000001, content='<contentUrl>https://example.invalid/TeSt</contentUrl>')),
                (3, moment_xml(1700000002, '无关动态')),
            ])
            timeline = load_moments(session)
            path.unlink()
            self.assertEqual([post['id'] for post in timeline.search('  test  ')], ['2', '1'])
            output, stats = export_moments(session, directory, start=1700000001, include_images=False,
                                           timeline=timeline, keyword='TEST')
            self.assertEqual(stats['moments'], 1)
            post = json.loads((output/'朋友圈.json').read_text('utf8'))[0]
            self.assertEqual(post['id'], '2')

    def test_moments_export_formats_and_image_cache(self):
        from contextlib import closing
        from zstandard import ZstdCompressor
        with tempfile.TemporaryDirectory() as directory, closing(Session(scratch=directory)) as session:
            root = Path(directory)
            session.account = root / 'test_account'
            session.contacts = {'alice': {'name': '发布者'}}
            cache = session.account/'cache'/'2026-11'/'Sns'/'Img'/'ab'
            cache.mkdir(parents=True)
            picture = io.BytesIO()
            Image.new('RGB', (23, 31), 'red').save(picture, 'JPEG')
            raw = picture.getvalue()
            digest = hashlib.md5(raw).hexdigest()
            (cache/'local').write_bytes(raw + bytes(24))
            other = io.BytesIO()
            Image.new('RGB', (41, 27), 'blue').save(other, 'PNG')
            candidate = other.getvalue()
            (cache/'opaque').write_bytes(candidate)
            Image.new('RGB', (20, 29), 'green').save(cache/'unlinked', 'PNG')
            photo = (f'<media><type>2</type><url md5="{digest}"/><size width="23" height="31" '
                     f'totalSize="{len(raw)}"/></media>')
            tentative = (f'<media><type>2</type><url md5="{"a"*32}"/><size width="41" height="27" '
                         f'totalSize="{len(candidate)}"/></media>')
            missing = '<media><type>2</type><url md5="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"/></media>'
            video = '<media><type>6</type><url>https://example.invalid/movie</url></media>'
            content = (f'<contentUrl>https://example.invalid/?a=1&amp;b=2</contentUrl>'
                       f'<title>分享 &amp; 标题</title><description>链接摘要</description>'
                       f'<mediaList>{photo}{tentative}{missing}{video}</mediaList>')
            good = moment_xml(1700000000, '多行\n<script>x</script>', content)
            compression = ZstdCompressor().compress(good.encode())
            rows = [
                (9007199254740999, compression),
                (2, moment_xml(1700000001, content='<contentUrl>javascript:alert(1)</contentUrl>')),
                (3, '<broken'),
                (4, moment_xml(1700000002, '越界')),
            ]
            moment_database(session.cache/'sns.db', rows)
            output, stats = export_moments(session, root, 1700000000, 1700000002, include_cache=True)
            records = json.loads((output/'朋友圈.json').read_text('utf8'))
            self.assertEqual([post['id'] for post in records], ['2', '9007199254740999'])
            self.assertEqual(records[1]['author'], '发布者')
            self.assertEqual(records[1]['timestamp'], 1700000000)
            self.assertEqual(dt.datetime.fromisoformat(records[1]['time']).timestamp(), 1700000000)
            self.assertEqual(records[1]['text'], '多行\n<script>x</script>')
            self.assertEqual(records[1]['links'][0]['description'], '链接摘要')
            images = records[1]['images']
            self.assertEqual([i['code'] for i in images], ['verified', 'candidate', 'missing'])
            self.assertEqual((output/images[0]['file']).read_bytes(), raw)
            self.assertEqual((output/images[1]['file']).read_bytes(), candidate)
            markup = (output/'朋友圈.html').read_text('utf8')
            self.assertIn('href="https://example.invalid/?a=1&amp;b=2"', markup)
            self.assertIn('&lt;script&gt;', markup)
            self.assertNotIn('href="javascript:', markup)
            self.assertIn('<details><summary>链接封面', markup)
            plain = (output/'朋友圈.txt').read_text('utf8')
            self.assertIn('https://example.invalid/?a=1&b=2', plain)
            self.assertIn(images[0]['file'], plain)
            self.assertEqual(stats['without_time_excluded'], 1)
            self.assertEqual(stats['cached_images'], 3)
            gallery = (output/'本机图片缓存.html').read_text('utf8')
            self.assertEqual(gallery.count('<img '), 3)
            self.assertEqual(stats['state'], 'complete')

            output, stats = export_moments(session, root, include_images=False)
            self.assertEqual((stats['moments'], stats['parse_warnings']), (4, 1))
            self.assertFalse(list((output/'assets').iterdir()))
            self.assertFalse((output/'本机图片缓存.html').exists())

    def test_moments_reject_ambiguous_cache_candidate(self):
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory, closing(Session(scratch=directory)) as session:
            session.account = Path(directory)
            cache = session.account/'cache'/'2026-11'/'Sns'/'Img'
            cache.mkdir(parents=True)
            Image.new('RGB',(27,19),'green').save(cache/'item','PNG')
            size = (cache/'item').stat().st_size
            content = (f'<mediaList><media><type>2</type><url md5="{{}}"/>'
                       f'<size width="27" height="19" totalSize="{size}"/></media></mediaList>')
            moment_database(session.cache/'sns.db', [
                (1, moment_xml(1700000000, content=content.format('a'*32))),
                (2, moment_xml(1700000001, content=content.format('b'*32))),
            ])
            _, stats = export_moments(session, directory, end=1700000001)
            self.assertEqual((stats['moments'], stats['candidate_images'], stats['unavailable_images']), (1, 0, 1))

    def test_moments_cancellation_marks_output_incomplete(self):
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory, closing(Session(scratch=directory)) as session:
            moment_database(session.cache/'sns.db', [(1, moment_xml(1700000000))])
            session.cancel.set()
            with self.assertRaises(InterruptedError):
                export_moments(session, directory, include_images=False)
            report = next(Path(directory).glob('*.未完成/导出统计.json'))
            self.assertEqual(json.loads(report.read_text('utf8'))['reason'], 'cancelled')

    def test_time_ranges_and_shortcuts(self):
        stamp = lambda day, hour=0: dt.datetime(2026, 11, day, hour).timestamp()
        self.assertEqual(parse_time_range(' 2026-11-09-1 ', '2026-11-09-2'), (stamp(9,1),stamp(9,2)))
        self.assertEqual(parse_time_range('2026-11-09', '2026-11-09'), (stamp(9),stamp(10)))
        self.assertEqual(parse_time_range('', '2026-11-09-0'), (None,stamp(9)))
        self.assertEqual(parse_time_range('2026-11-09-23', ''), (stamp(9,23),None))
        self.assertEqual(parse_time_range('', ' '), (None,None))
        for start,end in [('2026-11-09-24',''),('2026-02-30',''),('2026-11-09--1',''),
                          ('2026-11-09-1','2026-11-09-1'),('2026-11-10','2026-11-09'),
                          ('2026-11-09-1:30',''),('','invalid')]:
            with self.subTest(start=start,end=end), self.assertRaises(ValueError):
                parse_time_range(start,end)
        today = dt.date(2026,1,1)
        self.assertEqual(quick_range('today',today), ('2026-01-01','2026-01-01'))
        self.assertEqual(quick_range('yesterday',today), ('2025-12-31','2025-12-31'))
        self.assertEqual(quick_range('week',today), ('2025-12-25','2026-01-01'))
        self.assertEqual(quick_range('all',today), ('',''))

    def test_hour_filter_export_boundaries(self):
        start,end = parse_time_range('2026-11-09-1','2026-11-09-2')
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(scratch=tmp)
            try:
                path = session.cache/'message_0.db'
                table = 'Msg_'+hashlib.md5(b'friend').hexdigest()
                con = sqlite3.connect(path)
                con.execute('CREATE TABLE Name2Id(user_name TEXT)')
                con.execute("INSERT INTO Name2Id VALUES ('alice')")
                con.execute('CREATE TABLE '+table+'(local_id INTEGER,local_type INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content TEXT,sort_seq INTEGER)')
                con.executemany('INSERT INTO '+table+' VALUES (?,?,?,?,?,?)',
                    [(i,1,1,int(t),'message '+str(i),i) for i,t in enumerate((start-1,start,end-1,end),1)])
                con.commit()
                con.close()
                session.shards.append(path)
                session.index()
                output,stats = export_chats(session,session.chats,tmp,start,end,include_images=False)
                records = json.loads(next(output.glob('*/*.json')).read_text('utf8'))
                self.assertEqual(stats['messages'],2)
                self.assertEqual([m['local_id'] for m in records],[2,3])
                full_start,full_end = parse_time_range('2026-11-09','2026-11-09')
                self.assertEqual(len(list(session.messages(session.chats[0],full_start,full_end))),4)
            finally:
                session.close()

    def test_wrapped_declaration_in_forwarded_card(self):
        from exporter import html_message
        count = 100
        items = ''.join(f'<dataitem datatype="1"><sourcename>甲</sourcename><sourcetime>08:30</sourcetime><datadesc>第{i}条</datadesc></dataitem>' for i in range(count))
        raw = ('<msg><?xml version="1.0"?>\n<appmsg><type>19</type><title>合并转发</title>'
               '<recorditem><![CDATA[<recordinfo><datalist count="100">'+items+'</datalist></recordinfo>]]></recorditem></appmsg></msg>')
        parsed = parse_message(raw, (19 << 32) | 49)
        self.assertEqual(parsed['kind'],'聊天记录')
        self.assertEqual([c['text'] for c in parsed['forwarded']],[f'第{i}条' for i in range(count)])
        page = html_message(dict(parsed,raw_content=raw,sender='乙',time='今天'))
        visible = page.split('<summary>原始消息信息')[0]
        self.assertIn('展开聊天记录（100 条）',visible)
        self.assertNotIn('&lt;appmsg',visible)
        self.assertNotIn('<recorditem',parse_message(raw[:-20],(19 << 32) | 49)['text'])

    def test_multishard_export_and_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(scratch=tmp)
            try:
                key = hashlib.md5(b'friend').hexdigest()
                table = 'Msg_' + key
                s.contacts = {'alice':{'nickname':'Alice'},'bob':{'nickname':'Bob'}}
                for index,user in enumerate(('alice','bob')):
                    path = s.cache / ('message_%d.db' % index)
                    con = sqlite3.connect(path)
                    con.execute('CREATE TABLE Name2Id(user_name TEXT)')
                    con.execute('INSERT INTO Name2Id VALUES (?)',(user,))
                    con.execute('CREATE TABLE '+table+'(local_id INTEGER,local_type INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content TEXT,sort_seq INTEGER)')
                    con.executemany('INSERT INTO '+table+' VALUES (?,?,?,?,?,?)',[(n,1,1,1700000000+n*2+index,'<script>alert(1)</script> & 文本',n) for n in range(151)])
                    con.commit(); con.close()
                    s.shards.append(path)
                s.index()
                s.contacts = {'alice':{'nickname':'Alice'},'bob':{'nickname':'Bob'}}
                messages = list(s.messages(s.chats[0]))
                self.assertEqual(len(messages),302)
                self.assertEqual([m['sender'] for m in messages[:4]],['Alice','Bob','Alice','Bob'])
                output,stats = export_chats(s,s.chats,tmp,include_images=False)
                self.assertEqual(stats['messages'],302)
                records = json.loads(next(output.rglob('聊天记录.json')).read_text('utf8'))
                self.assertEqual(len(records),302)
                page = next(output.rglob('聊天记录.html')).read_text('utf8')
                self.assertNotIn('<script>',page)
                self.assertIn('&lt;script&gt;',page)
                self.assertEqual(len(list(s.messages(s.chats[0],1700000010,1700000020))),10)
                s.cancel.set()
                with self.assertRaises(InterruptedError):
                    export_chats(s,s.chats,tmp,include_images=False)
                self.assertTrue(list(Path(tmp).glob('*.未完成')))
            finally:
                s.close()

    def test_special_messages(self):
        r=parse_message('<msg><appmsg><type>5</type><title>Search</title><url>https://www.google.com.hk</url></appmsg></msg>',49)
        self.assertEqual(r['kind'],'链接')
        self.assertIn('https://www.google.com.hk',r['text'])
        self.assertEqual(parse_message('<msg><emoji desc="OK" /></msg>',47)['text'],'[动画表情]OK')
        self.assertIn('恭喜发财',parse_message('<msg><appmsg><type>2001</type><wcpayinfo><receivertitle>恭喜发财</receivertitle></wcpayinfo></appmsg></msg>',49)['text'])
        self.assertIn('broken',parse_message('broken',49)['text'])

    def test_nested_forwarded_export(self):
        from html import escape
        from html.parser import HTMLParser
        from types import SimpleNamespace
        from xml.etree import ElementTree as ET

        digest = 'e'*32
        leaf = ('<dataitem datatype="1"><sourcename>原作者</sourcename>'
                '<sourcetime>09:41</sourcetime><datadesc>深层正文 &lt;script&gt;</datadesc></dataitem>'
                '<dataitem datatype="2"><fullmd5>'+digest+'</fullmd5></dataitem>')
        def record(items):
            return '<recordinfo><datalist>'+items+'</datalist></recordinfo>'
        def nested(items):
            return ('<dataitem datatype="17"><datatitle>子记录</datatitle><recordxml>'
                    +items+'</recordxml></dataitem>')
        damaged = '<dataitem datatype="17"><datadesc>仅有摘要</datadesc><recordxml>broken</recordxml></dataitem>'
        tail = '<dataitem datatype="1"><datadesc>同层后一条</datadesc></dataitem>'
        payload = record(nested(record(nested(escape(record(leaf)))) )+damaged+tail)
        card = '<msg><appmsg><type>19</type><title>外层</title><recorditem><![CDATA['+payload+']]></recorditem></appmsg></msg>'
        class Page(HTMLParser):
            def __init__(self):
                super().__init__()
                self.depth = 0
                self.leaves = []
            def handle_starttag(self, tag, attrs):
                if tag == 'details':
                    self.depth += 1
            def handle_endtag(self, tag):
                if tag == 'details':
                    self.depth -= 1
            def handle_data(self, data):
                if data.startswith('深层正文'):
                    self.leaves.append((self.depth,data))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root/'msg'/'attach'/'demo'/'Rec'
            cache.mkdir(parents=True)
            Image.new('RGB',(9,12),'blue').save(cache/(digest+'.png'))
            session = SimpleNamespace(account=root,image_key=None,check=lambda:None,
                        messages=lambda *_:iter([{'sender':'转发人','time':'今天','type_code':49,'raw_content':card}]))
            out,stats = export_chats(session,[{'id':'demo','name':'测试'}],root)
            self.assertEqual(stats['messages'],1)
            folder = next(out.glob('*/聊天记录.json')).parent
            data = json.loads((folder/'聊天记录.json').read_text('utf8'))[0]['forwarded']
            self.assertEqual(len(data),3)
            self.assertIn('仅有摘要',data[1]['text'])
            self.assertEqual(data[-1]['text'],'同层后一条')
            a,b = data[0]['forwarded'][0]['forwarded']
            self.assertEqual((a['sender'],a['time'],a['text']),('原作者','09:41','深层正文 <script>'))
            self.assertTrue((folder/b['media']['file']).is_file())
            page = Page()
            markup = (folder/'聊天记录.html').read_text('utf8')
            page.feed(markup)
            self.assertEqual(page.leaves,[(3,'深层正文 <script>')])
            self.assertEqual(page.depth,0)
            self.assertIn('src="'+b['media']['file']+'"',markup)
            self.assertIn('\n      原作者 09:41\n      深层正文 <script>',(folder/'聊天记录.txt').read_text('utf8'))
            direct = ET.fromstring(card)
            node = direct.find('.//recorditem')
            node.text = ''
            node.append(ET.fromstring(payload))
            self.assertEqual(len(parse_message(ET.tostring(direct,encoding='unicode'),49)['forwarded']),3)

    def test_media_fallback_and_continuation(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(scratch=tmp)
            self.addCleanup(session.close)
            session.account = Path(tmp)
            chat = {'id':'test','name':'测试'}
            cache = session.account/'msg'/'attach'/'test'/'2025-09'/'Img'
            cache.mkdir(parents=True)
            digest = 'a'*32
            thumbnail = cache/(digest+'_t.dat')
            original = cache/(digest+'_h.dat')
            buf = io.BytesIO()
            Image.new('RGB',(12,16),'blue').save(buf,'PNG')
            row = {'sender':'甲','timestamp':100,'time':'时间','shard':'demo'}
            rows = [dict(row,local_id=1,type_code=1,raw_content='之前'),
                    dict(row,local_id=2,type_code=3,raw_content='<msg><img md5="'+digest+'"/></msg>'),
                    dict(row,local_id=3,type_code=1,raw_content='之后')]
            session.messages = lambda *_:(dict(r) for r in rows)
            for case in ('thumbnail','missing','decode_failed'):
                with self.subTest(case=case):
                    if case == 'thumbnail':
                        original.write_bytes(V2+b'x'*40)
                        thumbnail.write_bytes(buf.getvalue())
                    elif case == 'missing':
                        original.unlink()
                        thumbnail.unlink()
                    else:
                        original.write_bytes(b'broken')
                    with patch('media.find_image_key') as scan:
                        output,stats = export_chats(session,[chat],tmp)
                    scan.assert_not_called()
                    folder = next(output.rglob('聊天记录.json')).parent
                    records = json.loads((folder/'聊天记录.json').read_text('utf8'))
                    page = (folder/'聊天记录.html').read_text('utf8')
                    self.assertEqual(stats['messages'],3)
                    self.assertEqual(records[-1]['text'],'之后')
                    self.assertIn('之后',page)
                    self.assertIn('之后',(folder/'聊天记录.txt').read_text('utf8'))
                    media = records[1]['media']
                    self.assertEqual(media['code'],case)
                    if case == 'thumbnail':
                        self.assertIn(media['file'],page)
                        self.assertEqual((folder/media['file']).read_bytes(),buf.getvalue())
                    else:
                        self.assertEqual(stats['unavailable_images'],1)
                        self.assertIn(media['status'],page)
            with patch('media.decode_dat',side_effect=InterruptedError('已取消')):
                with self.assertRaises(InterruptedError):
                    export_chats(session,[chat],tmp)
            partial = next(Path(tmp).glob('*.未完成'))
            stats = json.loads((partial/'导出统计.json').read_text('utf8'))
            kept = json.loads(next(partial.rglob('聊天记录.json')).read_text('utf8'))
            self.assertEqual(stats['reason'],'cancelled')
            self.assertEqual(stats['position']['local_id'],2)
            self.assertEqual(stats['unavailable_images'],0)
            self.assertEqual([r['text'] for r in kept],['之前'])

    def test_migrated_account_image_key(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from media import LocalImages
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = root/'migrated'/'example_account_a2b3'
            folder = account/'msg'/'attach'/'chat'/'2026-11'/'Img'
            folder.mkdir(parents=True)
            kv = root/'Tencent'/'xwechat'/'net_2'/'kvcomm'
            kv.mkdir(parents=True)
            (kv/'key_1234567_1_ready.statistic').touch()
            (kv/'key_8642_1_input.statistic').touch()
            session = SimpleNamespace(account=account, image_key=None, check=lambda:None, progress=lambda _:None)
            chat = {'id':'chat'}
            buf = io.BytesIO()
            Image.new('RGB',(9,16),'green').save(buf,'PNG')
            picture = buf.getvalue()
            name = 'b'*32
            message = {'raw_content':'', '_packed':name.encode()}
            with patch.dict('os.environ',APPDATA=str(root)), patch('media.find_image_key') as scan:
                images = LocalImages(session,[chat])
                for number in (1234567,8642):
                    key = hashlib.md5(f'{number}example_account'.encode()).hexdigest()[:16].encode()
                    enc = Cipher(algorithms.AES(key),modes.ECB()).encryptor()
                    payload = enc.update(picture[:32]+bytes([16])*16)+enc.finalize()
                    data = V2+struct.pack('<II',32,8)+bytes(1)+payload+picture[32:-8]+bytes(x^(number&255) for x in picture[-8:])
                    images._ensure_key(data)
                    self.assertEqual((images.key,images.xor),(key,number&255))
                (folder/(name+'_t.dat')).write_bytes(data)
                assets = root/'assets'
                assets.mkdir()
                result = LocalImages(session,[chat]).attach(chat,message,assets)
                self.assertEqual(result['code'],'thumbnail')
                self.assertEqual((root/result['file']).read_bytes(),picture)
                scan.assert_not_called()

    def test_images(self):
        self.assertFalse(key_signature(b'BM'+bytes(14)))
        for fmt in ('PNG','JPEG','GIF'):
            buf=io.BytesIO(); Image.new('RGB',(32,32),'red').save(buf,fmt); raw=buf.getvalue()
            self.assertEqual(decode_dat(bytes(x^91 for x in raw)),raw)
            key=b'0123456789abcdef'; size=32; tail_size=10
            first=raw[:size]+bytes([16])*16
            enc=Cipher(algorithms.AES(key),modes.ECB()).encryptor()
            wrapped=V2+struct.pack('<II',size,tail_size)+b'\0'+enc.update(first)+enc.finalize()+raw[size:-tail_size]+bytes(x^91 for x in raw[-tail_size:])
            self.assertEqual(decode_dat(wrapped,key,91),raw)

    def test_wxgf_preview(self):
        import av
        buffer=io.BytesIO()
        with av.open(buffer,mode='w',format='hevc') as container:
            stream=container.add_stream('libx265',rate=1)
            stream.width=64; stream.height=64; stream.pix_fmt='yuv420p'
            stream.options={'x265-params':'log-level=error'}
            frame=av.VideoFrame.from_image(Image.new('RGB',(64,64),'red'))
            for packet in stream.encode(frame): container.mux(packet)
            for packet in stream.encode(): container.mux(packet)
        preview=wxgf_preview(b'wxgf'+bytes(28)+buffer.getvalue())
        self.assertTrue(preview and valid_image(preview))

    def test_network_guard(self):
        import subprocess,sys
        if getattr(sys,'frozen',False):
            # The packaged application already installs the guard before all tests.
            with self.assertRaises(PermissionError): sys.audit('socket.connect',None,('example.invalid',443))
        else:
            code="from offline_guard import enforce_offline; import sys; enforce_offline(); sys.audit('socket.connect',None,('example.invalid',443))"
            r=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parent,capture_output=True)
            self.assertNotEqual(r.returncode,0)
            self.assertIn(b'PermissionError',r.stderr)

    def test_committed_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db'
            con=sqlite3.connect(path)
            con.execute('PRAGMA page_size=4096')
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('CREATE TABLE data(value)')
            con.execute('INSERT INTO data VALUES (42)'); con.commit()
            dst=snapshot(path,Path(tmp)/'copy.db',None)
            other=sqlite3.connect(dst)
            self.assertEqual(other.execute('SELECT value FROM data').fetchone()[0],42)
            other.close()
            wal=Path(str(path)+'-wal')
            original=wal.read_bytes()
            head=original[:32]
            endian='<' if struct.unpack('>I',head[:4])[0]==0x377f0682 else '>'
            previous=struct.unpack('>II',original[-4120+16:-4120+24])
            frame=struct.pack('>II',1,0)+head[16:24]
            body=bytes(4096)
            rolling=wal_checksum(frame[:8]+body,endian,previous)
            appended=Path(tmp)/'tail.wal'
            appended.write_bytes(original+frame+struct.pack('>II',*rolling)+body)
            self.assertEqual(committed_wal(wal,lambda:None),committed_wal(appended,lambda:None))
            con.close()

if __name__=='__main__':
    unittest.main()
