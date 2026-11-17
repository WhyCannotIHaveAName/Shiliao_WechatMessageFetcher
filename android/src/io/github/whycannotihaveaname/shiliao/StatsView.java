package io.github.whycannotihaveaname.shiliao;

import java.text.NumberFormat;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

final class StatsView {
    private static final String STYLE = "<style>"
            + ".hero{background:#f0dfd4;border-color:#e4c6b5;padding:18px}"
            + ".eyebrow{font-size:11px;color:#8b3d2b;font-weight:750;letter-spacing:.15em}"
            + ".hero h1{margin:4px 0;font-size:26px}.hero p{font-size:13px;margin:4px 0}"
            + ".tiles{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}"
            + ".metric{min-width:105px;flex:1;background:#fff8f2;border-radius:9px;padding:9px 11px}"
            + ".metric strong{display:block;font-size:21px;font-variant-numeric:tabular-nums}"
            + ".panel h2,.panel summary{font-size:17px;font-weight:700;margin:0 0 5px}"
            + ".panel p.note{font-size:13px;color:#675d51;margin:2px 0 9px}"
            + ".panel details{border-top:1px solid #e5ddd0;padding-top:8px}.track{height:9px;border-radius:8px;"
            + "background:#f0e9de;color:#a6533b;overflow:hidden;margin:3px 0 8px}"
            + ".track i{display:block;height:100%;border-radius:8px;background:currentColor}"
            + ".green .track{color:#626d53}.bar{display:flex;gap:8px;align-items:baseline;font-size:13px}"
            + ".bar span{flex:1;min-width:0;overflow-wrap:anywhere}.bar strong{font-variant-numeric:tabular-nums}"
            + ".year{display:flex;flex-wrap:wrap;gap:7px;align-items:baseline;padding:8px 0;border-bottom:1px solid #e5ddd0}"
            + ".year strong{color:#974530}.year span{overflow-wrap:anywhere}"
            + "</style>";

    private StatsView() { }

    static String intro() {
        return RecordView.open("趣味统计") + STYLE
                + "<article class=hero><small class=eyebrow>拾聊 · 档案回顾</small><h1>趣味统计</h1>"
                + "<p>谁是你的年度人物？最常打出的是哪个表情？<br>从手机里的档案回看这些小细节。</p></article>"
                + "<article class=panel><h2>从已导入的档案开始</h2>"
                + "<p class=note>点下方「生成统计」，查看年度人物、私聊排行、常用字词与朋友圈发帖排行。</p>"
                + "<p class=note>可在「范围与来源」选择档案、日期和本人账号。统计在手机上离线完成。</p></article>"
                + "</main></html>";
    }

    static String render(FunStats.Options o, FunStats.Result r) {
        StringBuilder h = new StringBuilder(RecordView.open("趣味统计")).append(STYLE)
                .append("<article class=hero><small class=eyebrow>拾聊 · 档案回顾</small><h1>趣味统计</h1><p>")
                .append(RecordView.esc(range(o))).append(" · 手机本地档案</p><div class=tiles>");
        if (o.chat) metric(h, "消息 · " + n(r.chatArchives) + " 份档案", r.chats);
        if (o.moments) metric(h, "动态 · " + n(r.momentArchives) + " 份档案", r.moments);
        if (o.chat && o.words && !o.owner.isEmpty()) metric(h, "本人文字消息", r.ownTexts);
        h.append("</div></article>");
        if (o.chat) {
            annual(h, r);
            String verified = "按对方账号合计双向消息（前 12 位）。";
            if (r.unidentifiedChats > 0) verified += " 另有 " + n(r.unidentifiedChats)
                    + " 条未能确认会话对象的消息（含群聊），未计入私聊排行。";
            chart(h, "双向私聊排行", verified, r.ranked(r.people, r.chatNames), false);
            if (r.chatArchives == 0) {
                h.append("<article class=panel><p class=note>可导入电脑拾聊的聊天 ZIP 或 JSON。</p></article>");
            } else if (r.people.isEmpty() && r.chats > 0) {
                h.append("<article class=panel><p class=note>私聊排行需用电脑导出的会话文件夹与发送者账号核对。"
                        + "请压缩并导入整个电脑导出目录，尽量保留双方消息。</p></article>");
            }
        }
        if (o.moments) {
            String note = "同一时间范围内，按发布者账号比较动态数量（前 12 位）。";
            if (r.unidentifiedPosts > 0) note += " " + n(r.unidentifiedPosts) + " 条收藏或动态缺少发布者账号，未计入排行。";
            chart(h, "朋友圈发帖排行", note, r.ranked(r.posters, r.momentNames), true);
        }
        if (o.chat && o.words) {
            h.append("<article class=panel><h2>聊天里的常用字词</h2><p class=note>来自 ")
                    .append(n(r.texts)).append(" 条文字消息。中文词用 Jieba 词典离线切分，链接和已知表情不计入字词。</p>");
            detail(h, "大家 · 常用字", r.ranked(r.chars, null), true);
            detail(h, "大家 · 常用词", r.ranked(r.words, null), true);
            h.append("</article><article class=panel><h2>我的语言习惯</h2>");
            if (o.owner.isEmpty()) {
                h.append("<p class=note>在「范围与来源」确认本人账号后，可查看自己的常用字、词和微信表情。</p>");
            } else {
                h.append("<p class=note>本人账号：").append(RecordView.esc(r.ownerName(o.owner)))
                        .append(" · ").append(n(r.ownTexts)).append(" 条文字消息</p>");
                detail(h, "本人 · 常用字", r.ranked(r.ownChars, null), false);
                detail(h, "本人 · 常用词", r.ranked(r.ownWords, null), false);
                detail(h, "本人 · 微信表情", r.ranked(r.emoji, null), true);
            }
            h.append("</article>");
        }
        return h.append("<footer>排行仅反映所选档案及日期；缺少时间的记录只在全部时间纳入总数。</footer></main></html>").toString();
    }

    private static void annual(StringBuilder h, FunStats.Result r) {
        h.append("<article class=panel><h2>年度人物</h2><p class=note>每年与你消息往来最多的私聊对象。</p>");
        if (r.annual.isEmpty()) empty(h);
        for (Map.Entry<Integer, Map<String, Long>> year : r.annual.entrySet()) {
            List<FunStats.Row> ranked = r.ranked(year.getValue(), r.chatNames);
            long best = ranked.get(0).count;
            List<String> names = new ArrayList<>();
            int tied = 0;
            for (long count : year.getValue().values()) if (count == best) tied++;
            for (FunStats.Row row : ranked) if (row.count == best) names.add(row.label);
            h.append("<div class=year><strong>").append(year.getKey()).append("</strong><span>")
                    .append(RecordView.esc(String.join("、", names))).append("</span><small>").append(n(best)).append(" 条");
            if (tied > 1) h.append(" · ").append(tied).append(" 人并列");
            h.append("</small></div>");
        }
        h.append("</article>");
    }

    private static void metric(StringBuilder h, String label, long total) {
        h.append("<div class=metric><small>").append(label).append("</small><strong>")
                .append(n(total)).append("</strong></div>");
    }

    private static void chart(StringBuilder h, String label, String note, List<FunStats.Row> rows, boolean green) {
        h.append(green ? "<article class='panel green'>" : "<article class=panel>")
                .append("<h2>").append(label).append("</h2><p class=note>").append(note).append("</p>");
        bars(h, rows);
        h.append("</article>");
    }

    private static void detail(StringBuilder h, String label, List<FunStats.Row> rows, boolean open) {
        h.append(open ? "<details open>" : "<details>").append("<summary>").append(label).append("</summary>");
        bars(h, rows);
        h.append("</details>");
    }

    private static void bars(StringBuilder h, List<FunStats.Row> rows) {
        if (rows.isEmpty()) empty(h);
        for (FunStats.Row row : rows) {
            long fraction = Math.max(1, Math.round(100.0 * row.count / rows.get(0).count));
            h.append("<div class=bar><span>").append(RecordView.esc(row.label))
                    .append("</span><strong>").append(n(row.count)).append("</strong></div>")
                    .append("<div class=track aria-hidden=true><i style='width:").append(fraction).append("%'></i></div>");
        }
    }

    private static void empty(StringBuilder h) {
        h.append("<p class=note>此范围暂无可统计记录。</p>");
    }

    private static String n(long value) { return NumberFormat.getIntegerInstance().format(value); }

    private static String range(FunStats.Options o) {
        if (o.start.isEmpty() && o.end.isEmpty()) return "全部时间";
        return (o.start.isEmpty() ? "不限开始" : o.start) + " 至 " + (o.end.isEmpty() ? "不限结束" : o.end);
    }
}
