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
import java.util.Arrays;
import java.util.Collections;
import java.util.UUID;
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
            result.putString("stream", "\nPASS: ZIP and JSON import, Unicode search, date boundaries, media, re-export, collection, bad ZIP, native launch.\n");
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
                    .put("images", new JSONArray().put(photo));
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
            require(RecordView.export(store, moments, range, output) == 1, "export count");
            try (ZipFile zip = new ZipFile(output)) {
                String html = new String(read(zip.getInputStream(zip.getEntry("朋友圈.html"))), StandardCharsets.UTF_8);
                require(html.contains("assets/photo.png") && html.contains("&lt;script&gt;")
                        && !html.contains("<script>") && !html.contains("另一个动态"), "escaped filtered HTML");
                require(Arrays.equals(read(zip.getInputStream(zip.getEntry("assets/photo.png"))), red), "export media");
                require(zip.getEntry("朋友圈.txt") != null && zip.getEntry("朋友圈.json") != null, "export formats");
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
