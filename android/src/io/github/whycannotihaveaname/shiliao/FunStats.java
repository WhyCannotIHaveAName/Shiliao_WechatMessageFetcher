package io.github.whycannotihaveaname.shiliao;

import android.content.Context;
import android.database.Cursor;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.CancellationException;
import java.util.function.BooleanSupplier;
import java.util.function.LongConsumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class FunStats {
    private static final int LIMIT = 12;
    private static final Pattern FOLDER = Pattern.compile("-([a-fA-F0-9]{10})/$");
    private static final Pattern URL = Pattern.compile("(?i)https?://[^\\s<>，。！？]+");
    private static final Pattern BRACKET = Pattern.compile("\\[([^\\[\\]\\r\\n]{1,20})\\]");
    private static final Set<String> EMOJI = terms(
            "微笑 撇嘴 色 发呆 得意 流泪 害羞 闭嘴 睡 大哭 尴尬 发怒 调皮 呲牙 龇牙 惊讶 难过 酷 冷汗 抓狂 "
            + "吐 偷笑 愉快 白眼 傲慢 饥饿 困 惊恐 流汗 憨笑 悠闲 奋斗 咒骂 疑问 嘘 晕 疯了 衰 骷髅 敲打 再见 "
            + "擦汗 抠鼻 鼓掌 糗大了 坏笑 左哼哼 右哼哼 哈欠 鄙视 委屈 快哭了 阴险 亲亲 吓 可怜 菜刀 西瓜 啤酒 "
            + "篮球 乒乓 咖啡 饭 猪头 玫瑰 凋谢 嘴唇 爱心 心碎 蛋糕 闪电 炸弹 刀 足球 瓢虫 便便 月亮 太阳 礼物 "
            + "拥抱 强 弱 握手 胜利 抱拳 勾引 拳头 差劲 爱你 NO OK 爱情 飞吻 跳跳 发抖 怄火 转圈 磕头 回头 跳绳 "
            + "投降 激动 乱舞 献吻 左太极 右太极 笑哭 捂脸 破涕为笑 皱眉 耶 机智 失望 无语 狗头 旺柴 打脸 加油 "
            + "嘿哈 奸笑 茶 让我看看 叹气 苦涩 裂开 啊这 天啊 托腮 揉脸 社会社会 吃瓜 加油加油 "
            + "合十 抱抱 红包 福 发 红包来了 围观 庆祝 烟花 爆竹 打气 辛苦 666 翻白眼");
    private static final Set<String> SERVICE = terms(
            "filehelper fmessage newsapp weixin medianote floatbottle qqmail qmessage tmessage weibo "
            + "feedsapp notification_messages masssendapp officialaccounts appbrandcustomerservicemsg weixinreminder");

    static final class Options {
        final boolean chat, moments, words;
        final String start, end, owner;
        // Null selects all archives of that type, including those imported later.
        final Set<Long> chatArchives, momentArchives;

        Options(boolean chat, boolean moments, boolean words, String start, String end, String owner,
                Set<Long> chatArchives, Set<Long> momentArchives) {
            this.chat = chat;
            this.moments = moments;
            this.words = words;
            this.start = start;
            this.end = end;
            this.owner = owner;
            this.chatArchives = chatArchives == null ? null : new HashSet<>(chatArchives);
            this.momentArchives = momentArchives == null ? null : new HashSet<>(momentArchives);
        }

        ArchiveStore.Filter filter() {
            Long lower = MainActivity.date(start, false), upper = MainActivity.date(end, true);
            if (lower != null && upper != null && lower >= upper) throw new IllegalArgumentException("请检查日期顺序");
            return new ArchiveStore.Filter("", lower, upper);
        }
    }

    static final class Row {
        final String label;
        final long count;
        Row(String label, long count) { this.label = label; this.count = count; }
    }

    static final class Result {
        long chats, moments, privateMessages, unidentifiedChats, unidentifiedPosts, texts, ownTexts;
        int chatArchives, momentArchives;
        final Map<String, Long> people = new HashMap<>(), chars = new HashMap<>(), words = new HashMap<>(),
                ownChars = new HashMap<>(), ownWords = new HashMap<>(), emoji = new HashMap<>(),
                posters = new HashMap<>(), senders = new HashMap<>();
        final Map<String, String> chatNames = new HashMap<>(), momentNames = new HashMap<>();
        final Map<Integer, Map<String, Long>> annual = new TreeMap<>(Comparator.reverseOrder());

        List<Row> ranked(Map<String, Long> counts, Map<String, String> names) {
            List<Row> rows = new ArrayList<>();
            for (String id : sorted(counts, LIMIT)) rows.add(new Row(display(id, names), counts.get(id)));
            return rows;
        }

        String ownerName(String id) { return chatNames.getOrDefault(id, id); }

        private String display(String id, Map<String, String> names) {
            if (names == null) return id;
            String name = names.getOrDefault(id, id);
            for (Map.Entry<String, String> other : names.entrySet()) {
                if (!id.equals(other.getKey()) && name.equals(other.getValue())) return name + " · " + id;
            }
            return name;
        }

        List<String> ownerChoices() { return sorted(senders, 36); }
    }

    private static final class Conversation {
        final String token;
        long count;
        final Map<Integer, Long> years = new HashMap<>();
        Conversation(String token) { this.token = token; }
    }

    static Result collect(Context context, ArchiveStore store, Options options, BooleanSupplier stopped,
                          LongConsumer progress) throws Exception {
        ArchiveStore.Filter filter = options.filter();
        Result result = new Result();
        MessageDigest md5 = MessageDigest.getInstance("MD5");
        Map<String, String> byToken = new HashMap<>();
        Set<String> seenSenders = new HashSet<>(), collisions = new HashSet<>();
        Set<String> seenChat = new HashSet<>(), seenPosts = new HashSet<>();
        List<Conversation> conversations = new ArrayList<>();
        long scanned = 0;
        WordLexicon lexicon = null;
        if (options.chat) for (ArchiveStore.Dataset data : store.datasets(ArchiveStore.CHAT)) {
            if (options.chatArchives != null && !options.chatArchives.contains(data.id)) continue;
            result.chatArchives++;
            Conversation chat = new Conversation(token(data.prefix));
            conversations.add(chat);
            try (Cursor rows = store.records(data, filter, 0, 0)) {
                while (rows.moveToNext()) {
                    tick(stopped, progress, ++scanned);
                    JSONObject record = new JSONObject(rows.getString(0));
                    int code = (int) record.optLong("type_code");
                    if (code == 10000 || code == 10002 || code == 0 && record.has("type_code")) continue;
                    String sender = RecordView.str(record, "sender_username");
                    if (!sender.isEmpty()) {
                        if (seenSenders.add(sender)) {
                            String hash = hashToken(md5, sender);
                            String old = byToken.putIfAbsent(hash, sender);
                            if (old != null && !old.equals(sender)) collisions.add(hash);
                        }
                        name(result.chatNames, sender, RecordView.str(record, "sender"));
                    }
                    String serverId = RecordView.str(record, "server_id");
                    if (!chat.token.isEmpty() && !serverId.isEmpty() && !"0".equals(serverId)
                            && !seenChat.add(chat.token + ':' + serverId)) continue;
                    result.chats++;
                    chat.count++;
                    if (!sender.isEmpty()) increment(result.senders, sender);
                    Integer year = year(record);
                    if (year != null) chat.years.merge(year, 1L, Long::sum);
                    if (!options.words || code != 1 && record.has("type_code")) continue;
                    String body = RecordView.str(record, "text");
                    if (!sender.isEmpty() && (body.startsWith(sender + ":\n") || body.startsWith(sender + ":\r\n"))) {
                        body = body.substring(body.indexOf('\n') + 1);
                    }
                    if (body.isEmpty()) continue;
                    if (lexicon == null) lexicon = new WordLexicon(context.getAssets().open("words.bin"));
                    boolean own = !options.owner.isEmpty() && options.owner.equals(sender);
                    result.texts++;
                    if (own) result.ownTexts++;
                    countText(lexicon, result, body, own);
                }
            }
        }
        for (Conversation chat : conversations) {
            String user = collisions.contains(chat.token) ? "" : byToken.getOrDefault(chat.token, "");
            if (user.isEmpty()) result.unidentifiedChats += chat.count;
            if (!person(user, options.owner)) continue;
            result.privateMessages += chat.count;
            result.people.merge(user, chat.count, Long::sum);
            for (Map.Entry<Integer, Long> year : chat.years.entrySet()) {
                result.annual.computeIfAbsent(year.getKey(), k -> new HashMap<>()).merge(user, year.getValue(), Long::sum);
            }
        }
        if (options.moments) for (ArchiveStore.Dataset data : store.datasets(ArchiveStore.MOMENTS)) {
            if (options.momentArchives != null && !options.momentArchives.contains(data.id)) continue;
            result.momentArchives++;
            try (Cursor rows = store.records(data, filter, 0, 0)) {
                while (rows.moveToNext()) {
                    tick(stopped, progress, ++scanned);
                    JSONObject record = new JSONObject(rows.getString(0));
                    String id = RecordView.str(record, "id");
                    String user = RecordView.str(record, "username");
                    if (record.has("collected_at")) user = "";
                    if (!id.isEmpty() && !seenPosts.add(id)) continue;
                    result.moments++;
                    if (user.isEmpty()) result.unidentifiedPosts++;
                    else {
                        increment(result.posters, user);
                        name(result.momentNames, user, RecordView.str(record, "author"));
                    }
                }
            }
        }
        cancelled(stopped);
        return result;
    }

    private static void tick(BooleanSupplier stopped, LongConsumer progress, long scanned) {
        cancelled(stopped);
        if (scanned % 5000 == 0) progress.accept(scanned);
    }

    private static void cancelled(BooleanSupplier stopped) {
        if (stopped.getAsBoolean()) throw new CancellationException();
    }

    private static void countText(WordLexicon lexicon, Result result, String raw, boolean own) {
        String normalized = URL.matcher(Normalizer.normalize(raw, Normalizer.Form.NFKC)).replaceAll(" ");
        Matcher bracket = BRACKET.matcher(normalized);
        StringBuffer cleaned = new StringBuffer();
        while (bracket.find()) if (EMOJI.contains(bracket.group(1))) {
            if (own) increment(result.emoji, bracket.group());
            bracket.appendReplacement(cleaned, " ");
        }
        String text = bracket.appendTail(cleaned).toString();
        for (int i = 0; i < text.length();) {
            int c = text.codePointAt(i);
            int first = i;
            i += Character.charCount(c);
            if (han(c)) {
                while (i < text.length() && han(text.codePointAt(i))) i += Character.charCount(text.codePointAt(i));
                for (int j = first; j < i;) {
                    int next = j + Character.charCount(text.codePointAt(j));
                    String ch = text.substring(j, next);
                    increment(result.chars, ch);
                    if (own) increment(result.ownChars, ch);
                    j = next;
                }
                lexicon.count(text, first, i, result.words, own ? result.ownWords : null);
            } else if (Character.isLetter(c)) {
                while (i < text.length()) {
                    int letter = text.codePointAt(i);
                    if (!Character.isLetter(letter) || han(letter)) break;
                    i += Character.charCount(letter);
                }
                if (i - first > Character.charCount(c)) {
                    String word = text.substring(first, i).toLowerCase(Locale.ROOT);
                    increment(result.words, word);
                    if (own) increment(result.ownWords, word);
                }
            }
        }
    }

    private static boolean han(int c) {
        return c >= 0x3400 && c <= 0x9fff || c >= 0xf900 && c <= 0xfaff || c >= 0x20000 && c <= 0x323af;
    }

    private static Set<String> terms(String words) { return new HashSet<>(Arrays.asList(words.split(" "))); }

    private static boolean person(String user, String owner) {
        return !user.isEmpty() && !user.equals(owner) && !SERVICE.contains(user)
                && !user.startsWith("gh_") && !user.endsWith("@chatroom") && !user.endsWith("@openim");
    }

    private static String token(String prefix) {
        Matcher match = FOLDER.matcher(prefix);
        return match.find() ? match.group(1).toLowerCase(Locale.ROOT) : "";
    }

    private static String hashToken(MessageDigest md5, String user) {
        byte[] hash = md5.digest(user.getBytes(StandardCharsets.UTF_8));
        char[] text = new char[10];
        String digits = "0123456789abcdef";
        for (int i = 0; i < 5; i++) {
            text[i * 2] = digits.charAt((hash[i] & 255) >>> 4);
            text[i * 2 + 1] = digits.charAt(hash[i] & 15);
        }
        return new String(text);
    }

    private static Integer year(JSONObject record) {
        // Prefer the archive's timezone to preserve the original calendar year.
        long stamp = record.optLong("timestamp");
        if (stamp <= 0 || stamp > 253402300799L) return null;
        try { return OffsetDateTime.parse(RecordView.str(record, "time")).getYear(); }
        catch (RuntimeException ignored) { return Instant.ofEpochSecond(stamp).atZone(ZoneId.systemDefault()).getYear(); }
    }

    private static void name(Map<String, String> names, String id, String value) {
        if (!value.isEmpty()) names.putIfAbsent(id, value);
    }

    static void increment(Map<String, Long> counts, String key) { counts.merge(key, 1L, Long::sum); }

    private static List<String> sorted(Map<String, Long> counts, int limit) {
        List<String> order = new ArrayList<>(counts.keySet());
        order.sort(Comparator.<String>comparingLong(counts::get).reversed().thenComparing(Comparator.naturalOrder()));
        return Collections.unmodifiableList(order.subList(0, Math.min(limit, order.size())));
    }
}
