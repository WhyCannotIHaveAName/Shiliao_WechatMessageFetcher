import hashlib
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
from media import decode_dat, V2, wxgf_preview, valid_image, key_signature
from snapshot import snapshot, committed_wal, wal_checksum

class ExportTests(unittest.TestCase):
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
