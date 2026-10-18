#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付前边界审计：把每条成片的边界句子摊出来给人读，并标高置信可疑点。

用法:
  python audit_bounds.py --dump <转录TSV(序号\t起\t止\t文本)> --manifest a.tsv,b.tsv

- 同名成片以**最后一份提到它的清单为准**（返修后不会被旧清单叠加污染）。
- 打印「前 2 句 | 起点句 | 终点句 | 后 2 句」——这是主产物，**每条必须人工读一遍**，
  启发式只是兜底，宁可少报也不刷一堆无效告警。
- 三类告警（都是高精度规则）：
    START-MIDSENTENCE  起点句以连词/虚词开头或收尾 → 上一句可能才是话题第一句
    END-CLIPPED        终点句以虚词收尾或极短 → 后一句可能才是它的下半句
    TITLE-LATE         标题里的数字/英文锚点在全片 45% 之后才出现 → 前段可能属于别的提问
  标题里没有可定位锚点时不报警，只提示"需人工判断"。
只读，不写文件。
"""
import argparse, os, re, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OPEN_TAIL = ("的", "了", "是", "在", "把", "和", "与", "就", "都", "也", "要", "能", "会",
             "有", "不", "没", "很", "更", "地", "得", "而", "且", "但", "我", "你", "他",
             "这", "那", "个", "之", "于", "来", "去", "做", "学", "大", "小", "多", "少")
HEAD_START = ("所以", "但是", "而且", "因为", "然后", "可是", "或者", "还是", "不过",
              "那就是", "这", "那", "它", "他", "她")
DONE_TAIL = ("。", "？", "?", "！", "!", "吗", "啊", "呢", "吧", "呀", "了", "的", "嘛", "哈", "呗")


def sec(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s.replace(",", "."))


def load_dump(path):
    rows = []
    for ln in open(path, encoding="utf-8"):
        c = ln.rstrip("\n").split("\t")
        if len(c) >= 4:
            rows.append((sec(c[1]), sec(c[2]), c[3]))
    return rows


def find(rows, t, col):
    for i, r in enumerate(rows):
        if abs(r[col] - t) < 0.08:
            return i
    return None


def anchors(title):
    """从标题里抽可定位锚点：数字（含单位）与 2 字以上英文。"""
    out = re.findall(r"\d+(?:\.\d+)?(?:万|亿|块|岁|月|日|年|个|种|类|次|遍|分|秒|成|倍|w)?", title)
    out += re.findall(r"[A-Za-z]{2,}", title)
    return [x for x in out if len(x) >= 2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--manifest", required=True, help="逗号分隔；同名以靠后的清单为准")
    a = ap.parse_args()
    rows = load_dump(a.dump)
    clips = {}
    for p in [x.strip() for x in a.manifest.split(",")]:
        if not os.path.exists(p):
            print("跳过不存在的清单:", p)
            continue
        local = {}
        for ln in open(p, encoding="utf-8"):
            if ln.startswith("#") or not ln.strip():
                continue
            c = ln.rstrip("\n").split("\t")
            if len(c) >= 3:
                local.setdefault(c[2].strip(), set()).add((sec(c[0]), sec(c[1])))
        clips.update(local)

    bad = 0
    for name in sorted(clips):
        rs = sorted(clips[name])
        i0, i1 = find(rows, rs[0][0], 0), find(rows, rs[-1][1], 1)
        print("=== %s  跨度 %.1f min  %d 段" % (name, (rs[-1][1] - rs[0][0]) / 60, len(rs)))
        if i0 is None or i1 is None:
            print("    !! 时间码在转录里找不到：先核对 SRT 与清单是否同源")
            bad += 1
            continue
        pre = [rows[j][2] for j in range(max(0, i0 - 2), i0)]
        post = [rows[j][2] for j in range(i1 + 1, min(len(rows), i1 + 3))]
        s0, s1 = rows[i0][2], rows[i1][2]
        print("    前2句: %s" % " | ".join(pre))
        print("    起点句: %s" % s0)
        print("    终点句: %s" % s1)
        print("    后2句: %s" % " | ".join(post))
        flags = []
        if s0.startswith(HEAD_START) or (len(s0) <= 12 and s0[-1:] in OPEN_TAIL):
            flags.append("START-MIDSENTENCE 起点句像半句/承接句 → 上一句可能才是话题第一句")
        if (s1[-1:] in OPEN_TAIL and s1[-2:] not in ("的了",)) or len(s1) <= 4:
            if not s1.endswith(DONE_TAIL):
                flags.append("END-CLIPPED 终点句可能没说完 → 后一句可能才是下半句")
        title = re.sub(r"^\d+-|^定制-", "", name)
        span = "".join(r[2] for r in rows[i0:i1 + 1])
        hits = [span.find(k) for k in anchors(title)]
        hits = [h for h in hits if h >= 0]
        if anchors(title) and not hits:
            flags.append("TITLE-ANCHOR 标题里的数字/英文锚点 %s 在段内没找到 → 核对是不是编造"
                         % anchors(title))
        elif hits and min(hits) / max(1, len(span)) > 0.45:
            flags.append("TITLE-LATE 标题锚点最早出现在全片 %.0f%% 处 → 前段可能属于别的提问"
                         % (100 * min(hits) / len(span)))
        elif not anchors(title):
            print("    （标题无数字/英文锚点，切题与否只能人工判断）")
        for f in flags:
            print("    ⚠ %s" % f)
        bad += len(flags)
        if not flags:
            print("    ✓ 高置信检查通过（仍需人读上面四行）")
    print("\n合计告警 %d 条。" % bad)


if __name__ == "__main__":
    main()
