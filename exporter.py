"""Streaming offline exports. No fixed message limit and no external requests."""
import datetime as dt
import html
import json
import re
import uuid
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import quote

TYPES = {1: '文本', 3: '图片', 34: '语音', 43: '视频', 47: '动画表情',
         48: '位置', 49: '分享', 50: '通话', 10000: '系统消息', 10002: '系统消息'}

def message_xml(payload):
    # Some desktop messages wrap a complete XML document in an extra <msg>.
    # Strip only the misplaced declaration at this boundary, never text in CDATA.
    try:
        return ET.fromstring(payload)
    except ET.ParseError:
        cleaned, count = re.subn(r'^(\s*<msg\s*>\s*)<\?xml\s+[^?<>]*\?>', r'\1', payload, count=1)
        if not count:
            raise
        return ET.fromstring(cleaned)

def record_items(node, depth=0):
    """Unwrap one record and read only its direct children, preserving their order."""
    if node is None:
        return [], '此条只保存了聊天记录摘要'
    if depth >= 32:
        return [], '嵌套层数过深，余下内容保留在原始消息中'
    for _ in range(10):
        if node.tag == 'datalist':
            break
        nested = next((child for child in node if child.tag in
                       ('recordinfo','recordxml','recorditem','datalist','appmsg','msg')), None)
        if nested is not None:
            node = nested
            continue
        payload = (node.text or '').strip()
        if payload.startswith('&lt;'):
            payload = html.unescape(payload)
        try:
            node = message_xml(payload)
        except (ValueError, ET.ParseError):
            return [], '未能读取此层明细，已保留摘要和原始消息'
    else:
        return [], '未能识别此层结构，详见原始消息'
    children = []
    for item in node.findall('dataitem'):
        typ = item.get('datatype', '')
        title = item.findtext('datatitle') or ''
        desc = item.findtext('datadesc') or ''
        label = {'1':'文本','2':'图片','3':'语音','4':'视频','5':'链接','8':'文件',
                 '14':'聊天记录','17':'聊天记录','37':'动画表情'}.get(typ, '附件')
        wrapper = next((child for child in item if child.tag in
                        ('recordxml','recordinfo','recorditem','datalist')), None)
        is_card = wrapper is not None or typ in ('14','17') or typ == '19' and '聊天记录' in title
        if is_card and wrapper is None and re.match(r'\s*(<|&lt;)(recordinfo|recorditem|recordxml|msg|appmsg)\b',desc):
            wrapper = item.find('datadesc')
        child = {'sender':item.findtext('sourcename') or item.findtext('dataitemsource/realchatname')
                         or item.findtext('dataitemsource/fromusr') or item.findtext('fromusr') or '未知发送者',
                 'time':item.findtext('sourcetime') or item.findtext('srcMsgCreateTime') or '',
                 'type':typ, 'kind':'聊天记录' if is_card else label,
                 'raw_xml':ET.tostring(item,encoding='unicode'), 'forwarded':[]}
        if is_card:
            child['forwarded'], note = record_items(wrapper,depth+1)
            child['text'] = title or '聊天记录'
            if note:
                child['text'] += '\n' + '\n'.join(filter(None,(desc,note)))
        else:
            url = item.findtext('weburlitem/link') or item.findtext('dataurl') or item.findtext('link')
            emoji = item.find('emojiitem')
            caption = (emoji.get('desc') or emoji.findtext('desc')) if emoji is not None else ''
            parts = [part for part in (title,desc,url,caption) if part]
            child['text'] = '\n'.join(dict.fromkeys(parts)) or '[' + label + ']'
            if typ == '2':
                child['image_ids'] = [value for value in (item.get('dataid'),item.findtext('fullmd5'),
                                        item.findtext('thumbfullmd5')) if value and re.fullmatch('[a-fA-F0-9]{32}',value)]
        children.append(child)
    return children, '' if children else '此层没有可展开的消息'

def parse_message(raw, code):
    base = int(code) & 0xffffffff
    label = TYPES.get(base, '消息类型%d' % code)
    result = {'kind': label, 'text': raw if base == 1 else '[' + label + ']', 'forwarded': []}
    if base == 1:
        return result
    try:
        root = message_xml(raw[raw.index('<'):])
    except (ValueError, ET.ParseError):
        if base == 49 and int(code) >> 32 == 19:
            result.update(kind='聊天记录', text='[聊天记录] 未能读取此卡片的明细，详见原始消息信息')
        else:
            result['text'] += raw
        return result
    app = root if root.tag == 'appmsg' else root.find('.//appmsg')
    fields = []
    def add(value):
        if value and value.strip() and value.strip() not in fields:
            fields.append(value.strip())
    if app is not None:
        subtype = app.findtext('type') or str(int(code) >> 32)
        label = {'5':'链接','6':'文件','19':'聊天记录','33':'小程序','36':'小程序',
                 '57':'引用','2001':'红包','2000':'转账','51':'视频号'}.get(subtype, label)
        for tag in ('title','des','url','sourcedisplayname','receivertitle','receiverdes',
                    'sendertitle','senderdes','feedesc','pay_memo'):
            for item in (app.findall(tag) if subtype == '19' else app.iter(tag)):
                add(item.text)
        ref = app.find('refermsg')
        if ref is not None:
            add('引用 ' + (ref.findtext('displayname') or '') + '：' + (ref.findtext('content') or ''))
        if subtype == '19' or app.find('recorditem') is not None:
            result['forwarded'], note = record_items(app.find('recorditem'))
            if note:
                add(note)
    elif base == 47:
        emoji = root if root.tag == 'emoji' else root.find('.//emoji')
        if emoji is not None:
            for key in ('desc','name','text','meaning','title'):
                add(emoji.get(key))
            if not fields:
                add(emoji.get('cdnurl') or emoji.get('md5'))
    elif base == 48:
        loc = root if root.tag == 'location' else root.find('.//location')
        if loc is not None:
            for key in ('label','poiname','x','y'):
                add(loc.get(key))
    elif base in (10000, 10002):
        add(' '.join(root.itertext()))
    result.update(kind=label, text='[' + label + ']' + '\n'.join(fields))
    return result

def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:60] or '会话'

STYLE = '''body{font:16px/1.6 system-ui,sans-serif;background:#f1f5f4;color:#1c3030;margin:0}
main{max-width:900px;margin:auto;padding:28px}article{padding:18px 22px;margin:16px 0;background:white;border-radius:14px}
header{color:#55716e;font-size:14px}p{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:100%;max-height:640px}
details,small{color:#657672}pre{white-space:pre-wrap;overflow-wrap:anywhere}blockquote{border-left:3px solid #9bbab4;padding-left:16px}'''

def html_message(m, nested=False):
    esc = lambda s: html.escape(str(s))
    tag = 'blockquote' if nested else 'article'
    out = '<' + tag + '><header><b>' + esc(m['sender']) + '</b> · ' + esc(m['time']) + '</header><p>' + esc(m['text']) + '</p>'
    if m['forwarded']:
        out += '<details class="record"><summary>展开聊天记录（%d 条）</summary>' % len(m['forwarded'])
        out += ''.join(html_message(child, True) for child in m['forwarded']) + '</details>'
    media = m.get('media')
    if media:
        if media.get('file'):
            out += '<a href="' + esc(media['file']) + '"><img loading="lazy" src="' + esc(media['file']) + '" alt="聊天图片"></a>'
        out += '<p><small>' + esc(media['status']) + '</small></p>'
        if media.get('original_file'):
            out += '<a href="' + esc(media['original_file']) + '">保存的原始图片容器</a>'
    if not nested and m['kind'] != '文本':
        out += '<details><summary>原始消息信息</summary><pre>' + esc(m['raw_content']) + '</pre></details>'
    return out + '</' + tag + '>\n'

def write_forwarded(stream, children, depth=1):
    indent = '  ' * depth
    for child in children:
        stream.write(indent + child['sender'] + ' ' + child['time'] + '\n')
        for line in child['text'].splitlines():
            stream.write(indent + line + '\n')
        if child.get('media'):
            stream.write(indent + child['media']['status'] + ' ' + child['media'].get('file','') + '\n')
        write_forwarded(stream, child['forwarded'], depth+1)

def attach_forwarded(children, images, chat, assets):
    for child in children:
        if child['kind'] == '图片':
            hashes = ' '.join(child.get('image_ids', []))
            child['media'] = images.attach(chat,{'raw_content':'','_packed':hashes},assets) if images else {'status':'未启用图片导出'}
        attach_forwarded(child['forwarded'],images,chat,assets)

def export_chats(session, chats, directory, start=None, end=None, include_images=True):
    if not chats:
        raise ValueError('请至少选择一个会话')
    target = Path(directory) / ('拾聊导出-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    staging = target.with_name(target.name + '.未完成')
    staging.mkdir(parents=True)
    summary = {'state':'incomplete', 'created':dt.datetime.now().astimezone().isoformat(),
               'chats':[], 'messages':0, 'images':0, 'unavailable_images':0, 'thumbnails':0, 'parse_warnings':0}
    try:
        if include_images:
            from media import LocalImages
            images = LocalImages(session, chats)
        else:
            images = None
        for chat in chats:
            session.check()
            folder = staging / (safe_name(chat['name']) + '-' + chat['id'][:10])
            folder.mkdir()
            assets = folder / 'assets'
            assets.mkdir()
            count = 0
            with (folder/'聊天记录.html').open('w',encoding='utf8') as h, (folder/'聊天记录.txt').open('w',encoding='utf8') as t, (folder/'聊天记录.json').open('w',encoding='utf8') as j:
                h.write('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' data:; style-src \'unsafe-inline\'"><title>' + html.escape(chat['name']) + '</title><style>' + STYLE + '</style><main><h1>' + html.escape(chat['name']) + '</h1>')
                j.write('[\n')
                chat_stats = {'name':chat['name'],'id':chat['id'],'count':0,'folder':folder.name}
                summary['chats'].append(chat_stats)
                try:
                    for m in session.messages(chat, start, end):
                        session.check()
                        summary['position'] = {key:m.get(key) for key in ('shard','local_id','type_code')}
                        try:
                            m.update(parse_message(m['raw_content'], m['type_code']))
                        except (TypeError, ValueError, LookupError, OverflowError, RecursionError, ET.ParseError) as exc:
                            m['parse_warning'] = '消息解析失败：' + type(exc).__name__
                            m.update(kind='未解析消息', text=m['raw_content'], forwarded=[])
                        if m.get('parse_warning'):
                            summary['parse_warnings'] += 1
                            m['text'] = '[原始消息，详见 JSON] ' + m['text']
                        if m['kind'] == '图片':
                            m['media'] = images.attach(chat,m,assets) if images else {'status':'未启用图片导出'}
                            if m['media'].get('code') == 'thumbnail':
                                summary['thumbnails'] += 1
                            summary['images' if m['media'].get('file') else 'unavailable_images'] += 1
                        attach_forwarded(m['forwarded'], images, chat, assets)
                        m.pop('_packed', None)
                        h.write(html_message(m))
                        t.write(m['sender'] + '\n' + m['time'] + '\n' + m['text'] + '\n')
                        write_forwarded(t, m['forwarded'])
                        if m.get('media'):
                            t.write('[' + m['media']['status'] + '] ' + m['media'].get('file','') + '\n')
                        t.write('\n')
                        if count:
                            j.write(',\n')
                        json.dump(m,j,ensure_ascii=False)
                        count += 1
                        chat_stats['count'] = count
                        summary['messages'] += 1
                        if count % 100 == 0:
                            session.progress('正在导出：%s · %d 条' % (chat['name'],count))
                except InterruptedError:
                    j.write('\n]\n')
                    h.write('<footer>导出已取消，保留 %d 条消息</footer></main></html>' % count)
                    raise
                j.write('\n]\n')
                h.write('<footer>共 %d 条消息</footer></main></html>' % count)
        session.check()
        summary['state'] = 'complete'
        (staging/'导出统计.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
        links = ''.join('<li><a href="' + quote(c['folder'] + '/聊天记录.html') + '">' + html.escape(c['name']) + '</a> · %d 条</li>' % c['count'] for c in summary['chats'])
        (staging/'打开聊天记录.html').write_text('<!doctype html><meta charset="utf-8"><title>拾聊导出</title><style>'+STYLE+'</style><main><h1>聊天记录</h1><ul>'+links+'</ul></main>',encoding='utf8')
        staging.rename(target)
        return target, summary
    except BaseException as exc:
        summary['state'] = 'incomplete'
        summary['reason'] = 'cancelled' if isinstance(exc, InterruptedError) else 'failed'
        summary['error'] = type(exc).__name__ + '：' + str(exc)
        (staging/'导出统计.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
        raise
