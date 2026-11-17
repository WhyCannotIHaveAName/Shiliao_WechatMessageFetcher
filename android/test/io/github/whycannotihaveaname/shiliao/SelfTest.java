package io.github.whycannotihaveaname.shiliao;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.os.Bundle;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.Arrays;
import java.util.Collections;
import java.util.UUID;
import java.util.concurrent.CancellationException;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

public final class SelfTest extends Instrumentation {
    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            checkCore();
            checkLaunch();
            result.putString("stream", "\nPASS: ZIP/JSON import, media, comment opt-in and reply anchors, date/scope filtering, annual and private chat rankings, own words and emoji, Moments comparison, cancellation and native launch.\n");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable error) {
            result.putString("stream", android.util.Log.getStackTraceString(error));
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private void checkCore() throws Exception {
        Context target = getTargetContext();
        String name = "shiliao-test-" + UUID.randomUUID();
        File root = new File(target.getCacheDir(), name);
        require(root.mkdirs(), "test sandbox");
        ArchiveStore store = new ArchiveStore(target, name + ".db", root);
        File output = File.createTempFile("filtered-", ".zip", root);
        try {
            Bitmap pixel = Bitmap.createBitmap(8, 8, Bitmap.Config.ARGB_8888);
            pixel.eraseColor(Color.RED);
            ByteArrayOutputStream image = new ByteArrayOutputStream();
            pixel.compress(Bitmap.CompressFormat.PNG, 100, image);
            pixel.recycle();
            byte[] red = image.toByteArray();
            JSONObject photo = new JSONObject().put("file", "assets/photo.png").put("code", "verified");
            JSONObject item = moment("a", 1700000000, "<script>alert(1)</script> 藏书")
                    .put("links", new JSONArray().put(new JSONObject().put("url", "https://example.invalid")
                            .put("title", "MyLink")))
                    .put("images", new JSONArray().put(photo))
                    .put("comments", new JSONArray()
                            .put(new JSONObject().put("id", "c1").put("author", "甲")
                                    .put("text", "<b>原评论</b>").put("timestamp", 1700000020))
                            .put(new JSONObject().put("id", "c2").put("author", "乙")
                                    .put("text", "评论正文唯一标记").put("timestamp", 1700000030)
                                    .put("time", "2023-11-15T06:13:50+08:00").put("emoji_count", 2)
                                    .put("reply_to", new JSONObject().put("author", "甲").put("parent_id", "c1")))
                            .put(new JSONObject().put("id", "c3").put("text", "孤立回复")
                                    .put("reply_to", new JSONObject().put("author", "丙").put("comment64_id", "9876"))));
            JSONArray feed = new JSONArray().put(item).put(moment("b", 1700000001, "另一个动态"));
            JSONObject nested = new JSONObject().put("sender", "甲").put("text", "转发文本");
            JSONObject message = new JSONObject().put("sender", "乙").put("timestamp", 1700000000)
                    .put("type_code", 49).put("text", "聊天正文").put("forwarded", new JSONArray().put(nested));
            ByteArrayOutputStream fixture = new ByteArrayOutputStream();
            try (ZipOutputStream zip = new ZipOutputStream(fixture, StandardCharsets.UTF_8)) {
                add(zip, "测试/朋友圈.json", utf8(feed.toString()));
                add(zip, "测试/本机图片缓存.html", utf8("HTML is rendered from JSON instead."));
                add(zip, "测试/assets/photo.png", red);
                add(zip, "会话/聊天记录.json", utf8(new JSONArray().put(message).toString()));
            }
            ArchiveStore.ImportResult imported = store.importFile(new ByteArrayInputStream(fixture.toByteArray()), "示例.zip");
            require(imported.datasets == 2 && imported.records == 3, "archive count");
            ArchiveStore.Dataset moments = store.dataset(imported.selection);
            ArchiveStore.Dataset chat = store.datasets(ArchiveStore.CHAT).get(0);
            require(moments.kind == ArchiveStore.MOMENTS && moments.gallery, "gallery recognized");
            require(store.gallery(moments).equals(Collections.singletonList("assets/photo.png")), "gallery path");
            require(store.asset(moments, "assets/../photo.png") == null, "traversal blocked");
            try (InputStream asset = store.asset(moments, "assets/photo.png")) {
                require(asset != null && Arrays.equals(read(asset), red), "image retained");
            }
            require(store.count(chat, new ArchiveStore.Filter("转发文本", null, null)) == 1, "nested search");
            require(store.count(moments, new ArchiveStore.Filter("  mylink  ", null, null)) == 1, "link search");
            ArchiveStore.Filter range = new ArchiveStore.Filter("", 1700000000L, 1700000001L);
            require(store.count(moments, range) == 1, "exclusive date end");
            try (Cursor rows = store.records(moments, range, 0, 1)) {
                require(rows.moveToFirst() && new JSONObject(rows.getString(0)).getString("id").equals("a"), "record query");
            }
            require(RecordView.export(store, moments, range, output, false) == 1, "export count");
            try (ZipFile zip = new ZipFile(output)) {
                String html = new String(read(zip.getInputStream(zip.getEntry("朋友圈.html"))), StandardCharsets.UTF_8);
                require(html.contains("assets/photo.png") && html.contains("&lt;script&gt;")
                        && !html.contains("<script>") && !html.contains("另一个动态"), "escaped filtered HTML");
                require(Arrays.equals(read(zip.getInputStream(zip.getEntry("assets/photo.png"))), red), "export media");
                require(!html.contains("评论正文唯一标记")
                        && !new JSONArray(entry(zip, "朋友圈.json")).getJSONObject(0).has("comments")
                        && !entry(zip, "朋友圈.txt").contains("评论正文唯一标记"), "default export excludes comments in all formats");
            }
            require(RecordView.export(store, moments, range, output, true) == 1, "opted-in export count");
            try (ZipFile zip = new ZipFile(output)) {
                String html = entry(zip, "朋友圈.html"), txt = entry(zip, "朋友圈.txt");
                JSONArray saved = new JSONArray(entry(zip, "朋友圈.json")).getJSONObject(0).getJSONArray("comments");
                require(html.contains("id='post-0-comment-0'") && html.contains("href='#post-0-comment-0'")
                        && html.contains("原评论不可定位") && html.contains("&lt;b&gt;原评论&lt;/b&gt;")
                        && txt.contains("回复 甲（评论 c1）") && txt.contains("2023-11-15 06:13:50+08:00")
                        && txt.contains("2 个表情") && saved.toString().equals(item.getJSONArray("comments").toString()),
                        "reply display and comment timestamps preserved, including later comments");
                ArchiveStore.ImportResult repost = store.importFile(new java.io.FileInputStream(output), "再导入.zip");
                require(repost.records == 1, "comment export can be imported again");
            }
            ArchiveStore.ImportResult raw = store.importFile(new ByteArrayInputStream(utf8(feed.toString())), "照片.json");
            require(raw.records == 2 && store.asset(store.dataset(raw.selection), "assets/photo.png") == null,
                    "standalone JSON semantics");
            long collectedId = store.collect("示例作者", "主动收藏", null, Collections.emptyList());
            try (Cursor rows = store.records(store.dataset(collectedId), new ArchiveStore.Filter("", null, null), 0, 1)) {
                require(rows.moveToFirst(), "saved collection");
                JSONObject collected = new JSONObject(rows.getString(0));
                require(collected.isNull("timestamp") && collected.has("collected_at"), "publication time provenance");
            }
            int before = store.datasets(ArchiveStore.MOMENTS).size();
            ByteArrayOutputStream invalid = new ByteArrayOutputStream();
            try (ZipOutputStream zip = new ZipOutputStream(invalid, StandardCharsets.UTF_8)) {
                add(zip, "合法/朋友圈.json", utf8(feed.toString()));
                add(zip, "不完整/朋友圈.json", utf8("[{"));
            }
            try {
                store.importFile(new ByteArrayInputStream(invalid.toByteArray()), "不能导入.zip");
                throw new AssertionError("invalid archive accepted");
            } catch (IOException expected) { }
            require(store.datasets(ArchiveStore.MOMENTS).size() == before, "atomic import");
            require(MainActivity.date("2026-11-09", true).equals(MainActivity.date("2026-11-10", false)),
                    "date endpoint");
            checkStats(target, store);
        } finally {
            store.close();
            output.delete();
            target.deleteDatabase(name + ".db");
            File archives = new File(root, "archives");
            File[] fixtureFiles = archives.listFiles();
            if (fixtureFiles != null) for (File file : fixtureFiles) file.delete();
            archives.delete();
            new File(root, "photos").delete();
            root.delete();
        }
    }

    private void checkStats(Context target, ArchiveStore store) throws Exception {
        JSONArray amy = new JSONArray().put(chat("wxid_amy", "同名", 10, "2025-11-10T10:00:00+08:00", "天气"))
                .put(chat("wxid_me", "本人", 11, "2025-11-11T10:00:00+08:00", "天气预报 [呲牙][呲牙] https://example.invalid/不要计数"))
                .put(chat("wxid_me", "本人", 12, "2024-11-11T10:00:00+08:00", "今天 [捂脸]"))
                .put(chat("wxid_me", "本人", 13, "2025-11-12T10:00:00+08:00", "明天不计数").put("type_code", 10000));
        JSONArray bo = new JSONArray().put(chat("wxid_bo", "同名", 20, "2025-11-11T10:00:00+08:00", "中文 中文"))
                .put(chat("wxid_me", "本人", 21, "2025-11-11T10:00:00+08:00", "天气 [呲牙]"));
        JSONArray group = new JSONArray().put(chat("wxid_me", "本人", 30, "2025-11-11T10:00:00+08:00", "wxid_me:\n中文"))
                .put(chat("wxid_bo", "同名", 31, "2025-11-11T10:00:00+08:00", "朋友 [捂脸]"));
        JSONArray posts = new JSONArray().put(moment("s1", 1762740000, "甲").put("username", "wxid_amy"))
                .put(moment("s2", 1762741000, "乙").put("username", "wxid_amy"))
                .put(moment("s3", 1762742000, "丙").put("username", "wxid_bo").put("author", "另一个人"))
                .put(moment("s4", 1762743000, "丁").put("timestamp", JSONObject.NULL).put("username", "wxid_bo"));
        ByteArrayOutputStream fixture = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(fixture, StandardCharsets.UTF_8)) {
            add(zip, "动态/朋友圈.json", utf8(posts.toString()));
            add(zip, "会话/同名-099b49109d/聊天记录.json", utf8(amy.toString()));
            add(zip, "会话/同名-c6f6453187/聊天记录.json", utf8(bo.toString()));
            add(zip, "会话/群聊-141611b52b/聊天记录.json", utf8(group.toString()));
            add(zip, "会话/重叠-099b49109d/聊天记录.json", utf8(new JSONArray().put(amy.get(1)).toString()));
        }
        ArchiveStore.ImportResult imported = store.importFile(new ByteArrayInputStream(fixture.toByteArray()), "统计示例.zip");
        java.util.Set<Long> chatIds = new java.util.HashSet<>();
        for (ArchiveStore.Dataset data : store.datasets(ArchiveStore.CHAT)) if (data.id > imported.selection) chatIds.add(data.id);
        FunStats.Options options = new FunStats.Options(true, true, true, "", "", "wxid_me", chatIds,
                Collections.singleton(imported.selection));
        FunStats.Result stats = FunStats.collect(target, store, options, () -> false, n -> { });
        require(stats.chats == 7 && stats.privateMessages == 5 && stats.unidentifiedChats == 2
                        && stats.people.get("wxid_amy") == 3 && stats.people.get("wxid_bo") == 2,
                "private conversations verified with PC archive folder IDs; groups and duplicate messages excluded");
        require(stats.annual.get(2025).get("wxid_amy") == 2 && stats.annual.get(2024).get("wxid_amy") == 1,
                "calendar years");
        require(stats.ownTexts == 4 && stats.emoji.get("[呲牙]") == 3 && stats.emoji.get("[捂脸]") == 1
                        && stats.ownWords.containsKey("天气") && stats.words.get("中文") == 3
                        && !stats.chars.containsKey("呲") && !stats.chars.containsKey("计") && !stats.words.containsKey("wxid"),
                "dictionary words, verified senders, group prefix and exclusion of links or emoji from lexical counts");
        require(stats.moments == 4 && stats.posters.get("wxid_amy") == 2 && stats.posters.get("wxid_bo") == 2,
                "Moments counts by author identity");
        String html = StatsView.render(options, stats);
        require(html.contains("2025") && html.contains("2 人并列") && html.contains("同名 · wxid_amy")
                && html.contains("width:100%") && html.contains("朋友圈发帖排行") && html.contains("[呲牙]"),
                "rendered cards, tie and bars");
        FunStats.Options only2025 = new FunStats.Options(true, true, false, "2025-01-01", "2025-12-31", "",
                chatIds, Collections.singleton(imported.selection));
        FunStats.Result filtered = FunStats.collect(target, store, only2025, () -> false, n -> { });
        require(filtered.chats == 6 && filtered.moments == 3 && !filtered.annual.containsKey(2024)
                && filtered.texts == 0 && filtered.ownTexts == 0, "date scope omits unknown timestamps and skips lexical work");
        try {
            FunStats.collect(target, store, options, () -> true, n -> { });
            throw new AssertionError("stats cancellation ignored");
        } catch (CancellationException expected) { }
    }

    private static JSONObject chat(String username, String name, long id, String time, String body) throws Exception {
        return new JSONObject().put("sender_username", username).put("sender", name).put("server_id", id)
                .put("type_code", 1).put("text", body).put("time", time)
                .put("timestamp", OffsetDateTime.parse(time).toEpochSecond());
    }

    private void checkLaunch() {
        Intent open = new Intent(Intent.ACTION_MAIN);
        open.setClassName(getTargetContext(), MainActivity.class.getName());
        open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Activity activity = startActivitySync(open);
        waitForIdleSync();
        require(activity instanceof MainActivity && !activity.isFinishing(), "Android activity launches");
        runOnMainSync(activity::finish);
    }

    private static JSONObject moment(String id, long timestamp, String body) throws Exception {
        return new JSONObject().put("id", id).put("timestamp", timestamp)
                .put("author", "发布者").put("text", body).put("images", new JSONArray());
    }

    private static void add(ZipOutputStream zip, String path, byte[] value) throws Exception {
        zip.putNextEntry(new ZipEntry(path));
        zip.write(value);
        zip.closeEntry();
    }

    private static String entry(ZipFile zip, String name) throws IOException {
        return new String(read(zip.getInputStream(zip.getEntry(name))), StandardCharsets.UTF_8);
    }

    private static byte[] read(InputStream input) throws IOException {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            ArchiveStore.copy(stream, output, 1024 * 1024);
            return output.toByteArray();
        }
    }

    private static byte[] utf8(String input) { return input.getBytes(StandardCharsets.UTF_8); }
    private static void require(boolean condition, String note) {
        if (!condition) throw new AssertionError(note);
    }
}
