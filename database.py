import datetime as dt
from contextlib import closing
import hashlib
import heapq
import json
import re
import sqlite3
import tempfile
import threading
from pathlib import Path

import native
from snapshot import snapshot, readonly

def text(value):
    return value if isinstance(value, str) else (value.decode("utf8", "replace") if isinstance(value, bytes) else str(value or ""))

def decode_content(value):
    if isinstance(value, bytes):
        if value.startswith(b"\x28\xb5\x2f\xfd"):
            import zstandard
            with zstandard.ZstdDecompressor().stream_reader(value) as stream:
                return stream.read().decode("utf8", "replace").rstrip("\0")
        return value.decode("utf8", "replace").rstrip("\0")
    return text(value)

def db_files(account):
    root = Path(account) / "db_storage"
    return sorted(p for p in root.rglob("*.db")
                  if p.name in ("contact.db", "session.db") or re.fullmatch(r"message_\d+\.db", p.name))

class Session:
    def __init__(self, progress=lambda s: None, cancel=None, scratch=None):
        self.progress = progress
        self.cancel = cancel or threading.Event()
        self.temporary = tempfile.TemporaryDirectory(prefix="Shiliao-", dir=scratch)
        self.cache = Path(self.temporary.name)
        self.account = None
        self.contacts = {}
        self.chats = []
        self.shards = []
        self.image_key = None

    def check(self):
        if self.cancel.is_set():
            raise InterruptedError("已取消")

    def connect(self, root=None, account=None):
        root = root or native.auto_detect_db_dir()
        if not root:
            raise RuntimeError("未找到微信数据目录。请点「选择目录」指定 xwechat_files。")
        root = Path(root)
        if root.name == "db_storage":
            root = root.parent
        if (root / "db_storage").is_dir():
            accounts = [root]
        else:
            accounts = [Path(p) for p in native._find_account_dirs(str(root))]
        if account:
            accounts = [p for p in accounts if p.name == account]
        if not accounts:
            raise RuntimeError("此目录下没有微信 4.x 数据库。请选择 xwechat_files 或账号目录。")
        candidates = native.MemoryKeys(self.cancel, self.progress).candidates()
        matches = []
        for acc in accounts:
            keys = {}
            paths = db_files(acc)
            for p in paths:
                self.check()
                with p.open("rb") as stream:
                    page = stream.read(4096)
                for key, salt in candidates:
                    if native._verify_enc_key(key, page, salt):
                        keys[p] = key + (salt or b"")
                        break
            if keys:
                matches.append((acc, paths, keys))
        if len(matches) > 1:
            raise RuntimeError("检测到多个已登录账号，请选择具体账号目录后读取。")
        if not matches:
            raise RuntimeError("未能取得此账号的数据库密钥。请确认电脑微信已登录，且所选目录属于当前账号。")
        self.account, paths, keys = matches[0]
        missing = [p.name for p in paths if p not in keys]
        if missing:
            raise RuntimeError("以下数据库尚未取得密钥，读取未完成：" + "、".join(missing))
        for i, p in enumerate(paths):
            self.check()
            self.progress("读取本地记录 %d/%d · %s" % (i + 1, len(paths), p.name))
            dst = snapshot(p, self.cache / p.name, keys[p], self.check)
            if re.fullmatch(r"message_\d+\.db", p.name):
                self.shards.append(dst)
        keys.clear()
        candidates.clear()
        self.index()
        return len(self.chats)

    def index(self):
        names, index = {}, {}
        contact = self.cache / "contact.db"
        if contact.exists():
            with closing(readonly(contact)) as con:
                for r in con.execute("SELECT username,nick_name,remark FROM contact"):
                    user, nick, remark = map(text, r)
                    names[user] = {"name": remark or nick or user, "nickname": nick or user}
                    index[hashlib.md5(user.encode()).hexdigest()] = user
        session = self.cache / "session.db"
        if session.exists():
            with closing(readonly(session)) as con:
                for r in con.execute("SELECT username FROM SessionTable"):
                    user = text(r[0])
                    index.setdefault(hashlib.md5(user.encode()).hexdigest(), user)
        self.contacts = names
        chats = {}
        for path in self.shards:
            self.check()
            with closing(readonly(path)) as con:
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"):
                    table = r[0]
                    if not re.fullmatch(r"Msg_[0-9a-fA-F]{32}", table):
                        continue
                    key = table[4:].lower()
                    count, first, last = con.execute('SELECT count(*),min(create_time),max(create_time) FROM "' + table + '"').fetchone()
                    if not count:
                        continue
                    user = index.get(key, key)
                    c = chats.setdefault(key, {"id": key, "username": user, "name": names.get(user, {}).get("name", user),
                                              "count": 0, "first": first, "last": last, "tables": []})
                    c["count"] += count
                    c["first"] = min(c["first"], first)
                    c["last"] = max(c["last"], last)
                    c["tables"].append((path, table))
        self.chats = sorted(chats.values(), key=lambda c: c["last"], reverse=True)

    def messages(self, chat, start=None, end=None):
        conns, streams = [], []
        try:
            for path, table in chat["tables"]:
                con = readonly(path)
                conns.append(con)
                columns = {r[1] for r in con.execute('PRAGMA table_info("' + table + '")')}
                needed = {"local_id", "local_type", "real_sender_id", "create_time", "message_content"}
                if not needed <= columns:
                    raise RuntimeError("数据库消息字段不兼容：" + path.name)
                names = {int(r[0]): text(r[1]) for r in con.execute("SELECT rowid,user_name FROM Name2Id")}
                filters, params = [], []
                if start is not None:
                    filters.append("create_time>=?")
                    params.append(start)
                if end is not None:
                    filters.append("create_time<?")
                    params.append(end)
                where = " WHERE " + " AND ".join(filters) if filters else ""
                order = "create_time," + ("sort_seq," if "sort_seq" in columns else "") + "local_id"
                cursor = con.execute('SELECT * FROM "' + table + '"' + where + " ORDER BY " + order, params)
                streams.append(self._stream(cursor, names, path.name))
            def order_key(m):
                def numeric(value):
                    try:
                        return int(value)
                    except (TypeError, ValueError, OverflowError):
                        return 0
                return numeric(m['timestamp']), numeric(m['sort_seq']), m['shard'], numeric(m['local_id'])
            yield from heapq.merge(*streams, key=order_key)
        finally:
            for con in conns:
                con.close()

    def _stream(self, rows, names, shard):
        for row in rows:
            self.check()
            r = dict(row)
            try:
                message = self._decode_row(r, names, shard)
            except (InterruptedError, MemoryError):
                raise
            except Exception as exc:
                # A malformed payload belongs to one row. Retain all original fields.
                import base64
                fields = {k: ({'base64': base64.b64encode(v).decode('ascii')} if isinstance(v, bytes) else v)
                          for k, v in r.items()}
                sender = names.get(r.get('real_sender_id'), '')
                message = {'local_id':r.get('local_id'), 'server_id':r.get('server_id'),
                           'sender_id':r.get('real_sender_id'), 'sender_username':sender,
                           'sender':self.contacts.get(sender, {}).get('nickname', sender) or '未知发送者',
                           'timestamp':r.get('create_time'), 'sort_seq':r.get('sort_seq'),
                           'type_code':r.get('local_type'), 'shard':shard, 'time':text(r.get('create_time')),
                           'raw_content':text(r.get('message_content')), 'original_fields':fields,
                           'parse_warning':'读取消息字段失败：' + type(exc).__name__}
                try:
                    message['time'] = dt.datetime.fromtimestamp(message['timestamp']).astimezone().isoformat(timespec='seconds')
                except (ValueError, TypeError, OSError, OverflowError):
                    pass
                for k,v in list(message.items()):
                    if isinstance(v, bytes):
                        message[k] = text(v)
            yield message

    def _decode_row(self, r, names, shard):
        sender_id = r["real_sender_id"]
        sender = names.get(int(sender_id or 0), "")
        content = decode_content(r.get("message_content"))
        compressed = r.get("compress_content")
        if not content and compressed:
            content = decode_content(compressed)
        return {"local_id": r["local_id"], "server_id": r.get("server_id"), "type_code": r["local_type"],
               "sender_id": sender_id, "sender_username": sender,
               "sender": self.contacts.get(sender, {}).get("nickname", sender) or ('系统' if r['local_type'] in (10000,10002) else '未知发送者'),
               "timestamp": r["create_time"],
               "time": dt.datetime.fromtimestamp(r["create_time"]).astimezone().isoformat(timespec="seconds"),
               "sort_seq": r.get("sort_seq") or 0, "shard": shard, "raw_content": content,
               "_packed": r.get("packed_info_data") or b""}

    def close(self):
        self.image_key = None
        self.temporary.cleanup()

