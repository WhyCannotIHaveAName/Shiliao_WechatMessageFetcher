"""Pack the pinned Jieba dictionary as a compact trie for the Android app."""

from collections import deque
from hashlib import sha256
from math import log
from pathlib import Path
from struct import pack
import sys

import jieba


def is_han(code):
    return 0x3400 <= code <= 0x9fff or 0xf900 <= code <= 0xfaff or 0x20000 <= code <= 0x323af


def build(output):
    dictionary = Path(jieba.__file__).with_name("dict.txt").read_bytes()
    if sha256(dictionary).hexdigest() != "7197c3211ddd98962b036cdf40324d1ea2bfaa12bd028e68faa70111a88e12a8":
        raise ValueError("Install jieba==0.42.1 before building the Android APK")
    root = [0, {}]
    total = longest = 0
    for line in dictionary.decode("utf-8").splitlines():
        word, frequency, *_ = line.split()
        count = int(frequency)
        total += count
        if not all(is_han(ord(c)) for c in word):
            continue
        longest = max(longest, len(word))
        node = root
        for code in map(ord, word):
            node = node[1].setdefault(code, [0, {}])
        node[0] = count
    pending = deque([(0, root)])
    log_total = log(total)
    size = 1
    with output.open("wb") as stream:
        stream.write(pack(">IIIf", 0x534C4431, 0, longest, -log_total))
        while pending:
            code, (count, children) = pending.popleft()
            stream.write(pack(">IIf", code, size, log(count) - log_total if count else 0))
            pending.extend(sorted(children.items()))
            size += len(children)
        stream.write(pack(">I", size))
        stream.seek(4)
        stream.write(pack(">I", size))


if __name__ == "__main__":
    build(Path(sys.argv[1]))
