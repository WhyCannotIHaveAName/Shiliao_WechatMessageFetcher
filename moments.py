"""Read and export the locally cached WeChat 4.x Moments timeline."""
from collections import defaultdict
from contextlib import closing
import datetime as dt
import hashlib
import html
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid
import xml.etree.ElementTree as ET

from PIL import Image
from zstandard import ZstdError

from database import decode_content, text
from exporter import STYLE
from media import ImageDecoder, kind, valid_image
from snapshot import readonly


PAGE = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; img-src \'self\'; style-src \'unsafe-inline\'">')


def parse_moment(row, contacts):
    username = text(row['user_name'])
    post = {'id': text(row['tid']), 'username': username, 'author': username,
            'timestamp': None, 'time': None, 'text': '', 'links': [], 'images': []}
    try:
        payload = decode_content(row['content']).strip().lstrip('\ufeff')
        if '<!DOCTYPE' in payload:
            raise ValueError('unexpected document type')
        node = ET.fromstring(payload)
        timeline = node if node.tag == 'TimelineObject' else node.find('TimelineObject')
        if timeline is None:
            raise ValueError('missing TimelineObject')
    except (ET.ParseError, ValueError, ZstdError):
        post['warning'] = '朋友圈正文无法解析'
    else:
        username = timeline.findtext('username') or username
        post['username'] = username
        post['text'] = timeline.findtext('contentDesc') or ''
        try:
            timestamp = int(timeline.findtext('createTime') or '0')
            if timestamp <= 0:
                raise ValueError('missing timestamp')
            post['time'] = dt.datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec='seconds')
            post['timestamp'] = timestamp
        except (ValueError, OSError, OverflowError):
            post['warning'] = '缺少可用的发布时间'
        content = timeline.find('ContentObject')
        if content is not None:
            address = (content.findtext('contentUrl') or '').strip()
            title = (content.findtext('title') or '').strip()
            description = (content.findtext('description') or '').strip()
            if address:
                post['links'].append({'url': address, 'title': title, 'description': description})
            for media in content.findall('mediaList/media'):
                # WeChat image entries use type 2. A link card may also carry a type 2 cover.
                if (media.findtext('type') or '').strip() != '2':
                    continue
                source = media.find('url')
                dimensions = media.find('size')
                attrs = dimensions.attrib if dimensions is not None else {}
                digest = source.get('md5', '').lower() if source is not None else ''
                post['images'].append({
                    'role': 'cover' if address else 'photo',
                    'md5': digest if re.fullmatch(r'[a-f0-9]{32}', digest) else '',
                    'width': positive_integer(attrs.get('width')),
                    'height': positive_integer(attrs.get('height')),
                    'size': positive_integer(attrs.get('totalSize')),
                })
    post['author'] = contacts.get(username, {}).get('name') or username or '未知发布者'
    return post


def positive_integer(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


class MomentTimeline:
    def __init__(self, posts, unique_signatures):
        self.posts = posts
        self.unique_signatures = unique_signatures
        self.search_index = []
        for post in posts:
            fields = [post['author'], post['username'], post['text']]
            for link in post['links']:
                fields.extend((link['title'], link['description'], link['url']))
            self.search_index.append('\n'.join(fields).casefold())

    def search(self, keyword):
        term = keyword.strip().casefold()
        if not term:
            return self.posts
        return [post for post, content in zip(self.posts, self.search_index) if term in content]


def load_moments(session):
    posts = []
    signatures = defaultdict(set)
    with closing(readonly(session.cache / 'sns.db')) as con:
        for row in con.execute('SELECT tid, user_name, content FROM SnsTimeLine ORDER BY tid DESC'):
            session.check()
            post = parse_moment(row, session.contacts)
            for index, image in enumerate(post['images']):
                signature = image_signature(image)
                if all(signature):
                    signatures[signature].add(image['md5'] or (post['id'], index))
            posts.append(post)
    posts.sort(key=lambda post: post['timestamp'] or 0, reverse=True)
    unique = {signature for signature, identities in signatures.items() if len(identities) == 1}
    return MomentTimeline(posts, unique)


def filter_dates(session, posts, start, end):
    if start is None and end is None:
        return posts, 0
    filtered, unknown = [], 0
    for post in posts:
        session.check()
        stamp = post['timestamp']
        if stamp is None:
            unknown += 1
        elif (start is None or stamp >= start) and (end is None or stamp < end):
            filtered.append(post)
    return filtered, unknown


def image_signature(image):
    return image['width'], image['height'], image['size']


def picture_bytes(decoder, source):
    data = decoder.decode(source.read_bytes())
    # Windows SNS JPEG caches can include a 24-byte trailer after the end marker.
    if kind(data) == 'jpg' and data[-26:-24] == b'\xff\xd9':
        data = data[:-24]
    if not valid_image(data):
        raise ValueError('无法读取缓存图片')
    return data


class LocalMomentImages:
    def __init__(self, session, posts, unique_signatures, destination, include_cache):
        self.session = session
        self.destination = destination
        self.decoder = ImageDecoder(session)
        self.exact = {}
        self.candidates = defaultdict(dict)
        self.saved = {}
        self.gallery = {}
        images = [image for post in posts for image in post['images']]
        wanted = {image['md5'] for image in images if image['md5']}
        unique = unique_signatures.intersection(image_signature(image) for image in images)
        if not wanted and not unique and not include_cache:
            return
        cache = (session.account / 'cache').resolve()
        for index, source in enumerate(cache.glob('*/Sns/Img/**/*'), 1):
            session.check()
            if index % 100 == 0:
                session.progress(f'正在核对朋友圈图片缓存 · 已检查 {index:,} 个条目')
            try:
                if not source.is_file() or not source.resolve().is_relative_to(cache):
                    continue
                data = picture_bytes(self.decoder, source)
                digest = hashlib.md5(data).hexdigest()
                with Image.open(io.BytesIO(data)) as image:
                    signature = *image.size, len(data)
            except InterruptedError:
                raise
            except (ValueError, OSError, Image.DecompressionBombError):
                continue
            if include_cache:
                relative = self.save(data)
                self.gallery[relative] = None
                self.saved[digest] = relative
            if digest in wanted:
                self.exact.setdefault(digest, source)
            if signature in unique:
                self.candidates[signature].setdefault(digest, source)

    def attach(self, image):
        digest = image['md5']
        source = self.exact.get(digest)
        code = 'verified'
        status = '已校验的本地图片'
        if source is None:
            candidates = self.candidates.get(image_signature(image), {})
            if len(candidates) != 1:
                return {'status': '本地缓存中没有可匹配的图片', 'code': 'missing'}
            digest, source = next(iter(candidates.items()))
            code = 'candidate'
            status = '缓存候选图（宽高和字节数相同，待核对）'
        relative = self.saved.get(digest)
        if relative is None:
            try:
                data = picture_bytes(self.decoder, source)
                with Image.open(io.BytesIO(data)) as decoded:
                    decoded.load()
            except InterruptedError:
                raise
            except (ValueError, OSError, Image.DecompressionBombError):
                return {'status': '图片缓存无法读取', 'code': 'missing'}
            if hashlib.md5(data).hexdigest() != digest:
                return {'status': '图片缓存已变化', 'code': 'missing'}
            relative = self.saved[digest] = self.save(data)
        return {'status': status, 'code': code, 'file': relative}

    def save(self, data):
        name = hashlib.sha256(data).hexdigest() + '.' + kind(data)
        path = self.destination / name
        if not path.exists():
            path.write_bytes(data)
        return 'assets/' + name

    def write_gallery(self, folder):
        with (folder / '本机图片缓存.html').open('w', encoding='utf8') as page:
            page.write(PAGE + '<title>本机图片缓存</title><style>' + STYLE
                       + '</style><main><h1>本机图片缓存</h1>'
                       + f'<p>共 {len(self.gallery):,} 张。缓存文件没有对应的动态编号或发布时间。</p>')
            page.write('<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px">')
            for path in self.gallery:
                self.session.check()
                page.write(f'<a href="{path}"><img loading="lazy" src="{path}" alt="朋友圈缓存图片"></a>')
            page.write('</div></main></html>')


def clickable(url):
    if '\\' in url or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        return False
    try:
        parts = urlsplit(url)
        return parts.scheme in ('http', 'https') and bool(parts.hostname)
    except ValueError:
        return False


def write_post(post, markup, plain):
    esc = html.escape
    time = post['time'] or '发布时间未知'
    author = post['author']
    markup.write(f'<article><header><b>{esc(author)}</b> · {esc(time)}</header>')
    plain.write(f'{author}\n{time}\n')
    if post['text']:
        markup.write(f"<p>{esc(post['text'])}</p>")
        plain.write(post['text'] + '\n')
    for link in post['links']:
        label, address = esc(link['title'] or link['url']), esc(link['url'])
        if clickable(link['url']):
            markup.write(f'<p><a href="{address}" rel="noreferrer noopener" target="_blank">{label}</a></p>')
        else:
            markup.write(f'<p>{label}<br>{address}</p>')
        plain.write(f"{link['title']} {link['url']}\n".lstrip())
        if link['description']:
            markup.write(f"<p>{esc(link['description'])}</p>")
            plain.write(link['description'] + '\n')
    for image in post['images']:
        label = '链接封面' if image['role'] == 'cover' else '图片'
        caption = f"{label} · {image['status']}"
        relative = image.get('file', '')
        if relative:
            path = esc(relative)
            picture = f'<a href="{path}"><img loading="lazy" src="{path}" alt="{label}"></a>'
            if image['code'] == 'candidate':
                markup.write(f'<details><summary>{esc(caption)}</summary>{picture}</details>')
            else:
                markup.write(f'<p>{picture}<br><small>{esc(caption)}</small></p>')
        else:
            markup.write(f'<p><small>{esc(caption)}</small></p>')
        plain.write(f'[{caption}] {relative}'.rstrip() + '\n')
    if post.get('warning'):
        markup.write(f"<p><small>{esc(post['warning'])}</small></p>")
        plain.write(post['warning'] + '\n')
    markup.write('</article>\n')
    plain.write('\n')


def export_moments(session, directory, start=None, end=None, include_images=True, include_cache=False,
                   *, timeline=None, keyword=''):
    target = Path(directory) / ('拾聊朋友圈-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S')
                                + '-' + uuid.uuid4().hex[:6])
    staging = target.with_name(target.name + '.未完成')
    staging.mkdir(parents=True)
    summary_file = staging / '导出统计.json'
    summary = {'state': 'incomplete', 'moments': 0, 'links': 0, 'verified_images': 0,
               'candidate_images': 0, 'unavailable_images': 0, 'parse_warnings': 0,
               'without_time_excluded': 0, 'include_images': include_images,
               'include_cache': include_cache, 'cached_images': 0,
               'start': start, 'end_exclusive': end, 'keyword': keyword}
    try:
        session.progress('正在整理本地朋友圈…')
        if timeline is None:
            timeline = load_moments(session)
        posts, summary['without_time_excluded'] = filter_dates(session, timeline.search(keyword), start, end)
        assets = staging / 'assets'
        assets.mkdir()
        images = None
        if include_images or include_cache:
            images = LocalMomentImages(session, posts if include_images else [],
                                       timeline.unique_signatures, assets, include_cache)
        if include_cache:
            images.write_gallery(staging)
            summary['cached_images'] = len(images.gallery)
        with ((staging / '朋友圈.html').open('w', encoding='utf8') as markup,
              (staging / '朋友圈.txt').open('w', encoding='utf8') as plain,
              (staging / '朋友圈.json').open('w', encoding='utf8') as records):
            markup.write(PAGE + '<title>朋友圈 · 拾聊</title><style>' + STYLE + '</style>'
                         '<main><h1>朋友圈</h1>')
            if include_cache:
                markup.write('<p><a href="本机图片缓存.html">浏览本机图片缓存'
                             f"（{summary['cached_images']:,} 张）</a></p>")
            records.write('[\n')
            try:
                for index, source in enumerate(posts):
                    session.check()
                    if index and index % 200 == 0:
                        session.progress(f'正在导出朋友圈 · {index:,}/{len(posts):,} 条')
                    post = dict(source, images=[dict(image) for image in source['images']])
                    for image in post['images']:
                        if not include_images:
                            image.update(status='未启用图片导出', code='disabled')
                            continue
                        image.update(images.attach(image))
                        key = {'verified': 'verified_images', 'candidate': 'candidate_images',
                               'missing': 'unavailable_images'}[image['code']]
                        summary[key] += 1
                    write_post(post, markup, plain)
                    if index:
                        records.write(',\n')
                    json.dump(post, records, ensure_ascii=False)
                    summary['moments'] += 1
                    summary['links'] += len(post['links'])
                    summary['parse_warnings'] += 'warning' in post
                if not posts:
                    markup.write('<p>所选范围内没有本地朋友圈记录。</p>')
            finally:
                markup.write(f"<footer>已写入 {summary['moments']:,} 条</footer></main></html>")
                records.write('\n]\n')
        session.check()
        summary['state'] = 'complete'
        summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
        staging.rename(target)
        return target, summary
    except BaseException as exc:
        summary['state'] = 'incomplete'
        summary['reason'] = 'cancelled' if isinstance(exc, InterruptedError) else 'failed'
        summary['error'] = type(exc).__name__ + '：' + str(exc)
        summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
        raise
