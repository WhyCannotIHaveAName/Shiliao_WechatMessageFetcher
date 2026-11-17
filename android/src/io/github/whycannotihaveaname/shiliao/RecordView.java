package io.github.whycannotihaveaname.shiliao;

import android.database.Cursor;
import android.net.Uri;
import android.text.TextUtils;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

final class RecordView {
    private static final Pattern URL = Pattern.compile("(?i)https?://[^\\s<>\"'，。！？]+");
    private static final String PAGE = "<!doctype html><html lang=zh-CN><meta charset=utf-8>"
            + "<meta name=viewport content='width=device-width,initial-scale=1'>"
            + "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; img-src 'self' https://archive.shiliao.invalid; style-src 'unsafe-inline'; base-uri 'none'\">"
            + "<style>*{box-sizing:border-box}body{margin:0;background:#f8f5ef;color:#29251f;"
            + "font:16px/1.6 system-ui,sans-serif}main{max-width:750px;margin:auto;padding:0 12px 16px}"
            + "article,.empty{background:#fffdf9;border:1px solid #e5ddd0;border-radius:14px;"
            + "padding:13px 15px;margin:10px 0}header{display:flex;align-items:baseline;flex-wrap:wrap;"
            + "gap:2px 10px}b{font-size:15px}time,small{font-size:12px;color:#6b5e51}"
            + "p{margin:7px 0;overflow-wrap:anywhere;white-space:pre-wrap}"
            + "img{display:block;max-width:100%;max-height:520px;border-radius:9px;margin:7px 0}"
            + "a,summary{color:#974530}blockquote{margin:8px 0;border-left:2px solid #d8b29e;padding-left:10px}"
            + ".replies{border-top:1px solid #e5ddd0;padding-top:8px}"
            + ".comment{padding:9px;margin-top:8px;border-radius:8px;background:#f8f1e8;overflow-wrap:anywhere}"
            + ".comment:target{outline:2px solid #974530}"
            + "details{margin-top:8px}h1{font-size:23px;margin:16px 0}footer{font-size:13px;color:#6b5e51}"
            + "</style>";
    private RecordView() { }

    static String searchText(JSONObject record, int kind) {
        StringBuilder result = new StringBuilder();
        appendSearch(record, kind, result);
        return result.toString();
    }

    private static void appendSearch(JSONObject record, int kind, StringBuilder result) {
        for (String name : kind == ArchiveStore.CHAT
                ? new String[]{"sender", "sender_username", "text"}
                : new String[]{"author", "username", "text"}) {
            result.append(str(record, name)).append('\n');
        }
        JSONArray links = record.optJSONArray("links");
        if (links != null) for (int i = 0; i < links.length(); i++) {
            JSONObject link = links.optJSONObject(i);
            if (link != null) for (String key : new String[]{"title", "description", "url"}) {
                result.append(str(link, key)).append('\n');
            }
        }
        JSONArray children = record.optJSONArray("forwarded");
        if (children != null) for (int i = 0; i < children.length(); i++) {
            JSONObject child = children.optJSONObject(i);
            if (child != null) appendSearch(child, kind, result);
        }
    }

    static String renderPage(String title, String notice, List<JSONObject> records, int kind, boolean pictures) {
        StringBuilder html = new StringBuilder(open(title));
        if (!notice.isEmpty()) html.append("<p class=empty>").append(esc(notice)).append("</p>");
        for (int i = 0; i < records.size(); i++) {
            html.append(recordHtml(records.get(i), kind, pictures, false, i, true));
        }
        return html.append("</main></html>").toString();
    }

    static String renderGallery(List<String> images, int offset) {
        StringBuilder html = new StringBuilder(open("本机缓存图册"));
        if (images.isEmpty()) html.append("<p class=empty>图库里没有可预览的图片。</p>");
        for (int i = 0; i < images.size(); i++) {
            html.append("<article><small>缓存图 ").append(offset + i + 1).append("</small><img loading=lazy src='")
                    .append(esc(images.get(i))).append("' alt='缓存图片'></article>");
        }
        return html.append("</main></html>").toString();
    }

    static String open(String title) {
        return PAGE + "<title>" + esc(title) + "</title><main>";
    }

    private static String recordHtml(JSONObject item, int kind, boolean pictures, boolean nested,
                                     int ordinal, boolean includeComments) {
        String tag = nested ? "blockquote" : "article";
        String name = str(item, kind == ArchiveStore.CHAT ? "sender" : "author");
        String time = str(item, "time");
        if (time.isEmpty()) time = kind == ArchiveStore.CHAT ? "消息时间未知" : "发布时间未知";
        StringBuilder html = new StringBuilder("<").append(tag).append("><header><b>")
                .append(esc(name)).append("</b><time>").append(esc(time.replace('T', ' ')))
                .append("</time></header>");
        String body = str(item, "text");
        if (!body.isEmpty()) html.append("<p>").append(linkify(body)).append("</p>");
        JSONArray links = item.optJSONArray("links");
        if (links != null) for (int i = 0; i < links.length(); i++) {
            JSONObject link = links.optJSONObject(i);
            if (link == null) continue;
            String url = str(link, "url");
            String title = str(link, "title");
            if (title.isEmpty() && !url.isEmpty() && body.contains(url)) continue;
            html.append("<p>").append(anchor(url, title.isEmpty() ? url : title)).append("</p>");
            String description = str(link, "description");
            if (!description.isEmpty()) html.append("<p><small>").append(esc(description)).append("</small></p>");
        }
        for (JSONObject photo : directPhotos(item)) {
            String path = str(photo, "file");
            String status = pictures || path.isEmpty() ? str(photo, "status") : "此 JSON 未包含图片文件";
            if (pictures && ArchiveStore.validAsset(path) && mime(path) != null) {
                if ("candidate".equals(str(photo, "code"))) {
                    html.append("<details><summary>").append(esc(status)).append("</summary>");
                    image(html, path);
                    html.append("</details>");
                } else {
                    image(html, path);
                    if (!status.isEmpty()) html.append("<small>").append(esc(status)).append("</small>");
                }
            } else if (!status.isEmpty()) {
                html.append("<p><small>").append(esc(status)).append("</small></p>");
            }
        }
        JSONArray children = item.optJSONArray("forwarded");
        if (children != null && children.length() > 0) {
            html.append("<details><summary>展开转发记录（").append(children.length()).append(" 条）</summary>");
            for (int i = 0; i < children.length(); i++) {
                JSONObject child = children.optJSONObject(i);
                if (child != null) html.append(recordHtml(child, kind, pictures, true, ordinal, false));
            }
            html.append("</details>");
        }
        if (kind == ArchiveStore.MOMENTS && !nested && includeComments) commentsHtml(html, item, ordinal);
        String warning = str(item, "warning");
        if (!warning.isEmpty()) html.append("<p><small>").append(esc(warning)).append("</small></p>");
        return html.append("</").append(tag).append(">").toString();
    }

    private static final DateTimeFormatter COMMENT_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss XXX");

    private static String commentTime(JSONObject comment) {
        String formatted = str(comment, "time");
        if (!formatted.isEmpty()) return formatted.replace('T', ' ');
        long stamp = comment.optLong("timestamp", 0);
        if (stamp > 0 && stamp <= 253402300799L) {
            return COMMENT_TIME.format(Instant.ofEpochSecond(stamp).atZone(ZoneId.systemDefault()));
        }
        return "评论时间未知";
    }

    private static String commentName(JSONObject comment) {
        String name = str(comment, "author");
        if (name.isEmpty()) name = str(comment, "username");
        return name.isEmpty() ? "未知用户" : name;
    }

    private static String attachmentNote(JSONObject comment) {
        int images = comment.optInt("image_count"), emojis = comment.optInt("emoji_count");
        if (images <= 0 && emojis <= 0) return "";
        return "评论附件：" + (images > 0 ? images + " 张图片" : "")
                + (images > 0 && emojis > 0 ? "、" : "") + (emojis > 0 ? emojis + " 个表情" : "");
    }

    private static String replyRef(JSONObject reply) {
        for (String field : new String[]{"parent_id", "comment64_id", "comment_id"}) {
            String id = str(reply, field);
            if (!id.isEmpty()) return id;
        }
        return "";
    }

    private static void commentsHtml(StringBuilder html, JSONObject item, int ordinal) {
        JSONArray comments = item.optJSONArray("comments");
        if (comments == null || comments.length() == 0) return;
        Map<String, Integer> parents = new HashMap<>();
        int count = 0;
        for (int i = 0; i < comments.length(); i++) {
            JSONObject comment = comments.optJSONObject(i);
            if (comment == null) continue;
            count++;
            String id = str(comment, "id");
            if (!id.isEmpty()) parents.merge(id, i, (a, b) -> -1);
        }
        if (count == 0) return;
        String anchor = "post-" + ordinal + "-comment-";
        html.append("<details class=replies><summary>评论（").append(count).append(" 条）</summary>");
        for (int i = 0; i < comments.length(); i++) {
            JSONObject comment = comments.optJSONObject(i);
            if (comment == null) continue;
            html.append("<section class=comment id='").append(anchor).append(i)
                    .append("'><header><b>").append(esc(commentName(comment))).append("</b>");
            JSONObject reply = comment.optJSONObject("reply_to");
            if (reply != null) {
                Integer parent = parents.get(str(reply, "parent_id"));
                String label = "回复 " + esc(commentName(reply));
                if (parent != null && parent >= 0 && parent != i) {
                    html.append("<a href='#").append(anchor).append(parent).append("'>").append(label).append("</a>");
                } else {
                    html.append("<small>").append(label);
                    if (!replyRef(reply).isEmpty()) html.append("（原评论不可定位）");
                    html.append("</small>");
                }
            }
            html.append("<time>").append(esc(commentTime(comment))).append("</time></header>");
            String body = str(comment, "text"), note = attachmentNote(comment);
            if (!body.isEmpty()) html.append("<p>").append(linkify(body)).append("</p>");
            if (!note.isEmpty()) html.append("<small>").append(esc(note)).append("</small>");
            html.append("</section>");
        }
        html.append("</details>");
    }

    private static void commentsText(BufferedWriter writer, JSONObject item) throws IOException {
        JSONArray comments = item.optJSONArray("comments");
        if (comments == null || comments.length() == 0) return;
        writer.write("\n评论\n");
        for (int i = 0; i < comments.length(); i++) {
            JSONObject comment = comments.optJSONObject(i);
            if (comment == null) continue;
            writer.write("  " + commentName(comment) + " · " + commentTime(comment));
            JSONObject reply = comment.optJSONObject("reply_to");
            if (reply != null) {
                writer.write(" · 回复 " + commentName(reply));
                String ref = replyRef(reply);
                if (!ref.isEmpty()) writer.write("（评论 " + ref + "）");
            }
            writer.write("\n    " + str(comment, "text").replace("\n", "\n    ") + "\n");
            String note = attachmentNote(comment);
            if (!note.isEmpty()) writer.write("    " + note + "\n");
        }
    }

    private static void image(StringBuilder html, String path) {
        html.append("<img loading=lazy src='").append(esc(path)).append("' alt='本机图片'>");
    }

    private static List<JSONObject> directPhotos(JSONObject item) {
        List<JSONObject> result = new java.util.ArrayList<>();
        JSONObject media = item.optJSONObject("media");
        if (media != null) result.add(media);
        JSONArray images = item.optJSONArray("images");
        if (images != null) for (int i = 0; i < images.length(); i++) {
            JSONObject image = images.optJSONObject(i);
            if (image != null) result.add(image);
        }
        return result;
    }

    static boolean http(String url) {
        if (url.isEmpty() || url.indexOf('\\') >= 0) return false;
        for (int i = 0; i < url.length(); i++) {
            char c = url.charAt(i);
            if (Character.isWhitespace(c) || Character.isISOControl(c)) return false;
        }
        Uri uri = Uri.parse(url);
        return ("https".equalsIgnoreCase(uri.getScheme()) || "http".equalsIgnoreCase(uri.getScheme()))
                && uri.getHost() != null && !uri.getHost().isEmpty();
    }

    private static String linkify(String input) {
        Matcher match = URL.matcher(input);
        StringBuilder result = new StringBuilder();
        int previous = 0;
        while (match.find()) {
            result.append(esc(input.substring(previous, match.start())));
            result.append(anchor(match.group(), match.group()));
            previous = match.end();
        }
        return result.append(esc(input.substring(previous))).toString();
    }

    static JSONArray linksIn(String input) throws JSONException {
        Matcher found = URL.matcher(input);
        Set<String> seen = new LinkedHashSet<>();
        JSONArray links = new JSONArray();
        while (found.find()) {
            String url = found.group();
            if (http(url) && seen.add(url)) links.put(new JSONObject().put("url", url));
        }
        return links;
    }

    private static String anchor(String url, String title) {
        if (!http(url)) return esc(title);
        return "<a rel='noreferrer noopener' href='" + esc(url) + "'>" + esc(title) + "</a>";
    }

    static String esc(String value) { return TextUtils.htmlEncode(value); }

    static String str(JSONObject item, String field) {
        return item.isNull(field) ? "" : item.optString(field, "");
    }

    static String mime(String asset) {
        int dot = asset.lastIndexOf('.');
        if (dot < 0) return null;
        switch (asset.substring(dot + 1).toLowerCase(Locale.ROOT)) {
            case "jpeg": case "jpg": return "image/jpeg";
            case "png": return "image/png";
            case "gif": return "image/gif";
            case "webp": return "image/webp";
            case "bmp": return "image/bmp";
            default: return null;
        }
    }

    static String extension(String mime) {
        if ("image/jpeg".equals(mime)) return "jpg";
        if ("image/png".equals(mime)) return "png";
        if ("image/gif".equals(mime)) return "gif";
        if ("image/webp".equals(mime)) return "webp";
        if ("image/bmp".equals(mime) || "image/x-ms-bmp".equals(mime)) return "bmp";
        return null;
    }

    static int export(ArchiveStore store, ArchiveStore.Dataset dataset, ArchiveStore.Filter filter, File file,
                      boolean includeComments)
            throws IOException, JSONException {
        boolean pictures = dataset.zipped || "local/".equals(dataset.prefix);
        String stem = dataset.kind == ArchiveStore.CHAT ? "聊天记录" : "朋友圈";
        Set<String> assets = new LinkedHashSet<>();
        try (ZipOutputStream zip = new ZipOutputStream(new FileOutputStream(file), StandardCharsets.UTF_8)) {
            BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(zip, StandardCharsets.UTF_8));
            zip.putNextEntry(new ZipEntry(stem + ".json"));
            writer.write("[\n");
            int count = 0;
            try (Cursor rows = store.records(dataset, filter, 0, 0)) {
                while (rows.moveToNext()) {
                    JSONObject item = new JSONObject(rows.getString(0));
                    if (dataset.kind == ArchiveStore.MOMENTS && !includeComments) item.remove("comments");
                    normalizeAssets(item, pictures, assets);
                    if (count++ > 0) writer.write(",\n");
                    writer.write(item.toString());
                }
            }
            writer.write("\n]\n");
            writer.flush();
            zip.closeEntry();
            zip.putNextEntry(new ZipEntry(stem + ".html"));
            writer.write(open(stem) + "<h1>" + stem + "</h1>");
            try (Cursor rows = store.records(dataset, filter, 0, 0)) {
                while (rows.moveToNext()) {
                    writer.write(recordHtml(new JSONObject(rows.getString(0)), dataset.kind, pictures, false,
                            rows.getPosition(), includeComments));
                }
            }
            writer.write("<footer>共 " + count + " 条</footer></main></html>");
            writer.flush();
            zip.closeEntry();
            zip.putNextEntry(new ZipEntry(stem + ".txt"));
            try (Cursor rows = store.records(dataset, filter, 0, 0)) {
                while (rows.moveToNext()) {
                    JSONObject item = new JSONObject(rows.getString(0));
                    String fields = searchText(item, dataset.kind);
                    writer.write(str(item, "time") + "\n" + fields.trim() + "\n");
                    if (dataset.kind == ArchiveStore.MOMENTS && includeComments) commentsText(writer, item);
                    writer.write("\n");
                }
            }
            writer.flush();
            zip.closeEntry();
            store.copyAssets(dataset, assets, zip);
            return count;
        }
    }

    private static void normalizeAssets(JSONObject item, boolean pictures, Set<String> assets) throws JSONException {
        for (JSONObject image : directPhotos(item)) {
            for (String key : new String[]{"file", "original_file"}) {
                String path = str(image, key);
                if (path.isEmpty()) continue;
                if (pictures && ArchiveStore.validAsset(path)) {
                    assets.add(path);
                } else {
                    image.put("source_" + key, path);
                    image.remove(key);
                    image.put("status", "未取得图片文件").put("code", "missing");
                }
            }
        }
        JSONArray children = item.optJSONArray("forwarded");
        if (children != null) for (int i = 0; i < children.length(); i++) {
            JSONObject child = children.optJSONObject(i);
            if (child != null) normalizeAssets(child, pictures, assets);
        }
    }
}
