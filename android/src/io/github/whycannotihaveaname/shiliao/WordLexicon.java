package io.github.whycannotihaveaname.shiliao;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.Map;

/** A frequency-weighted dictionary segmenter for Han text, with no network or model. */
final class WordLexicon {
    private final int[] code, firstChild;
    private final float[] weight;
    private final int longest;
    private final float single;

    WordLexicon(InputStream source) throws IOException {
        try (DataInputStream input = new DataInputStream(new BufferedInputStream(source))) {
            if (input.readInt() != 0x534c4431) throw new IOException("分词词典格式错误");
            int nodes = input.readInt();
            longest = input.readInt();
            single = input.readFloat();
            if (nodes < 1 || nodes > 2_000_000 || longest < 1 || longest > 100) {
                throw new IOException("分词词典大小异常");
            }
            code = new int[nodes];
            firstChild = new int[nodes + 1];
            weight = new float[nodes];
            for (int i = 0; i < nodes; i++) {
                code[i] = input.readInt();
                firstChild[i] = input.readInt();
                weight[i] = input.readFloat();
            }
            firstChild[nodes] = input.readInt();
            if (firstChild[nodes] != nodes) throw new IOException("分词词典未完整读取");
        }
    }

    private int child(int node, int c) {
        int low = firstChild[node], high = firstChild[node + 1] - 1;
        while (low <= high) {
            int mid = (low + high) >>> 1;
            if (c == code[mid]) return mid;
            if (c < code[mid]) high = mid - 1;
            else low = mid + 1;
        }
        return -1;
    }

    void count(String content, int start, int end, Map<String, Long> words, Map<String, Long> own) {
        if (start == end || start + Character.charCount(content.codePointAt(start)) == end) return;
        int[] han = new int[end - start];
        int length = 0;
        for (int i = start; i < end;) {
            int c = content.codePointAt(i);
            han[length++] = c;
            i += Character.charCount(c);
        }
        double[] best = new double[length + 1];
        int[] next = new int[length];
        for (int i = length - 1; i >= 0; i--) {
            int node = child(0, han[i]);
            next[i] = i + 1;
            best[i] = best[i + 1] + (node >= 0 && weight[node] != 0 ? weight[node] : single);
            for (int j = i + 1; node >= 0 && j < length && j - i < longest; j++) {
                node = child(node, han[j]);
                if (node < 0 || weight[node] == 0) continue;
                double score = best[j + 1] + weight[node];
                if (score >= best[i]) {
                    best[i] = score;
                    next[i] = j + 1;
                }
            }
        }
        for (int i = 0; i < length; i = next[i]) if (next[i] - i > 1) {
            String token = new String(han, i, next[i] - i);
            FunStats.increment(words, token);
            if (own != null) FunStats.increment(own, token);
        }
    }
}
