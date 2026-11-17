"""Offline statistics from the same snapshots used for exports."""
from collections import Counter, defaultdict
from contextlib import closing
import datetime as dt
from functools import lru_cache
import logging
import re
import unicodedata

import jieba
from zstandard import ZstdError

from database import decode_content, text
from moments import filter_dates, load_moments
from snapshot import readonly

HAN = re.compile(r'[\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af]')
URL = re.compile(r'https?://[^\s<>，。！？]+', re.IGNORECASE)
BRACKET = re.compile(r'\[([^\[\]\r\n]{1,20})\]')
# The bracketed names of standard WeChat text emojis. Counts retain the original name.
WECHAT_EMOJIS = set('''
微笑 撇嘴 色 发呆 得意 流泪 害羞 闭嘴 睡 大哭 尴尬 发怒 调皮 呲牙 龇牙 惊讶 难过 酷 冷汗 抓狂
吐 偷笑 愉快 白眼 傲慢 饥饿 困 惊恐 流汗 憨笑 悠闲 奋斗 咒骂 疑问 嘘 晕 疯了 衰 骷髅 敲打 再见
擦汗 抠鼻 鼓掌 糗大了 坏笑 左哼哼 右哼哼 哈欠 鄙视 委屈 快哭了 阴险 亲亲 吓 可怜 菜刀 西瓜 啤酒
篮球 乒乓 咖啡 饭 猪头 玫瑰 凋谢 嘴唇 爱心 心碎 蛋糕 闪电 炸弹 刀 足球 瓢虫 便便 月亮 太阳 礼物
拥抱 强 弱 握手 胜利 抱拳 勾引 拳头 差劲 爱你 NO OK 爱情 飞吻 跳跳 发抖 怄火 转圈 磕头 回头 跳绳
投降 激动 乱舞 献吻 左太极 右太极 笑哭 捂脸 破涕为笑 皱眉 耶 机智 失望 无语 狗头 旺柴 打脸 加油
嘿哈 捂脸 奸笑 机智 皱眉 耶 茶 让我看看 叹气 苦涩 裂开 啊这 天啊 托腮 揉脸 社会社会 吃瓜 加油加油
合十 抱抱 红包 福 发 红包来了 围观 庆祝 烟花 爆竹 打气 辛苦 666 翻白眼
'''.split())
SERVICE_USERS = {'filehelper', 'fmessage', 'newsapp', 'weixin', 'medianote', 'floatbottle',
                 'qqmail', 'qmessage', 'tmessage', 'weibo', 'feedsapp', 'notification_messages',
                 'masssendapp', 'officialaccounts', 'appbrandcustomerservicemsg', 'weixinreminder'}
RANK_LIMIT = 12


def sender_maps(session):
    maps = {}
    for path in session.shards:
        session.check()
        with closing(readonly(path)) as con:
            maps[path] = {row[0]: text(row[1]) for row in con.execute('SELECT rowid,user_name FROM Name2Id')}
    return maps


def identify_owner(session, maps):
    if session.account is None:
        return ''
    folder = session.account.name
    conventional = re.sub(r'_[a-zA-Z0-9]{4}$', '', folder)
    known_senders = {user for names in maps.values() for user in names.values()}
    for candidate in (folder, conventional):
        if candidate in session.contacts and candidate in known_senders:
            return candidate
    return ''


def is_private_person(user, contacts, owner):
    if not user or user == owner or user in SERVICE_USERS or user.startswith('gh_') or user.endswith('@chatroom'):
        return False
    contact = contacts.get(user)
    return contact is not None and not contact.get('verify_flag')


def build_token_counter(scratch):
    jieba.setLogLevel(logging.WARNING)
    tokenizer = jieba.Tokenizer()
    tokenizer.tmp_dir = str(scratch)

    @lru_cache(maxsize=8192)
    def tokens(raw):
        emojis = tuple('[' + name + ']' for name in BRACKET.findall(raw) if name in WECHAT_EMOJIS)
        cleaned = URL.sub(' ', unicodedata.normalize('NFKC', raw))
        cleaned = BRACKET.sub(lambda match: ' ' if match[1] in WECHAT_EMOJIS else match[0], cleaned)
        chars = tuple(HAN.findall(cleaned))
        words = tuple(word.casefold() for word in tokenizer.cut(cleaned, HMM=False)
                      if len(word) >= 2 and word.isalpha())
        return chars, words, emojis

    return tokens


def rank_names(counter, names, limit=RANK_LIMIT):
    total = Counter(names.get(user, user) or '未知发布者' for user in counter)
    rows = []
    for user, count in sorted(counter.items(), key=lambda item: (-item[1], names.get(item[0], item[0]), item[0]))[:limit]:
        name = names.get(user, user) or '未知发布者'
        if total[name] > 1:
            name += ' · ' + user
        rows.append((name, count))
    return rows


def rank_words(counter):
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:RANK_LIMIT]


def chat_stat_rows(session, chat, maps, start, end, token_content=True):
    for path, table in chat['tables']:
        session.check()
        with closing(readonly(path)) as con:
            schema = {row[1] for row in con.execute('PRAGMA table_info("' + table + '")')}
            if not {'local_type', 'real_sender_id', 'create_time', 'message_content'} <= schema:
                raise RuntimeError('聊天统计所需的字段不齐全：' + path.name)
            condition = '(local_type & 4294967295)=1'
            raw = 'CASE WHEN ' + condition + ' THEN message_content END' if token_content else 'NULL'
            compressed = ('CASE WHEN ' + condition + ' THEN compress_content END'
                          if token_content and 'compress_content' in schema else 'NULL')
            filters = ['(local_type & 4294967295) NOT IN (0, 10000, 10002)']
            args = []
            if start is not None:
                filters.append('create_time>=?')
                args.append(start)
            if end is not None:
                filters.append('create_time<?')
                args.append(end)
            cursor = con.execute('SELECT create_time,real_sender_id,' + raw + ',' + compressed
                                 + ' FROM "' + table + '" WHERE ' + ' AND '.join(filters), args)
            for stamp, sender, body, compressed_body in cursor:
                session.check()
                yield stamp, maps[path].get(sender, ''), body, compressed_body


def chat_statistics(session, start=None, end=None, *, include_words=True):
    names = {user: info.get('name') or user for user, info in session.contacts.items()}
    maps = sender_maps(session)
    owner = identify_owner(session, maps)
    tokens = build_token_counter(session.cache) if include_words else None
    interactions, by_year = Counter(), defaultdict(Counter)
    chars, words, own_chars, own_words, own_emojis = (Counter() for _ in range(5))
    messages = text_messages = own_text_messages = skipped_text = 0
    for chat in session.chats:
        user = chat['username']
        personal = is_private_person(user, session.contacts, owner)
        for timestamp, sender, body, packed in chat_stat_rows(session, chat, maps, start, end, include_words):
            messages += 1
            if messages % 10000 == 0:
                session.progress(f'正在统计聊天 · 已读取 {messages:,} 条')
            if personal:
                interactions[user] += 1
                try:
                    if int(timestamp or 0) > 0:
                        by_year[dt.datetime.fromtimestamp(int(timestamp)).year][user] += 1
                except (ValueError, TypeError, OSError, OverflowError):
                    pass
            if not (tokens and (body or packed)):
                continue
            try:
                content = decode_content(body) or decode_content(packed)
            except (ValueError, ZstdError):
                skipped_text += 1
                continue
            if user.endswith('@chatroom') and sender and content.startswith((sender + ':\n', sender + ':\r\n')):
                content = content.partition('\n')[2]
            if not content:
                continue
            text_messages += 1
            msg_chars, msg_words, emoji = tokens(content)
            chars.update(msg_chars)
            words.update(msg_words)
            if owner and sender == owner:
                own_text_messages += 1
                own_chars.update(msg_chars)
                own_words.update(msg_words)
                own_emojis.update(emoji)
    annual = []
    for year, bucket in sorted(by_year.items(), reverse=True):
        best = max(bucket.values())
        winners = [label for label, count in rank_names(bucket, names) if count == best]
        total = sum(count == best for count in bucket.values())
        annual.append({'year': year, 'leaders': winners, 'tied': total, 'count': best})
    return {'messages': messages, 'text_messages': text_messages, 'own_text_messages': own_text_messages,
            'skipped_text': skipped_text, 'owner': owner, 'include_words': include_words,
            'private_rank': rank_names(interactions, names), 'annual': annual,
            'chars': rank_words(chars), 'words': rank_words(words),
            'own_chars': rank_words(own_chars), 'own_words': rank_words(own_words),
            'own_emojis': rank_words(own_emojis)}


def moments_statistics(session, start=None, end=None):
    if session.moment_count <= 0:
        return {'count': 0, 'authors': 0, 'rank': [], 'status': session.moment_status}
    session.progress('正在统计本机朋友圈…')
    posts, unknown = filter_dates(session, load_moments(session).posts, start, end)
    counts = Counter()
    names = {}
    for post in posts:
        session.check()
        user = post['username']
        if user:
            counts[user] += 1
            names[user] = post['author']
    return {'count': len(posts), 'authors': len(counts), 'rank': rank_names(counts, names),
            'unknown_time': unknown, 'status': ''}
