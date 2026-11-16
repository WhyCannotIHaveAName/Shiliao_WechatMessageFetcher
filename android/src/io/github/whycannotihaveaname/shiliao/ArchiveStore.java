package io.github.whycannotihaveaname.shiliao;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.util.JsonReader;
import android.util.JsonToken;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.zip.ZipEntry;
import java.util.zip.ZipException;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

final class ArchiveStore extends SQLiteOpenHelper {
    static final int CHAT = 0;
    static final int MOMENTS = 1;
    static final int PAGE_SIZE = 32;
    static final long MAX_PHOTO = 64L * 1024 * 1024;
    private static final long MAX_IMPORT = 8L * 1024 * 1024 * 1024;
    private static final long MAX_SEGMENT = 768L * 1024 * 1024;
    private static final String MOMENTS_JSON = "\u670b\u53cb\u5708.json";
    private static final String CHAT_JSON = "\u804a\u5929\u8bb0\u5f55.json";
    private static final String GALLERY = "\u672c\u673a\u56fe\u7247\u7f13\u5b58.html";
    private final File sourceDir;
    private final File photoDir;
    private final Context context;

    ArchiveStore(Context context) {
        this(context, "archives.db", context.getFilesDir());
    }

    ArchiveStore(Context context, String database, File files) {
        super(context, database, null, 1);
        setWriteAheadLoggingEnabled(true);
        this.context = context.getApplicationContext();
        sourceDir = new File(files, "archives");
        photoDir = new File(files, "photos");
    }

    @Override public void onConfigure(SQLiteDatabase db) {
        db.setForeignKeyConstraintsEnabled(true);
    }

    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE archives (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                + "kind INTEGER NOT NULL, source TEXT, prefix TEXT NOT NULL, is_zip INTEGER NOT NULL, "
                + "has_gallery INTEGER NOT NULL, size INTEGER NOT NULL DEFAULT 0)");
        db.execSQL("CREATE TABLE records (archive INTEGER NOT NULL REFERENCES archives(id) ON DELETE CASCADE, "
                + "ordinal INTEGER NOT NULL, stamp INTEGER, body TEXT NOT NULL, search TEXT NOT NULL, "
                + "PRIMARY KEY(archive,ordinal))");
    }

    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        throw new IllegalStateException("Unknown archive schema version");
    }

    static final class Dataset {
        final long id;
        final String name;
        final int kind;
        final String source;
        final String prefix;
        final boolean zipped;
        final boolean gallery;
        final int size;

        Dataset(Cursor row) {
            id = row.getLong(0);
            name = row.getString(1);
            kind = row.getInt(2);
            source = row.getString(3);
            prefix = row.getString(4);
            zipped = row.getInt(5) != 0;
            gallery = row.getInt(6) != 0;
            size = row.getInt(7);
        }
    }

    static final class Filter {
        final String keyword;
        final Long start;
        final Long end;

        Filter(String keyword, Long start, Long end) {
            this.keyword = keyword.trim();
            this.start = start;
            this.end = end;
        }
    }

    static final class ImportResult {
        final int datasets;
        final int records;
        final int kind;
        final long selection;

        ImportResult(int datasets, int records, int kind, long selection) {
            this.datasets = datasets;
            this.records = records;
            this.kind = kind;
            this.selection = selection;
        }
    }

    List<Dataset> datasets(int kind) {
        List<Dataset> list = new ArrayList<>();
        try (Cursor rows = getReadableDatabase().rawQuery(
                "SELECT id,name,kind,source,prefix,is_zip,has_gallery,size FROM archives "
                + "WHERE kind=? ORDER BY id DESC", new String[]{Integer.toString(kind)})) {
            while (rows.moveToNext()) list.add(new Dataset(rows));
        }
        return list;
    }

    Dataset dataset(long id) {
        try (Cursor rows = getReadableDatabase().rawQuery(
                "SELECT id,name,kind,source,prefix,is_zip,has_gallery,size FROM archives WHERE id=?",
                new String[]{Long.toString(id)})) {
            return rows.moveToFirst() ? new Dataset(rows) : null;
        }
    }

    ImportResult importFile(InputStream input, String name) throws IOException, JSONException {
        ensureDirectory(sourceDir);
        File source = new File(sourceDir, UUID.randomUUID() + ".archive");
        SQLiteDatabase db = getWritableDatabase();
        boolean complete = false;
        try {
            try (FileOutputStream output = new FileOutputStream(source)) {
                copy(input, output, MAX_IMPORT);
            }
            boolean zipped;
            try (FileInputStream probe = new FileInputStream(source)) {
                zipped = probe.read() == 'P' && probe.read() == 'K';
            }
            int datasets = 0, total = 0, firstKind = CHAT;
            long firstId = 0;
            db.beginTransaction();
            try {
                if (zipped) {
                    try (ZipFile archive = openZip(source)) {
                        Enumeration<? extends ZipEntry> entries = archive.entries();
                        Set<String> seen = new HashSet<>();
                        while (entries.hasMoreElements()) {
                            ZipEntry entry = entries.nextElement();
                            String path = entry.getName();
                            int slash = path.lastIndexOf('/');
                            String leaf = path.substring(slash + 1);
                            int kind = MOMENTS_JSON.equals(leaf) ? MOMENTS : CHAT_JSON.equals(leaf) ? CHAT : -1;
                            if (entry.isDirectory() || kind < 0 || !validZipPath(path)) continue;
                            if (!seen.add(path)) throw new IOException("ZIP 内有重复的记录文件");
                            if (entry.getSize() > MAX_SEGMENT) throw new IOException("单个记录文件过大");
                            String prefix = path.substring(0, slash + 1);
                            String folder = prefix.isEmpty() ? "" : prefix.substring(0, prefix.length() - 1);
                            String title = kind == MOMENTS ? stripExt(name)
                                    : folder.substring(folder.lastIndexOf('/') + 1).replaceFirst("-[a-fA-F0-9]{10}$", "");
                            boolean gallery = kind == MOMENTS && archive.getEntry(prefix + GALLERY) != null;
                            try (InputStream member = archive.getInputStream(entry)) {
                                Imported one = importSegment(db, member, source.getName(), prefix, true,
                                        gallery, kind, title);
                                if (datasets == 0) {
                                    firstId = one.id;
                                    firstKind = one.kind;
                                }
                                datasets++;
                                total = Math.addExact(total, one.size);
                            }
                        }
                    }
                    if (datasets == 0) throw new IOException("ZIP 内没有拾聊的聊天记录.json 或朋友圈.json");
                } else {
                    int kind = MOMENTS_JSON.equals(name) ? MOMENTS : CHAT_JSON.equals(name) ? CHAT : -1;
                    try (InputStream member = new FileInputStream(source)) {
                        Imported one = importSegment(db, member, source.getName(), "", false, false,
                                kind, stripExt(name));
                        datasets = 1;
                        total = one.size;
                        firstKind = one.kind;
                        firstId = one.id;
                    }
                }
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
            complete = true;
            return new ImportResult(datasets, total, firstKind, firstId);
        } finally {
            if (!complete || !referencedArchive(db, source.getName())) source.delete();
        }
    }

    private static boolean referencedArchive(SQLiteDatabase db, String name) {
        try (Cursor rows = db.rawQuery("SELECT 1 FROM archives WHERE source=? LIMIT 1", new String[]{name})) {
            return rows.moveToFirst();
        }
    }

    private static final class Imported {
        final long id;
        final int kind;
        final int size;
        Imported(long id, int kind, int size) { this.id = id; this.kind = kind; this.size = size; }
    }

    private Imported importSegment(SQLiteDatabase db, InputStream input, String source, String prefix,
                                   boolean zipped, boolean gallery, int hint, String name)
            throws IOException, JSONException {
        try (JsonReader json = new JsonReader(new InputStreamReader(
                new LimitedInput(input, MAX_SEGMENT), StandardCharsets.UTF_8))) {
            json.beginArray();
            JSONObject first = json.hasNext() ? readRecord(json) : null;
            int kind = first == null ? hint : inferKind(first);
            if (kind < 0 || hint >= 0 && kind != hint) throw new IOException("请选择拾聊导出的记录 JSON");
            String title = name.isEmpty() ? kind == CHAT ? "聊天记录" : "朋友圈" : name;
            long id = insertDataset(db, title, kind, zipped ? source : null, prefix, zipped, gallery);
            int ordinal = 0;
            if (first != null) {
                insertRecord(db, id, ordinal++, first, kind);
                while (json.hasNext()) {
                    JSONObject record = readRecord(json);
                    if (inferKind(record) != kind) throw new IOException("记录文件混入了不兼容的条目");
                    insertRecord(db, id, ordinal++, record, kind);
                }
            }
            json.endArray();
            if (json.peek() != JsonToken.END_DOCUMENT) throw new IOException("JSON 结尾不完整");
            updateCount(db, id, ordinal);
            return new Imported(id, kind, ordinal);
        } catch (IllegalStateException e) {
            throw new IOException("请选择拾聊导出的 ZIP 或 JSON 文件", e);
        }
    }

    private static int inferKind(JSONObject record) {
        if (record.has("id") && record.has("images") && record.has("author")) return MOMENTS;
        if (record.has("sender") && record.has("timestamp") && record.has("type_code")) return CHAT;
        return -1;
    }

    private static JSONObject readRecord(JsonReader json) throws IOException, JSONException {
        Object value = readValue(json, 0);
        if (!(value instanceof JSONObject)) throw new IOException("记录必须是 JSON 对象");
        return (JSONObject) value;
    }

    private static Object readValue(JsonReader input, int depth) throws IOException, JSONException {
        if (depth > 48) throw new IOException("JSON 嵌套过深");
        switch (input.peek()) {
            case BEGIN_ARRAY:
                JSONArray array = new JSONArray();
                input.beginArray();
                while (input.hasNext()) array.put(readValue(input, depth + 1));
                input.endArray();
                return array;
            case BEGIN_OBJECT:
                JSONObject object = new JSONObject();
                input.beginObject();
                while (input.hasNext()) object.put(input.nextName(), readValue(input, depth + 1));
                input.endObject();
                return object;
            case NULL:
                input.nextNull();
                return JSONObject.NULL;
            case BOOLEAN:
                return input.nextBoolean();
            case NUMBER:
                String number = input.nextString();
                try { return Long.valueOf(number); } catch (NumberFormatException ignored) { }
                try {
                    double decimal = Double.parseDouble(number);
                    return Double.isFinite(decimal) ? decimal : number;
                } catch (NumberFormatException e) { return number; }
            case STRING:
                return input.nextString();
            default:
                throw new IOException("无法识别的 JSON 字段");
        }
    }

    private static long insertDataset(SQLiteDatabase db, String name, int kind, String source, String prefix,
                                      boolean zipped, boolean gallery) {
        ContentValues values = new ContentValues();
        values.put("name", name);
        values.put("kind", kind);
        values.put("source", source);
        values.put("prefix", prefix);
        values.put("is_zip", zipped);
        values.put("has_gallery", gallery);
        return db.insertOrThrow("archives", null, values);
    }

    private static void insertRecord(SQLiteDatabase db, long id, int ordinal, JSONObject record, int kind)
            throws IOException {
        String body = record.toString();
        if (body.length() > 600_000) throw new IOException("有一条记录超出手机预览大小上限");
        ContentValues values = new ContentValues();
        values.put("archive", id);
        values.put("ordinal", ordinal);
        long stamp = record.optLong("timestamp", 0);
        if (stamp > 0) values.put("stamp", stamp);
        values.put("body", body);
        values.put("search", RecordView.searchText(record, kind).toLowerCase(Locale.ROOT));
        db.insertOrThrow("records", null, values);
    }

    private static void updateCount(SQLiteDatabase db, long id, int size) {
        ContentValues values = new ContentValues();
        values.put("size", size);
        db.update("archives", values, "id=?", new String[]{Long.toString(id)});
    }

    long collect(String author, String text, Long published, List<Uri> attachments)
            throws IOException, JSONException {
        ensureDirectory(photoDir);
        List<File> files = new ArrayList<>();
        boolean complete = false;
        try {
            JSONArray images = new JSONArray();
            for (Uri uri : attachments) {
                if (!"content".equals(uri.getScheme())) throw new IOException("请通过系统选择本机图片");
                File staging = File.createTempFile("photo-", ".partial", photoDir);
                files.add(staging);
                try (InputStream input = context.getContentResolver().openInputStream(uri);
                     OutputStream output = new FileOutputStream(staging)) {
                    if (input == null) throw new IOException("无法打开所选图片");
                    copy(input, output, MAX_PHOTO);
                }
                BitmapFactory.Options bounds = new BitmapFactory.Options();
                bounds.inJustDecodeBounds = true;
                BitmapFactory.decodeFile(staging.getAbsolutePath(), bounds);
                String extension = RecordView.extension(bounds.outMimeType);
                if (extension == null || bounds.outWidth <= 0 || bounds.outHeight <= 0) {
                    throw new IOException("请选择 JPEG、PNG、GIF、WebP 或 BMP 图片");
                }
                File photo = new File(photoDir, UUID.randomUUID() + "." + extension);
                if (!staging.renameTo(photo)) throw new IOException("照片保存失败");
                files.add(photo);
                images.put(new JSONObject().put("role", "photo").put("code", "verified")
                        .put("file", "assets/" + photo.getName()).put("status", "手机收藏时选取的图片"));
            }
            JSONObject item = new JSONObject()
                    .put("id", UUID.randomUUID().toString())
                    .put("author", author.isEmpty() ? "手机收藏" : author)
                    .put("username", "").put("text", text).put("links", RecordView.linksIn(text)).put("images", images)
                    .put("timestamp", published == null ? JSONObject.NULL : published)
                    .put("time", published == null ? JSONObject.NULL : formatTime(published))
                    .put("collected_at", Instant.now().toString());
            SQLiteDatabase db = getWritableDatabase();
            db.beginTransaction();
            long id;
            try {
                int ordinal;
                try (Cursor rows = db.rawQuery("SELECT id,size FROM archives WHERE source IS NULL "
                        + "AND name='手机收藏' AND prefix='local/' AND kind=1 LIMIT 1", null)) {
                    if (rows.moveToFirst()) {
                        id = rows.getLong(0);
                        ordinal = rows.getInt(1);
                    } else {
                        id = insertDataset(db, "手机收藏", MOMENTS, null, "local/", false, false);
                        ordinal = 0;
                    }
                }
                insertRecord(db, id, ordinal, item, MOMENTS);
                updateCount(db, id, ordinal + 1);
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
            complete = true;
            return id;
        } finally {
            if (!complete) for (File file : files) file.delete();
        }
    }

    private static String formatTime(long stamp) {
        return DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(
                Instant.ofEpochSecond(stamp).atZone(ZoneId.systemDefault()));
    }

    Cursor records(Dataset data, Filter filter, int offset, int maximum) {
        StringBuilder sql = new StringBuilder("SELECT body FROM records");
        List<String> args = where(sql, data, filter);
        sql.append(data.kind == MOMENTS ? " ORDER BY stamp DESC,ordinal DESC" : " ORDER BY ordinal");
        if (maximum > 0) {
            sql.append(" LIMIT ? OFFSET ?");
            args.add(Integer.toString(maximum));
            args.add(Integer.toString(offset));
        }
        return getReadableDatabase().rawQuery(sql.toString(), args.toArray(new String[0]));
    }

    int count(Dataset data, Filter filter) {
        StringBuilder sql = new StringBuilder("SELECT count(*) FROM records");
        List<String> args = where(sql, data, filter);
        try (Cursor rows = getReadableDatabase().rawQuery(sql.toString(), args.toArray(new String[0]))) {
            return rows.moveToFirst() ? rows.getInt(0) : 0;
        }
    }

    private static List<String> where(StringBuilder sql, Dataset data, Filter filter) {
        List<String> args = new ArrayList<>();
        sql.append(" WHERE archive=?");
        args.add(Long.toString(data.id));
        if (!filter.keyword.isEmpty()) {
            sql.append(" AND instr(search,?)>0");
            args.add(filter.keyword.toLowerCase(Locale.ROOT));
        }
        if (filter.start != null) {
            sql.append(" AND stamp>=?");
            args.add(filter.start.toString());
        }
        if (filter.end != null) {
            sql.append(" AND stamp<?");
            args.add(filter.end.toString());
        }
        return args;
    }

    List<String> gallery(Dataset dataset) throws IOException {
        if (!dataset.gallery || !dataset.zipped) return Collections.emptyList();
        Set<String> images = new LinkedHashSet<>();
        try (ZipFile archive = openZip(new File(sourceDir, dataset.source))) {
            Enumeration<? extends ZipEntry> entries = archive.entries();
            while (entries.hasMoreElements()) {
                ZipEntry member = entries.nextElement();
                String name = member.getName();
                if (!member.isDirectory() && name.startsWith(dataset.prefix)) {
                    String local = name.substring(dataset.prefix.length());
                    if (validAsset(local) && RecordView.mime(local) != null && member.getSize() <= MAX_PHOTO) {
                        images.add(local);
                    }
                }
            }
        }
        return new ArrayList<>(images);
    }

    InputStream asset(Dataset data, String name) throws IOException {
        if (!validAsset(name)) return null;
        if ("local/".equals(data.prefix)) {
            File photo = new File(photoDir, name.substring("assets/".length()));
            return photo.isFile() ? new FileInputStream(photo) : null;
        }
        if (!data.zipped) return null;
        ZipFile archive = openZip(new File(sourceDir, data.source));
        try {
            InputStream item = readMember(archive, data.prefix + name);
            if (item == null) {
                archive.close();
                return null;
            }
            return new FilterInputStream(item) {
                @Override public void close() throws IOException {
                    try { super.close(); } finally { archive.close(); }
                }
            };
        } catch (IOException | RuntimeException e) {
            archive.close();
            throw e;
        }
    }

    void copyAssets(Dataset data, Set<String> names, ZipOutputStream output) throws IOException {
        if (names.isEmpty()) return;
        try (ZipFile archive = data.zipped ? openZip(new File(sourceDir, data.source)) : null) {
            for (String name : names) {
                if (!validAsset(name)) continue;
                try (InputStream file = archive == null ? asset(data, name) : readMember(archive, data.prefix + name)) {
                    if (file == null) continue;
                    output.putNextEntry(new ZipEntry(name));
                    copy(file, output, MAX_PHOTO);
                    output.closeEntry();
                }
            }
        }
    }

    private static InputStream readMember(ZipFile archive, String name) throws IOException {
        ZipEntry item = archive.getEntry(name);
        if (item == null || item.isDirectory() || item.getSize() > MAX_PHOTO) return null;
        return new LimitedInput(archive.getInputStream(item), MAX_PHOTO);
    }

    static boolean validAsset(String path) {
        return path != null && path.matches("assets/[a-zA-Z0-9_-][a-zA-Z0-9_.-]{0,179}");
    }

    private static boolean validZipPath(String path) {
        if (path.startsWith("/") || path.contains("\\") || path.indexOf(':') >= 0) return false;
        for (String part : path.split("/", -1)) {
            if (part.isEmpty() || ".".equals(part) || "..".equals(part)) return false;
        }
        return true;
    }

    private static ZipFile openZip(File path) throws IOException {
        try { return new ZipFile(path); }
        catch (ZipException utf8Failure) { return new ZipFile(path, Charset.forName("GB18030")); }
    }

    private static String stripExt(String name) {
        return name.replaceFirst("(?i)\\.(json|zip)$", "");
    }

    private static void ensureDirectory(File directory) throws IOException {
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("无法创建本机档案目录");
    }

    static long copy(InputStream in, OutputStream out, long limit) throws IOException {
        long count = 0;
        byte[] buffer = new byte[64 * 1024];
        int size;
        while ((size = in.read(buffer)) != -1) {
            count += size;
            if (count > limit) throw new IOException("文件大小超过支持范围");
            out.write(buffer, 0, size);
        }
        return count;
    }

    private static class LimitedInput extends FilterInputStream {
        private final long limit;
        private long used;
        LimitedInput(InputStream source, long limit) { super(source); this.limit = limit; }
        @Override public int read() throws IOException {
            int value = in.read();
            if (value != -1) count(1);
            return value;
        }
        @Override public int read(byte[] buffer, int offset, int size) throws IOException {
            int result = in.read(buffer, offset, size);
            if (result > 0) count(result);
            return result;
        }
        private void count(int count) throws IOException {
            used += count;
            if (used > limit) throw new IOException("解包内容超出大小限制");
        }
    }
}
