"""Read the comments actually present in a local WeChat Moments snapshot."""
from collections import defaultdict
from contextlib import closing
import datetime as dt
import html
import re
import xml.etree.ElementTree as ET

from zstandard import ZstdError

from database import decode_content, text
from snapshot import readonly

COMMENT_STYLE = '''
.comments{margin-top:14px;border-top:1px solid #e4ddd2;padding-top:8px}
.comments>summary{cursor:pointer;color:#9f462f;font-weight:600}
.comment{margin:8px 0 0;padding:9px 11px;border-radius:8px;background:#faf7f1;scroll-margin-top:8px}
.comment:target{outline:2px solid #b76b54}.comment p{margin:4px 0}
.comment a{color:#9f462f}
'''


def integer(value):
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return 0


def number_id(value, bits):
    number = integer(value)
    if number and -(1 << (bits - 1)) <= number < (1 << bits):
        return str(number & ((1 << bits) - 1))
    return None


def timestamp_fields(value):
    stamp = integer(value)
    if stamp > 0:
        try:
            return stamp, dt.datetime.fromtimestamp(stamp).astimezone().isoformat(timespec='seconds')
        except (ValueError, OSError, OverflowError):
            pass
    return None, None


def aliases(record):
    for key in ('comment64_id', 'comment_id'):
        if record.get(key):
            yield key, record[key]


def display_name(contacts, username, nickname='', default='未知评论者'):
    return contacts.get(username, {}).get('name') or nickname or username or default


def new_comment(contacts, *, id32, id64, username, nickname, body, stamp, source,
                image_count=0, emoji_count=0, target32=None, target64=None, target_user=''):
    timestamp, published = timestamp_fields(stamp)
    reply = None
    if target32 or target64 or target_user:
        reply = {'comment_id': target32, 'comment64_id': target64, 'username': target_user,
                 'author': display_name(contacts, target_user, default='原评论'),
                 'parent_id': None}
    return {'id': id64 or id32, 'comment_id': id32, 'comment64_id': id64,
            'username': username, 'author': display_name(contacts, username, nickname),
            'timestamp': timestamp, 'time': published, 'text': body,
            'image_count': image_count, 'emoji_count': emoji_count,
            'source': source, 'reply_to': reply}


def embedded_comments(raw, contacts):
    payload = decode_content(raw).strip().lstrip('\ufeff')
    if '<!DOCTYPE' in payload:
        raise ValueError('unexpected document type')
    node = ET.fromstring(payload)
    for item in node.findall('.//comment_user_list/user_comment'):
        yield new_comment(
            contacts,
            id32=number_id(item.findtext('comment_id'), 32),
            id64=number_id(item.findtext('comment_64id'), 64),
            username=item.findtext('username') or '', nickname=item.findtext('nickname') or '',
            body=item.findtext('content') or '', stamp=item.findtext('create_time'),
            source='timeline', image_count=max(0, integer(item.findtext('comment_imageinfo_count'))),
            emoji_count=max(0, integer(item.findtext('comment_emojiinfo_count'))),
            target32=number_id(item.findtext('ref_comment_id'), 32),
            target64=number_id(item.findtext('ref_comment_64id'), 64),
            target_user=item.findtext('ref_username') or '',
        ), bool(integer(item.findtext('b_deleted')))


def notification_rows(con):
    fields = {row[1] for row in con.execute('PRAGMA table_info(SnsMessage_tmp3)')}
    needed = {'feed_id', 'type', 'from_username', 'content', 'create_time'}
    if not needed <= fields:
        return ()
    optional = ('local_id', 'from_nickname', 'comment_id', 'comment64_id', 'del_status', 'serialized_ref_buf')
    columns = ','.join(name if name in fields else 'NULL AS ' + name for name in optional)
    order = ' ORDER BY local_id DESC' if 'local_id' in fields else ''
    return con.execute('SELECT feed_id,from_username,content,create_time,' + columns
                       + ' FROM SnsMessage_tmp3 WHERE type=2' + order)


def notice_recipient(value):
    # SnsMessage_tmp3 serializes the referenced participant as protobuf field 1.
    if not isinstance(value, bytes):
        return ''
    cursor = 0

    def varint():
        nonlocal cursor
        number = 0
        for shift in range(0, 70, 7):
            byte = value[cursor]
            cursor += 1
            number |= (byte & 127) << shift
            if byte < 128:
                return number
        raise ValueError('oversized varint')

    try:
        while cursor < len(value):
            number, wire = divmod(varint(), 8)
            if wire == 2:
                length = varint()
                end = cursor + length
                if end > len(value):
                    return ''
                if number == 1:
                    candidate = value[cursor:end].decode('utf8')
                    return candidate if re.fullmatch(r'[\w@.-]{1,128}', candidate, re.ASCII) else ''
                cursor = end
            elif wire == 0:
                varint()
            elif wire in (1, 5):
                cursor += 8 if wire == 1 else 4
            else:
                return ''
    except (IndexError, ValueError):
        pass
    return ''


def add_replies(comments, contacts):
    known = {key: comment for comment in comments for key in aliases(comment)}
    for comment in comments:
        reply = comment['reply_to']
        if not reply:
            continue
        parent = next((known[key] for key in aliases(reply) if key in known), None)
        if parent is not None and parent is not comment and (not reply['username'] or reply['username'] == parent['username']):
            reply.update(parent_id=parent['id'], author=parent['author'], username=parent['username'])
        elif reply['username']:
            reply['author'] = display_name(contacts, reply['username'])


def load_comments(session, posts):
    post_ids = [post['id'] for post in posts]
    if not post_ids:
        return {}, 0
    thread_ids = {number_id(post_id, 64): post_id for post_id in post_ids if number_id(post_id, 64)}
    threads, known, removed = defaultdict(list), defaultdict(set), defaultdict(set)
    warnings = 0
    with closing(readonly(session.cache / 'sns.db')) as con:
        for first in range(0, len(post_ids), 500):
            batch = post_ids[first:first + 500]
            placeholders = ','.join('?' for _ in batch)
            for row in con.execute('SELECT tid,content FROM SnsTimeLine WHERE tid IN (' + placeholders + ')', batch):
                session.check()
                tid = text(row['tid'])
                try:
                    for comment, deleted in embedded_comments(row['content'], session.contacts):
                        identifiers = set(aliases(comment))
                        if deleted:
                            removed[tid].update(identifiers)
                        elif not identifiers & known[tid]:
                            threads[tid].append(comment)
                            known[tid].update(identifiers)
                except (ET.ParseError, ValueError, ZstdError):
                    warnings += 1
        for row in notification_rows(con):
            session.check()
            tid = thread_ids.get(number_id(row['feed_id'], 64))
            if tid is None:
                continue
            id32, id64 = number_id(row['comment_id'], 32), number_id(row['comment64_id'], 64)
            identifiers = set(aliases({'comment_id': id32, 'comment64_id': id64}))
            if integer(row['del_status']):
                # Older notification copies of this row must stay suppressed.
                removed[tid].update(identifiers)
                continue
            if identifiers & (known[tid] | removed[tid]):
                continue
            try:
                body = decode_content(row['content'])
            except (ValueError, ZstdError):
                body = '评论正文无法解析'
                warnings += 1
            comment = new_comment(
                session.contacts,
                id32=id32, id64=id64,
                username=text(row['from_username']), nickname=text(row['from_nickname']),
                body=body, stamp=row['create_time'], source='notification',
                target_user=notice_recipient(row['serialized_ref_buf']),
            )
            threads[tid].append(comment)
            known[tid].update(identifiers)
    for tid, comments in threads.items():
        session.check()
        comments[:] = [comment for comment in comments if not (set(aliases(comment)) & removed[tid])]
        comments.sort(key=lambda comment: (comment['timestamp'] is None, comment['timestamp'] or 0))
        add_replies(comments, session.contacts)
    return threads, warnings


def write_comments(comments, markup, plain, index):
    if not comments:
        return
    anchors = {comment['id']: f'm{index}-c{offset}' for offset, comment in enumerate(comments) if comment['id']}
    markup.write(f'<details class="comments"><summary>评论（{len(comments):,} 条）</summary>')
    plain.write(f'评论（{len(comments):,} 条）\n')
    esc = html.escape
    for offset, comment in enumerate(comments):
        author = comment['author']
        timestamp = comment['time'] or '评论时间未知'
        reply = comment['reply_to']
        heading = esc(author)
        line = author
        if reply:
            label = '回复 ' + reply['author']
            parent = anchors.get(reply['parent_id'])
            if parent:
                heading += f' · <a href="#{parent}">{esc(label)}</a>'
            else:
                has_id = bool(reply['comment64_id'] or reply['comment_id'])
                heading += ' · ' + esc(label + ('（原评论缺失）' if has_id else ''))
            line += ' · ' + label
            ref = reply['parent_id'] or reply['comment64_id'] or reply['comment_id']
            if ref:
                line += f'（评论 {ref}）'
        markup.write(f'<section id="m{index}-c{offset}" class="comment"><header>{heading} · {esc(timestamp)}</header>')
        plain.write(f'  {line} · {timestamp}\n')
        if comment['text']:
            markup.write(f"<p>{esc(comment['text'])}</p>")
            for part in comment['text'].splitlines():
                plain.write('    ' + part + '\n')
        attachments = [f'{comment[key]:,} {label}' for key, label in
                       (('image_count', '张图片'), ('emoji_count', '个表情')) if comment[key]]
        if attachments:
            media_note = '评论附件：' + '、'.join(attachments)
            markup.write(f'<small>{esc(media_note)}</small>')
            plain.write('    ' + media_note + '\n')
        if comment['source'] == 'notification':
            markup.write('<small>本地评论通知</small>')
        markup.write('</section>')
    markup.write('</details>')
