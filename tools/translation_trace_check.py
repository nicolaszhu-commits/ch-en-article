#!/usr/bin/env python3
"""
转写痕迹检测 —— 流水线阶段 5.1。

目标：判断英文成稿是否"看得出是从中文原文翻译/逐段对译"出来的。
这不是查抄袭(同语言撞文)，而是查【跨语言结构对译痕迹 + 翻译腔信号】。

两类检测：

A. 结构对译痕迹（脚本量化）
   利用"语言无关锚点"——在中英文里写法相同的实体：数字/金额/百分比、
   拉丁字母专有名词(SpaceX、Nasdaq、USDT…)、年份等。
   - 锚点顺序重合度：英文锚点序列 vs 中文锚点序列的最长公共子序列(LCS)占比。
     越高说明英文越是"顺着中文的顺序往下译"。
   - 段落数比值：中英段落数接近 1:1 也是逐段对译的信号。
   两者都高 → 强烈提示线性对译，应打散重组结构。

B. 翻译腔信号（脚本启发式，给人工复核线索）
   扫描英文成稿里常见的中译英痕迹：直译虚词、范畴词冗余、
   过多 "the development/construction/situation of" 之类抽象名词堆叠等。

仅用 Python 标准库。

示例：
  python3 tools/translation_trace_check.py \
      --source articles/<slug>/source.md \
      --output articles/<slug>/output-en.md
"""
import argparse
import re
import sys
from pathlib import Path

# ---------- 通用 ----------

def read(path):
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def strip_md(t):
    t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"^>.*$", " ", t, flags=re.MULTILINE)  # 引用块(免责声明等)
    return t


def paragraphs(t):
    return [p.strip() for p in re.split(r"\n\s*\n", t) if len(p.strip()) > 20]


# ---------- A. 锚点提取 ----------

# 这些大写词太常见，作为句首/通用词会噪声化，排除
STOPWORD_CAPS = {
    "The", "A", "An", "In", "On", "At", "It", "This", "That", "These", "Those",
    "But", "And", "Or", "So", "If", "As", "For", "To", "Of", "By", "We", "You",
    "I", "He", "She", "They", "Its", "Their", "What", "When", "Once", "There",
    "Note", "Other", "First", "Second", "Third", "US", "U.S", "U", "S",
}


def anchors(text, keep_latin=True):
    """按出现顺序提取锚点序列。数字标准化去逗号；专有名词保留原形。"""
    seq = []
    for m in re.finditer(r"\d[\d,.]*\s*%?|[A-Za-z][A-Za-z0-9.&-]+", text):
        tok = m.group(0).strip()
        if re.match(r"\d", tok):
            norm = tok.replace(",", "").replace(" ", "")
            norm = norm.rstrip(".")
            if len(norm) >= 2 or norm.endswith("%"):  # 跳过孤立单个数字噪声
                seq.append(("NUM", norm))
        elif keep_latin:
            if tok[0].isupper() and tok not in STOPWORD_CAPS and not tok.islower():
                seq.append(("ENT", tok))
    return seq


def anchors_from_chinese(text):
    """中文原文里同样提取数字 + 夹杂的拉丁专有名词。"""
    seq = []
    for m in re.finditer(r"\d[\d,.]*\s*%?|[A-Za-z][A-Za-z0-9.&-]+", text):
        tok = m.group(0).strip()
        if re.match(r"\d", tok):
            norm = tok.replace(",", "").replace(" ", "").rstrip(".")
            if len(norm) >= 2 or norm.endswith("%"):
                seq.append(("NUM", norm))
        else:
            if tok[0].isupper() and tok not in STOPWORD_CAPS:
                seq.append(("ENT", tok))
    return seq


def lcs_len(a, b):
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 0
    prev = [0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0] * (m + 1)
        ai = a[i - 1]
        for j in range(1, m + 1):
            cur[j] = prev[j - 1] + 1 if ai == b[j - 1] else max(prev[j], cur[j - 1])
        prev = cur
    return prev[m]


# ---------- B. 翻译腔启发式 ----------

TRANSLATIONESE = [
    (r"\bcarried out\b|\bcarry out\b", "直译 '进行/开展'"),
    (r"\b(the )?(development|construction|situation|phenomenon|problem) of\b",
     "范畴词/抽象名词堆叠(中式'…的发展/情况/问题')"),
    (r"\brelevant\b", "高频 'relevant'(中文'相关'直译)"),
    (r"\bcontinuously\b|\bunceasingly\b", "'不断地'直译"),
    (r"\bin order to\b", "冗长 'in order to'(可简化为 to)"),
    (r"\bmake (a |an )?\w+ (out of|of) (it|them|this)\b", "'做出…' 动名词化"),
    (r"\bso-?called\b", "'所谓的' 直译"),
    (r"\bas we (all )?know\b", "'众所周知' 直译套话"),
    (r"\bit is worth (noting|mentioning)\b", "'值得注意的是' 套话"),
    (r"\bvarious kinds of\b|\ball kinds of\b", "'各种各样的' 冗余"),
    (r"\bat present\b|\bat the present stage\b", "'目前/现阶段' 直译"),
    (r"\bplay (an? )?.*role\b", "'发挥…作用' 直译"),
]


def scan_translationese(text):
    hits = []
    for pat, label in TRANSLATIONESE:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            s = max(0, m.start() - 35)
            e = min(len(text), m.end() + 35)
            hits.append((label, text[s:e].replace("\n", " ").strip()))
    return hits


# ---------- 主流程 ----------

def analyze(source_path, output_path):
    src_raw = read(source_path)
    out_raw = strip_md(read(output_path))

    src_anchor = anchors_from_chinese(src_raw)
    out_anchor = anchors(out_raw)

    # 仅保留两边都出现过的锚点种类做顺序比较(交集词表)，更能反映"对译顺序"
    src_vals = [v for _, v in src_anchor]
    out_vals = [v for _, v in out_anchor]
    common_vocab = set(src_vals) & set(out_vals)
    src_seq = [v for v in src_vals if v in common_vocab]
    out_seq = [v for v in out_vals if v in common_vocab]

    l = lcs_len(src_seq, out_seq)
    denom = min(len(src_seq), len(out_seq))
    order_overlap = (l / denom) if denom else 0.0

    src_paras = paragraphs(src_raw)
    out_paras = paragraphs(out_raw)
    para_ratio = (len(out_paras) / len(src_paras)) if src_paras else 0.0

    tese = scan_translationese(out_raw)

    return {
        "shared_anchors": sorted(common_vocab),
        "shared_anchor_count": len(common_vocab),
        "order_overlap": round(order_overlap, 3),
        "src_paragraphs": len(src_paras),
        "out_paragraphs": len(out_paras),
        "paragraph_ratio": round(para_ratio, 2),
        "translationese": tese,
    }


def verdict(r):
    flags = []
    # 结构对译：共享锚点足够多 且 顺序高度一致
    if r["shared_anchor_count"] >= 8 and r["order_overlap"] >= 0.9:
        flags.append("⚠️ 锚点顺序高度一致(%.0f%%) → 强烈提示逐段线性对译，建议打散重排论证顺序"
                     % (r["order_overlap"] * 100))
    elif r["shared_anchor_count"] >= 8 and r["order_overlap"] >= 0.75:
        flags.append("△ 锚点顺序较一致(%.0f%%) → 存在对译倾向，建议局部重组"
                     % (r["order_overlap"] * 100))
    if 0.9 <= r["paragraph_ratio"] <= 1.15 and r["src_paragraphs"] >= 6:
        flags.append("△ 段落数近 1:1(%.2f) → 可能逐段对应，考虑合并/拆分重构"
                     % r["paragraph_ratio"])
    if len(r["translationese"]) >= 6:
        flags.append("△ 翻译腔信号偏多(%d 处) → 人工通读润色" % len(r["translationese"]))
    return flags


def print_report(src, out, r):
    print(f"\n=== 转写痕迹检测 ===")
    print(f"原文：{src}")
    print(f"成稿：{out}\n")
    print(f"[A. 结构对译]")
    print(f"  共享锚点数(数字/专名)：{r['shared_anchor_count']}")
    print(f"  锚点顺序重合度(LCS)：{r['order_overlap']}  (越接近 1 越像顺序对译)")
    print(f"  段落数 原文/成稿：{r['src_paragraphs']} / {r['out_paragraphs']}  比值 {r['paragraph_ratio']}")
    if r["shared_anchors"]:
        preview = ", ".join(r["shared_anchors"][:18])
        print(f"  共享锚点样例：{preview}{' …' if r['shared_anchor_count'] > 18 else ''}")
    print(f"\n[B. 翻译腔信号]  共 {len(r['translationese'])} 处")
    for label, ctx in r["translationese"][:12]:
        print(f"  · {label}")
        print(f"      …{ctx}…")
    if len(r["translationese"]) > 12:
        print(f"  ……另有 {len(r['translationese']) - 12} 处")

    flags = verdict(r)
    print(f"\n[结论]")
    if flags:
        for f in flags:
            print("  " + f)
    else:
        print("  ✅ 未见明显逐段对译或翻译腔，转写痕迹低，读起来像原生英文写作。")
    return bool(flags)


def main():
    ap = argparse.ArgumentParser(description="转写痕迹检测(跨语言对译 + 翻译腔)")
    ap.add_argument("--source", required=True, help="中文原文 source.md")
    ap.add_argument("--output", required=True, help="英文成稿 output-en.md")
    ap.add_argument("--order-flag", type=float, default=0.9,
                    help="锚点顺序重合度红线，默认 0.9")
    args = ap.parse_args()
    r = analyze(args.source, args.output)
    flagged = print_report(args.source, args.output, r)
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
