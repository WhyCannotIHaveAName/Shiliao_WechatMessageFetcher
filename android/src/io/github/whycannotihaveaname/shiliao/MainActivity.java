package io.github.whycannotihaveaname.shiliao;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int OPEN = 11, SAVE = 12, PHOTO = 13;
    private static final String HOST = "archive.shiliao.invalid";
    private static final int PAPER = 0xfff8f5ef, CREAM = 0xfffffdf9, CLAY = 0xff974530,
            INK = 0xff29251f, MUTED = 0xff675d51, SELECTED = 0xfff0dfd4, LINE = 0xffe4dccf;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Workspace[] workspaces = {new Workspace(), new Workspace()};
    private ArchiveStore store;
    private int kind = ArchiveStore.MOMENTS;
    private Spinner archives;
    private List<ArchiveStore.Dataset> datasets = Collections.emptyList();
    private Button chatTab, momentTab, importButton, collectButton, dateButton, galleryButton,
            previous, next, exportButton;
    private EditText search;
    private WebView web;
    private TextView countView, status;
    private boolean busy, loadingWorkspace, destroyed;
    private int generation;
    private long documentVersion;
    private Runnable refresh;
    private volatile Document mounted;
    private PendingExport pendingExport;
    private CollectionDraft draft;

    private static final class Workspace {
        long archive = -1;
        String keyword = "", start = "", end = "";
        int offset;
        boolean gallery;
    }

    private static final class PendingExport {
        final long id;
        final ArchiveStore.Filter filter;
        PendingExport(long id, ArchiveStore.Filter filter) { this.id = id; this.filter = filter; }
    }

    private static final class Document {
        final ArchiveStore.Dataset dataset;
        final Uri page;
        final byte[] markup;
        Document(ArchiveStore.Dataset dataset, String html, long version) {
            this.dataset = dataset;
            String id = dataset == null ? "home" : Long.toString(dataset.id);
            page = Uri.parse("https://" + HOST + "/" + id + "/index.html?v=" + version);
            markup = html.getBytes(StandardCharsets.UTF_8);
        }
    }

    private static final class CollectionDraft {
        String author = "", text = "", time = "";
        final ArrayList<Uri> photos = new ArrayList<>();
    }

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        store = new ArchiveStore(this);
        if (saved != null) restore(saved);
        build();
        switchTo(kind);
        if (saved == null) acceptShare(getIntent());
    }

    @Override public void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putInt("kind", kind);
        for (int i = 0; i < workspaces.length; i++) {
            Workspace page = workspaces[i];
            out.putLong("id" + i, page.archive);
            out.putString("keyword" + i, page.keyword);
            out.putString("start" + i, page.start);
            out.putString("end" + i, page.end);
            out.putInt("offset" + i, page.offset);
            out.putBoolean("gallery" + i, page.gallery);
        }
        if (pendingExport != null) {
            out.putLong("destinationId", pendingExport.id);
            out.putString("destinationKeyword", pendingExport.filter.keyword);
            if (pendingExport.filter.start != null) out.putLong("destinationStart", pendingExport.filter.start);
            if (pendingExport.filter.end != null) out.putLong("destinationEnd", pendingExport.filter.end);
        }
        if (draft != null) {
            out.putString("draftAuthor", draft.author);
            out.putString("draftText", draft.text);
            out.putString("draftTime", draft.time);
            out.putParcelableArrayList("draftPhotos", draft.photos);
        }
    }

    private void restore(Bundle in) {
        kind = in.getInt("kind", ArchiveStore.MOMENTS);
        for (int i = 0; i < workspaces.length; i++) {
            Workspace page = workspaces[i];
            page.archive = in.getLong("id" + i, -1);
            page.keyword = in.getString("keyword" + i, "");
            page.start = in.getString("start" + i, "");
            page.end = in.getString("end" + i, "");
            page.offset = in.getInt("offset" + i);
            page.gallery = in.getBoolean("gallery" + i);
        }
        if (in.containsKey("destinationId")) {
            pendingExport = new PendingExport(in.getLong("destinationId"), new ArchiveStore.Filter(
                    in.getString("destinationKeyword", ""),
                    in.containsKey("destinationStart") ? in.getLong("destinationStart") : null,
                    in.containsKey("destinationEnd") ? in.getLong("destinationEnd") : null));
        }
        if (in.containsKey("draftAuthor")) {
            draft = new CollectionDraft();
            draft.author = in.getString("draftAuthor", "");
            draft.text = in.getString("draftText", "");
            draft.time = in.getString("draftTime", "");
            ArrayList<Uri> photos = in.getParcelableArrayList("draftPhotos");
            if (photos != null) draft.photos.addAll(photos);
        }
    }

    private void build() {
        LinearLayout root = column(PAPER);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars()
                        | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                root.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return insets;
            });
        }

        LinearLayout toolbar = row();
        toolbar.setPadding(dp(16), dp(5), dp(12), 0);
        TextView title = label("拾聊", 26, INK);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        toolbar.addView(title, weighted(0));
        importButton = button("导入", true, view -> openPicker());
        toolbar.addView(importButton);
        root.addView(toolbar, full());

        LinearLayout controls = column(PAPER);
        controls.setPadding(dp(12), 0, dp(12), 0);
        LinearLayout tabs = row();
        chatTab = button("聊天记录", false, view -> switchTo(ArchiveStore.CHAT));
        momentTab = button("朋友圈", false, view -> switchTo(ArchiveStore.MOMENTS));
        tabs.addView(chatTab, weighted(dp(3)));
        tabs.addView(momentTab, weighted(0));
        controls.addView(tabs, full());

        LinearLayout catalog = row();
        archives = new Spinner(this);
        archives.setContentDescription("已导入档案");
        archives.setBackgroundTintList(ColorStateList.valueOf(MUTED));
        LinearLayout.LayoutParams picker = new LinearLayout.LayoutParams(0, dp(42), 1);
        picker.setMarginEnd(dp(5));
        catalog.addView(archives, picker);
        collectButton = button("＋ 收藏", false, view -> showCollection(new CollectionDraft()));
        catalog.addView(collectButton);
        controls.addView(catalog, full());

        LinearLayout filterBar = row();
        search = new EditText(this);
        search.setSingleLine(true);
        search.setTextSize(14);
        search.setTextColor(INK);
        search.setHintTextColor(MUTED);
        search.setBackground(panel(CREAM, LINE));
        search.setPadding(dp(10), 0, dp(10), 0);
        LinearLayout.LayoutParams searchParams = new LinearLayout.LayoutParams(0, dp(42), 1);
        searchParams.setMarginEnd(dp(5));
        filterBar.addView(search, searchParams);
        dateButton = button("时间", false, view -> showDates());
        galleryButton = button("图册", false, view -> {
            Workspace page = workspaces[kind];
            page.gallery = !page.gallery;
            page.offset = 0;
            refreshControls();
            requestPage(0);
        });
        filterBar.addView(dateButton);
        filterBar.addView(galleryButton);
        controls.addView(filterBar, full());
        root.addView(controls, full());

        web = new WebView(this);
        web.setBackgroundColor(PAPER);
        web.getSettings().setJavaScriptEnabled(false);
        web.getSettings().setAllowFileAccess(false);
        web.getSettings().setAllowContentAccess(false);
        web.setWebViewClient(new WebViewClient() {
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                Document document = mounted;
                if (document != null && document.page.equals(uri)) {
                    return new WebResourceResponse("text/html", "UTF-8", new ByteArrayInputStream(document.markup));
                }
                List<String> pieces = uri.getPathSegments();
                ArchiveStore.Dataset data = document == null ? null : document.dataset;
                if (data != null && "https".equals(uri.getScheme()) && HOST.equals(uri.getHost())
                        && pieces.size() == 3 && Long.toString(data.id).equals(pieces.get(0))
                        && "assets".equals(pieces.get(1))) {
                    String name = "assets/" + pieces.get(2);
                    String mime = RecordView.mime(name);
                    if (mime != null) try {
                        InputStream image = store.asset(data, name);
                        if (image != null) return new WebResourceResponse(mime, null, image);
                    } catch (IOException ignored) { }
                }
                return new WebResourceResponse("text/plain", "UTF-8", 404, "Not found",
                        Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
            }

            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (request.isForMainFrame() && request.hasGesture() && !HOST.equals(uri.getHost())
                        && RecordView.http(uri.toString())) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
                    catch (ActivityNotFoundException e) { status.setText("没有可打开该链接的浏览器。"); }
                }
                return true;
            }
        });
        root.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));

        LinearLayout bottom = column(CREAM);
        bottom.setPadding(dp(10), dp(3), dp(10), dp(3));
        status = label("", 12, MUTED);
        status.setMaxLines(2);
        bottom.addView(status, full());
        LinearLayout actions = row();
        previous = button("上一页", false, view -> changePage(-1));
        next = button("下一页", false, view -> changePage(1));
        countView = label("", 12, MUTED);
        countView.setGravity(Gravity.CENTER);
        exportButton = button("导出 ZIP", true, view -> exportPicker());
        actions.addView(previous);
        actions.addView(countView, weighted(0));
        actions.addView(next);
        actions.addView(exportButton);
        bottom.addView(actions, full());
        root.addView(bottom, full());
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                        | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                controller.setSystemBarsAppearance(light, light);
            }
        }

        archives.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onNothingSelected(AdapterView<?> parent) { }
            @Override public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                if (loadingWorkspace || position >= datasets.size()) return;
                Workspace page = workspaces[kind];
                if (page.archive == datasets.get(position).id) return;
                page.archive = datasets.get(position).id;
                page.offset = 0;
                page.gallery = false;
                refreshControls();
                requestPage(0);
            }
        });
        search.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) { }
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {
                if (loadingWorkspace) return;
                Workspace page = workspaces[kind];
                page.keyword = s.toString();
                page.offset = 0;
                requestPage(200);
            }
            @Override public void afterTextChanged(Editable s) { }
        });
    }

    private void switchTo(int target) {
        loadingWorkspace = true;
        kind = target;
        Workspace page = workspaces[target];
        datasets = store.datasets(target);
        int selection = 0;
        ArrayList<String> labels = new ArrayList<>();
        for (int index = 0; index < datasets.size(); index++) {
            ArchiveStore.Dataset data = datasets.get(index);
            labels.add(data.name + "  ·  " + data.size + " 条");
            if (data.id == page.archive) selection = index;
        }
        if (datasets.isEmpty()) {
            labels.add("还没有档案");
            page.archive = -1;
        } else {
            long id = datasets.get(selection).id;
            if (page.archive != id) {
                page.offset = 0;
                page.gallery = false;
            }
            page.archive = id;
        }
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, labels);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        archives.setAdapter(adapter);
        archives.setSelection(selection);
        search.setText(page.keyword);
        search.setHint(target == ArchiveStore.CHAT ? "搜索发送者或消息" : "搜索正文、发布者或链接");
        paintButton(chatTab, false, target == ArchiveStore.CHAT);
        paintButton(momentTab, false, target == ArchiveStore.MOMENTS);
        loadingWorkspace = false;
        refreshControls();
        requestPage(0);
    }

    private ArchiveStore.Dataset selected() {
        long id = workspaces[kind].archive;
        for (ArchiveStore.Dataset item : datasets) if (item.id == id) return item;
        return null;
    }

    private void refreshControls() {
        Workspace page = workspaces[kind];
        ArchiveStore.Dataset item = selected();
        boolean gallery = item != null && item.gallery && page.gallery;
        search.setEnabled(item != null && !gallery);
        archives.setEnabled(item != null);
        dateButton.setEnabled(item != null && !gallery);
        dateButton.setText(page.start.isEmpty() && page.end.isEmpty() ? "时间" : "时间 ✓");
        galleryButton.setVisibility(item != null && item.gallery ? View.VISIBLE : View.GONE);
        galleryButton.setText(gallery ? "动态" : "图册");
        collectButton.setVisibility(kind == ArchiveStore.MOMENTS ? View.VISIBLE : View.GONE);
        collectButton.setEnabled(!busy);
        exportButton.setEnabled(item != null && !busy && !gallery);
        importButton.setEnabled(!busy);
    }

    private void requestPage(long delay) {
        final int serial = ++generation;
        if (refresh != null) main.removeCallbacks(refresh);
        final ArchiveStore.Dataset data = selected();
        if (data == null) {
            String message = kind == ArchiveStore.CHAT
                    ? "导入电脑拾聊导出的 ZIP 或聊天记录 JSON，即可在这里查找和浏览。"
                    : "导入电脑拾聊的朋友圈档案，或用「收藏」保存本机的文字和图片。";
            showPage(null, RecordView.renderPage("拾聊", message, Collections.emptyList(), kind, false));
            countView.setText("");
            previous.setEnabled(false);
            next.setEnabled(false);
            return;
        }
        final Workspace page = workspaces[kind];
        final ArchiveStore.Filter filter = filters(page);
        final boolean gallery = page.gallery && data.gallery;
        final int offset = page.offset;
        refresh = () -> executor.execute(() -> {
            try {
                final int total;
                final String html;
                if (gallery) {
                    List<String> images = store.gallery(data);
                    total = images.size();
                    int first = Math.min(offset, total);
                    html = RecordView.renderGallery(images.subList(first, Math.min(first + ArchiveStore.PAGE_SIZE, total)), first);
                } else {
                    total = store.count(data, filter);
                    List<JSONObject> records = new ArrayList<>();
                    try (Cursor cursor = store.records(data, filter, offset, ArchiveStore.PAGE_SIZE)) {
                        while (cursor.moveToNext()) records.add(new JSONObject(cursor.getString(0)));
                    }
                    String notice = records.isEmpty() ? "没有匹配的记录。" : "";
                    html = RecordView.renderPage(data.name, notice, records, data.kind,
                            data.zipped || "local/".equals(data.prefix));
                }
                main.post(() -> {
                    if (destroyed || serial != generation) return;
                    showPage(data, html);
                    countView.setText(total == 0 ? "0 条" : (offset + 1) + "–"
                            + Math.min(total, offset + ArchiveStore.PAGE_SIZE) + " / " + total);
                    previous.setEnabled(offset > 0);
                    next.setEnabled(offset + ArchiveStore.PAGE_SIZE < total);
                });
            } catch (Exception error) {
                main.post(() -> { if (!destroyed && serial == generation) showError("查看失败", error); });
            }
        });
        main.postDelayed(refresh, delay);
    }

    private void showPage(ArchiveStore.Dataset data, String html) {
        Document document = new Document(data, html, ++documentVersion);
        mounted = document;
        web.loadUrl(document.page.toString());
    }

    private void changePage(int direction) {
        Workspace page = workspaces[kind];
        page.offset = Math.max(0, page.offset + direction * ArchiveStore.PAGE_SIZE);
        previous.setEnabled(false);
        next.setEnabled(false);
        requestPage(0);
    }

    private void openPicker() {
        Intent intent = document(Intent.ACTION_OPEN_DOCUMENT, "*/*");
        intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{
                "application/zip", "application/x-zip-compressed", "application/json", "text/plain", "application/octet-stream"});
        startActivityForResult(intent, OPEN);
    }

    private void exportPicker() {
        ArchiveStore.Dataset data = selected();
        if (data == null || busy) return;
        pendingExport = new PendingExport(data.id, filters(workspaces[kind]));
        Intent intent = document(Intent.ACTION_CREATE_DOCUMENT, "application/zip");
        intent.putExtra(Intent.EXTRA_TITLE, data.kind == ArchiveStore.CHAT ? "拾聊聊天记录.zip" : "拾聊朋友圈.zip");
        startActivityForResult(intent, SAVE);
    }

    private static Intent document(String action, String type) {
        return new Intent(action).addCategory(Intent.CATEGORY_OPENABLE).setType(type);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent result) {
        super.onActivityResult(requestCode, resultCode, result);
        if (requestCode == PHOTO) {
            if (draft == null) return;
            if (resultCode == RESULT_OK && result != null) readPhotoSelection(result, draft.photos);
            showCollection(draft);
            return;
        }
        if (requestCode == SAVE) {
            PendingExport selection = pendingExport;
            pendingExport = null;
            if (resultCode == RESULT_OK && result != null && result.getData() != null && selection != null) {
                save(result.getData(), selection);
            }
        }
        if (requestCode == OPEN && resultCode == RESULT_OK && result != null && result.getData() != null) {
            load(result.getData());
        }
    }

    private void load(Uri uri) {
        if (busy) return;
        begin("正在导入本机档案…");
        executor.execute(() -> {
            try (InputStream bytes = getContentResolver().openInputStream(uri)) {
                if (bytes == null) throw new IOException("无法打开所选文件");
                ArchiveStore.ImportResult result = store.importFile(bytes, displayName(uri));
                main.post(() -> {
                    if (destroyed) return;
                    Workspace page = workspaces[result.kind];
                    page.archive = result.selection;
                    page.offset = 0;
                    page.gallery = false;
                    finish("已导入 " + result.datasets + " 份档案，共 " + result.records + " 条记录");
                    switchTo(result.kind);
                });
            } catch (Exception e) { postFailure("导入失败", e); }
        });
    }

    private String displayName(Uri uri) {
        try (Cursor meta = getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (meta != null && meta.moveToFirst() && meta.getString(0) != null) return meta.getString(0);
        }
        return "拾聊档案";
    }

    private void save(Uri destination, PendingExport selection) {
        begin("正在打包筛选结果…");
        executor.execute(() -> {
            File staged = null;
            try {
                ArchiveStore.Dataset dataset = store.dataset(selection.id);
                if (dataset == null) throw new IOException("原档案不可用");
                staged = File.createTempFile("export-", ".zip", getCacheDir());
                final int count = RecordView.export(store, dataset, selection.filter, staged);
                try (InputStream data = new FileInputStream(staged);
                     OutputStream target = getContentResolver().openOutputStream(destination, "wt")) {
                    if (target == null) throw new IOException("无法写入所选保存位置");
                    ArchiveStore.copy(data, target, Long.MAX_VALUE);
                }
                main.post(() -> { if (!destroyed) finish("已将 " + count + " 条记录保存为 ZIP"); });
            } catch (Exception e) { postFailure("导出失败", e); }
            finally { if (staged != null) staged.delete(); }
        });
    }

    private void showDates() {
        Workspace page = workspaces[kind];
        LinearLayout fields = dialogFields();
        EditText start = field(fields, "开始日期 · YYYY-MM-DD 或 YYYY-MM-DD-H", page.start, false);
        EditText end = field(fields, "结束日期 · YYYY-MM-DD 或 YYYY-MM-DD-H", page.end, false);
        String title = kind == ArchiveStore.CHAT ? "消息时间范围" : "发布时间范围";
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle(title).setView(fields)
                .setPositiveButton("应用", null)
                .setNeutralButton("全部时间", (view, which) -> applyDates(page, "", ""))
                .setNegativeButton("取消", null).create();
        dialog.setOnShowListener(view -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(button -> {
            String a = start.getText().toString().trim();
            String b = end.getText().toString().trim();
            try {
                Long lower = date(a, false), upper = date(b, true);
                if (lower != null && upper != null && lower >= upper) throw new IllegalArgumentException();
            } catch (RuntimeException e) {
                end.setError("请检查日期格式及开始、结束顺序");
                return;
            }
            dialog.dismiss();
            applyDates(page, a, b);
        }));
        dialog.show();
    }

    private void applyDates(Workspace page, String start, String end) {
        page.start = start;
        page.end = end;
        page.offset = 0;
        refreshControls();
        requestPage(0);
    }

    static Long date(String text, boolean end) {
        if (text.isEmpty()) return null;
        if (text.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}")) {
            LocalDate local = LocalDate.parse(text);
            return (end ? local.plusDays(1) : local).atStartOfDay(ZoneId.systemDefault()).toEpochSecond();
        }
        if (!text.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{1,2}")) throw new IllegalArgumentException();
        return LocalDate.parse(text.substring(0, 10)).atTime(Integer.parseInt(text.substring(11)), 0)
                .atZone(ZoneId.systemDefault()).toEpochSecond();
    }

    private static ArchiveStore.Filter filters(Workspace page) {
        return new ArchiveStore.Filter(page.keyword, date(page.start, false), date(page.end, true));
    }

    private void acceptShare(Intent intent) {
        if (!Intent.ACTION_SEND.equals(intent.getAction()) && !Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) return;
        CollectionDraft incoming = new CollectionDraft();
        CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (text != null) incoming.text = text.toString();
        readPhotoSelection(intent, incoming.photos);
        if (incoming.text.isEmpty() && incoming.photos.isEmpty()) {
            status.setText("这份分享没有可收藏的文字或图片。");
        } else {
            showCollection(incoming);
        }
    }

    private static void readPhotoSelection(Intent intent, ArrayList<Uri> into) {
        addPhoto(intent.getData(), into);
        ClipData clip = intent.getClipData();
        if (clip != null) for (int i = 0; i < clip.getItemCount(); i++) addPhoto(clip.getItemAt(i).getUri(), into);
        if (Intent.ACTION_SEND.equals(intent.getAction())) addPhoto(intent.getParcelableExtra(Intent.EXTRA_STREAM), into);
        if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
            ArrayList<Uri> extra = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (extra != null) for (Uri uri : extra) addPhoto(uri, into);
        }
    }

    private static void addPhoto(Uri uri, ArrayList<Uri> into) {
        if (uri != null && "content".equals(uri.getScheme()) && !into.contains(uri)) into.add(uri);
    }

    private void showCollection(CollectionDraft current) {
        draft = current;
        LinearLayout fields = dialogFields();
        EditText author = field(fields, "发布者（选填）", current.author, false);
        EditText body = field(fields, "正文或链接", current.text, true);
        EditText stamp = field(fields, "原发布时间（选填，YYYY-MM-DD HH:mm）", current.time, false);
        fields.addView(label("已选图片 " + current.photos.size() + " 张", 13, MUTED), full());
        ScrollView scroll = new ScrollView(this);
        scroll.addView(fields);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("收藏到本机").setView(scroll)
                .setPositiveButton("保存", null).setNeutralButton("选图片", null)
                .setNegativeButton("取消", (view, which) -> draft = null).create();
        dialog.setOnCancelListener(view -> draft = null);
        dialog.setOnShowListener(view -> {
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(button -> {
                readDraft(current, author, body, stamp);
                dialog.dismiss();
                Intent photos = document(Intent.ACTION_OPEN_DOCUMENT, "image/*");
                photos.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                startActivityForResult(photos, PHOTO);
            });
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(button -> {
                readDraft(current, author, body, stamp);
                if (current.text.isEmpty() && current.photos.isEmpty()) {
                    body.setError("填写文字或选择一张图片");
                    return;
                }
                Long published = null;
                if (!current.time.isEmpty()) try {
                    published = LocalDateTime.parse(current.time.replace(' ', 'T'), DateTimeFormatter.ISO_LOCAL_DATE_TIME)
                            .atZone(ZoneId.systemDefault()).toEpochSecond();
                    if (published <= 0) throw new IllegalArgumentException();
                } catch (RuntimeException e) {
                    stamp.setError("请输入类似 2026-11-16 19:30 的时间");
                    return;
                }
                dialog.dismiss();
                draft = null;
                collect(current, published);
            });
        });
        dialog.show();
    }

    private static void readDraft(CollectionDraft current, EditText author, EditText body, EditText stamp) {
        current.author = author.getText().toString().trim();
        current.text = body.getText().toString().trim();
        current.time = stamp.getText().toString().trim();
    }

    private void collect(CollectionDraft value, Long published) {
        begin("正在保存收藏…");
        executor.execute(() -> {
            try {
                long id = store.collect(value.author, value.text, published, value.photos);
                main.post(() -> {
                    if (destroyed) return;
                    Workspace page = workspaces[ArchiveStore.MOMENTS];
                    page.archive = id;
                    page.gallery = false;
                    page.offset = 0;
                    page.keyword = page.start = page.end = "";
                    finish("已保存到「手机收藏」");
                    switchTo(ArchiveStore.MOMENTS);
                });
            } catch (Exception error) { postFailure("收藏失败", error); }
        });
    }

    private void begin(String message) {
        busy = true;
        status.setText(message);
        refreshControls();
    }

    private void finish(String message) {
        busy = false;
        status.setText(message);
        refreshControls();
    }

    private void postFailure(String title, Exception error) {
        main.post(() -> { if (!destroyed) { finish(title); showError(title, error); } });
    }

    private void showError(String title, Exception error) {
        new AlertDialog.Builder(this).setTitle(title)
                .setMessage(error.getMessage() == null ? "请检查文件和可用空间。" : error.getMessage())
                .setPositiveButton("关闭", null).show();
    }

    private LinearLayout dialogFields() {
        LinearLayout fields = column(CREAM);
        fields.setPadding(dp(20), dp(8), dp(20), dp(4));
        return fields;
    }

    private EditText field(LinearLayout parent, String label, String value, boolean multiline) {
        TextView caption = label(label, 13, MUTED);
        parent.addView(caption, full());
        EditText editor = new EditText(this);
        editor.setTextSize(15);
        editor.setSingleLine(!multiline);
        if (multiline) editor.setMinLines(2);
        editor.setText(value);
        parent.addView(editor, full());
        return editor;
    }

    private LinearLayout column(int background) {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setBackgroundColor(background);
        return layout;
    }

    private LinearLayout row() {
        LinearLayout layout = new LinearLayout(this);
        layout.setGravity(Gravity.CENTER_VERTICAL);
        return layout;
    }

    private static LinearLayout.LayoutParams full() { return new LinearLayout.LayoutParams(-1, -2); }

    private LinearLayout.LayoutParams weighted(int marginEnd) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
        params.setMarginEnd(marginEnd);
        return params;
    }

    private TextView label(String value, int points, int color) {
        TextView label = new TextView(this);
        label.setText(value);
        label.setTextSize(points);
        label.setTextColor(color);
        return label;
    }

    private Button button(String label, boolean primary, View.OnClickListener action) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(13);
        button.setAllCaps(false);
        button.setMinHeight(dp(48));
        button.setMinimumWidth(0);
        button.setMinWidth(0);
        button.setStateListAnimator(null);
        button.setPadding(dp(9), 0, dp(9), 0);
        button.setOnClickListener(action);
        paintButton(button, primary, false);
        return button;
    }

    private void paintButton(Button button, boolean primary, boolean selected) {
        button.setBackground(panel(primary ? CLAY : selected ? SELECTED : CREAM, primary ? CLAY : LINE));
        button.setTextColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled}, new int[0]},
                new int[]{0xff9d8e80, primary ? Color.WHITE : INK}));
    }

    private GradientDrawable panel(int color, int stroke) {
        GradientDrawable panel = new GradientDrawable();
        panel.setColor(color);
        panel.setCornerRadius(dp(10));
        panel.setStroke(dp(1), stroke);
        return panel;
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }

    @Override public void onDestroy() {
        destroyed = true;
        if (refresh != null) main.removeCallbacks(refresh);
        web.destroy();
        executor.execute(store::close);
        executor.shutdown();
        super.onDestroy();
    }
}
