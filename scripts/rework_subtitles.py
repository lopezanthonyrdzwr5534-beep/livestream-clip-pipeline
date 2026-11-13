#!/usr/bin/env python3
"""Rework an existing timeline's newly recognized subtitles.

Keeps video cuts, titles, effects and SFX unchanged; only rebuilds the subtitle
track with the fixed slice-edit style and the current subtitle rules:
proofread -> collapse repeats -> merge short pauses/fillers -> split <=12 chars
-> zero-gap timing -> fixed font/size/color/position.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import uuid
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MAX_CHARS = 12          # 软目标：优先在这个长度以内断
MAX_CHARS_HARD = 14     # 硬上限：超过才强制拆
FILLERS = set("呃啊唉哦嗯呀嘛哈哎")
FUNC = set("的地得了着过和与或而但就也还又才都把被给对在从向为以于跟同吗呢吧啊呀嘛")
REPEAT_TOKENS = ["我", "你", "他", "她", "它", "这个", "那个", "就是", "然后", "所以", "但是",
                 "就", "很", "都", "也", "还", "又", "再", "对"]
AA_WHITELIST = ["看看", "试试", "说说", "谢谢", "慢慢", "刚刚", "常常", "天天", "好好", "多多",
                "早早", "远远", "深深", "轻轻", "静静", "悄悄", "渐渐", "纷纷", "一一", "拜拜",
                "了了", "哈哈", "嘿嘿", "呵呵", "嘻嘻"]

# ABAB 动词重叠：连着说两遍是正常口语（「你琢磨琢磨」「多了解了解」），不是口吃。
# 2026-11-02 实测这一场 7 处「琢磨琢磨」，被旧规则删成一遍就把原话改错了。
REDUPLICATE = {"琢磨", "考虑", "寻思", "了解", "打听", "研究", "商量", "收拾", "照顾",
               "交流", "学习", "复习", "预习", "观察", "整理", "活动", "放松", "比划",
               "念叨", "讨论", "思考", "体验", "尝试", "比较", "教训", "表现", "意思",
               "什么", "非常", "对吧", "好多", "好久", "一会儿"}
TERMS = {"985211": "985/211", "d边": "地编", "d编": "地编", "地边": "地编",
         "树莓": "数媒", "树眉": "数媒", "树美": "数媒", "数眉": "数媒",
         "web coding": "vibe coding", "Web coding": "vibe coding",
         "试传": "视传", "视川": "视传", "世传": "视传", "事传": "视传",
         "怀艺": "环艺", "环椅": "环艺",
         "产舍": "产设", "产社": "产设", "阐舍": "产设",
         "研医": "研一", "延二": "研二",
         "大场": "大厂", "大长": "大厂", "打场": "大厂",
         "公资": "工资", "使论": "史论",
         "南易": "南艺", "难艺": "南艺",
         "技点": "绩点", "辑点": "绩点",
         "二幺": "211", "一v一": "1v1", "依v": "1v1",
         "秋昭": "秋招", "求招": "秋招", "川委": "川美", "瑞传达": "视觉传达",
         # 2026-11-02 场次新增
         "大4": "大四", "D边": "地编", "AI GC": "AIGC", "AI GC设计": "AIGC设计",
         "for a广告": "4A广告", "b站": "B站", "8080后": "80后"}
# 必须在数字转换之前整串替换：ASR 把一串薪资档位粘在一起（8,0009,000…），
# 先跑 digits_to_cn 会只换掉开头一段，剩下的既不对也匹配不到这条规则。
PRE_TERMS = {"从0": "从零", "8,0009,0001万11,0001万2": "八千九千一万、一万一千、一万两千",
             "8,0009,000": "八千九千",
             "8,0009千一万1万一一万2": "八千九千一万、一万一千、一万两千"}
PROTECTED = ["AI产品设计", "字节跳动", "能不能", "容不容易", "上班后", "下班后",
             "地编", "数媒", "字节", "offer", "1V1", "985/211", "Figma", "UE"]


def uid() -> str:
    return str(uuid.uuid4()).upper()


def norm(s: str) -> str:
    return "".join(c for c in s if re.match(r"[\u4e00-\u9fffA-Za-z0-9]", c)).lower()



CN_DIGITS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
STUTTER_CHARS = "我你他她它这那就都也还又才做工是有个对去来很太没在"
# 想想 / 说说 / 做做 是动词重叠（「你想想」「我跟你说说」「做做样子」），不是口吃；
# 2026-11-02 上一版把 想/说/做 收进来了，把「不想想怎么回事」改成「不想怎么回事」，改错了。


def _cn_value(tok: str):
    """一 / 十 / 十X / X十 / X十Y -> int；认不出返回 None。"""
    if not tok:
        return None
    if tok.isdigit():
        return int(tok)
    if len(tok) == 1:
        if tok == "十":
            return 10
        return CN_DIGITS.get(tok)
    if tok.startswith("十"):
        rest = CN_DIGITS.get(tok[1:])
        return 10 + rest if rest else None
    if tok.endswith("十"):
        head = CN_DIGITS.get(tok[:-1])
        return head * 10 if head else None
    if len(tok) == 3 and tok[1] == "十":
        h, r = CN_DIGITS.get(tok[0]), CN_DIGITS.get(tok[2])
        return h * 10 + r if h is not None and r is not None else None
    return None


def cn_to_digits(text: str) -> str:
    """把金额/数量的中文数字写成阿拉伯，单位字保留：二十万→20万、四五千→4-5千。

    只处理「数字 + 百/千/万」这一种形态；几十万、十几万这类约数保持原样。
    """
    mult = {"百": 100, "千": 1000, "万": 10000, "元": 1}

    def conv(m):
        num, unit = m.group(1), m.group(2)
        k = mult[unit]
        if len(num) == 2 and all(c in CN_DIGITS and c not in "零十" for c in num):
            a, b = CN_DIGITS[num[0]], CN_DIGITS[num[1]]
            if 1 <= a <= 9 and 1 <= b <= 9:      # 四五千 -> 4,000-5,000
                return f"{a * k:,}-{b * k:,}"
        v = _cn_value(num)
        if not v:
            return m.group(0)
        return f"{v * k:,}" if unit != "元" else f"{v:,}"

    # 几十万 / 十几万 / 数万 是约数，不转
    return re.sub(r"(?<![几数上])([一二两三四五六七八九十]{1,3})([百千万])", conv, text)



CN_D = "零一二三四五六七八九"

# 只有紧跟这些后缀的阿拉伯数字才转中文：数量级 + 量词。
# 故意不含 年/月/日/届/%/后 —— 那些是档位、日期、比例，写成中文反而读不顺。
CN_TAIL = r"(?:[千百十万]|分钟|年前|个|份|岁|倍|次|条|件|来|家|块|元|遍|人)"


def _cn_num(n: int) -> str:
    """0–99999 的纯数字读法，2 一律读「二」（序数、数位中间都用它）。"""
    if n < 10:
        return CN_D[n]
    if n < 100:
        t, o = divmod(n, 10)
        return ("十" if t == 1 else CN_D[t] + "十") + (CN_D[o] if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return CN_D[h] + "百" + ("" if r == 0 else "零" + _cn_num(r) if r < 10 else _cn_num(r))
    if n < 10000:
        k, r = divmod(n, 1000)
        return CN_D[k] + "千" + ("" if r == 0 else "零" + _cn_num(r) if r < 100 else _cn_num(r))
    w, r = divmod(n, 10000)
    return _cn_num(w) + "万" + ("" if r == 0 else "零" + _cn_num(r) if r < 1000 else _cn_num(r))


def _cn_qty(n: int) -> str:
    """金额/数量口径：单独的 2、以及打头的 2 读「两」（两万 / 两百 / 两千 / 两个），
    但 20、32、28 里的 2 还是「二」（二十万、三十二万、二十八万）——
    2026-11-02 第一版把 tens 位也写成「两」，出来是「两十万」「三十两万年」，全错。"""
    if n == 2:
        return "两"
    s = _cn_num(n)
    if s.startswith("二") and len(s) > 1 and s[1] in "百千万":
        s = "两" + s[1:]
    return s


def digits_to_cn(text: str) -> str:
    """阿拉伯数字写回中文（2026-11-02 口径）：只转「数量级 + 量词」前面的数。

    转：800->八百、2,000->两千、12,000->一万两千、1,500万->一千五百万、
        32万->三十二万、2万->两万、9千->九千、3个月->三个月、32岁->三十二岁、倒数第2->倒数第二。
    不转：985 / 211 / 1v1 / 4A / B站 / 0offer、百分比（90%）、日期档位（2008年、12月份、
        27届、25年）、70后、1+1、以及夹在数字串里的位（8,0009,000 这种 ASR 粘连，
        由 PRE_TERMS 先整串换掉）。
    """
    # 1) 带千分位的（1,000 / 12,000 / 1,500万）
    def grp(m):
        raw, tail, after = m.group(1), m.group(2) or "", m.group(3) or ""
        n = int(raw.replace(",", ""))
        if not tail and not after:
            if "," not in raw and 1900 <= n <= 2100:
                return m.group(0)      # 裸年份
        return _cn_qty(n) + tail + after

    # 千分位金额（2,000 / 1,500万）；带逗号的绝不是年份，后面跟 年/月/日/届/后 的也不动
    text = re.sub(r"(?<![\d.,A-Za-z%])(\d{1,3}(?:,\d{3})+)(" + CN_TAIL + r")?(年|月|日|届|后)?",
                  grp, text)

    # 2) 裸整数，必须紧跟数量级/量词
    def bare(m):
        n = int(m.group(1))
        if 1900 <= n <= 2100:
            return m.group(0)
        return _cn_qty(n) + m.group(2)

    text = re.sub(r"(?<![\d.,A-Za-z%])(\d{1,4})(" + CN_TAIL + r")", bare, text)

    # 3) 万/千位后面的尾数：一万6 -> 一万六（ASR 只转了前面的 1 会留下半中半西）
    text = re.sub(r"(?<=[千百十万])([1-9])(?![0-9.,%])",
                  lambda m: _cn_num(int(m.group(1))), text)

    # 3b) 单位在前的金额：9000月薪 -> 九千月薪（ASR 常把数字写在 月薪/年薪/工资 前面）
    text = re.sub(r"(?<![\d.,A-Za-z])(\d{3,4})(?=\s*(?:月薪|年薪|工资|块))",
                  lambda m: _cn_qty(int(m.group(1))), text)

    # 3) 序数：倒数第2 -> 倒数第二（这里 2 读「二」）
    text = re.sub(r"第(\d)(?![\d.,%])", lambda m: "第" + _cn_num(int(m.group(1))), text)
    return text



def correct(text: str) -> str:
    text = text.strip()
    for a, b in PRE_TERMS.items():
        text = text.replace(a, b)
    text = digits_to_cn(text)
    # 数字保持中文写法（七八千、一万），不转阿拉伯——见 SKILL.md 字幕规则
    # 单字口吃：说说着 / 工工资 / 有有一个 / 考考 —— 只收这几个高频虚字，
    # 看看/试试/谢谢/慢慢/刚刚 不在此列，不会被误伤。
    text = re.sub(r"([" + STUTTER_CHARS + r"])\1+", r"\1", text)
    for a, b in TERMS.items():
        text = text.replace(a, b)
    text = re.sub(r"(?<![A-Za-z])at(?![A-Za-z])", "@", text, flags=re.I)
    # 「98521」= 他说 985、211；但不能在 98521985 这种粘连串里瞎拆（会把 985/211 切到两条字幕上）
    text = re.sub(r"98521(?![\d/])", "985/211", text)

    # amounts
    text = re.sub(r"(\d{1,3}(?:,\d{3})+)\1", r"\1", text)
    text = re.sub(r"(\d{2})\1[Kk]", r"\1,000", text)

    def wan(m):
        v = float(m.group(1)) * 10000
        if m.group(2):
            v += (int(m.group(2)) if m.group(2).isdigit() else "一二三四五六七八九".index(m.group(2)) + 1) * 1000
        return f"{int(round(v)):,}"
    #text = re.sub(r"([0-9]+(?:\.[0-9]+)?)万([0-9一二三四五六七八九])?", wan, text)

    def qian(m):
        v = float(m.group(1)) * 1000
        if m.group(2):
            v += (int(m.group(2)) if m.group(2).isdigit() else "一二三四五六七八九".index(m.group(2)) + 1) * 100
        return f"{int(round(v)):,}"
    #text = re.sub(r"([0-9]+(?:\.[0-9]+)?)千([0-9一二三四五六七八九])?", qian, text)

    def k(m):
        return f"{int(round(float(m.group(1)) * 1000)):,}"
    #text = re.sub(r"([0-9]+(?:\.[0-9]+)?)[Kk]", k, text)

    def plain(m):
        n = int(m.group(0))
        if 1900 <= n <= 2100:
            return m.group(0)
        return f"{n:,}"
    #text = re.sub(r"(?<![\d,])\d{4,}(?![\d,年届岁月日])", plain, text)

    # collapse repeats
    for tok in REPEAT_TOKENS:
        text = re.sub(r"(?:" + re.escape(tok) + r"){2,}", tok, text)
    # generic 2-char token stutter: 面了面了 -> 面了, 一个一个 -> 一个
    AA = set(AA_WHITELIST) | REDUPLICATE | {"爸爸", "妈妈", "爷爷", "奶奶", "叔叔", "阿姨", "宝宝", "娃娃", "星星", "婷婷"}
    def two_char(m):
        return m.group(1) if m.group(1) not in AA else m.group(0)
    text = re.sub(r"([\u4e00-\u9fff]{2})\1", two_char, text)
    text = re.sub(r"([\u4e00-\u9fff]{3})\1", lambda m: m.group(1) if m.group(1) not in {"一天一天", "一年一年"} else m.group(0), text)
    # repeated 2-5 char phrase containing Chinese, e.g. 想转UE想转UE
    text = re.sub(r"([\u4e00-\u9fffA-Za-z]{2,5})\1",
                  lambda m: m.group(1) if re.search(r"[\u4e00-\u9fff]", m.group(1)) and m.group(1) not in AA else m.group(0),
                  text)
    text = re.sub(r"的的(?=[，。！？\s]|$)", "的", text)
    text = re.sub(r"(?<=[A-Za-z])现(?=但是|但|不过)", "", text)
    text = re.sub(r"(所以|然后|但是|就是|因为|其实)[哎呃啊唉哦嗯]?\1", r"\1", text)
    text = re.sub(r"(破|烂|差|难|很|太|非|更|最)\1", r"\1", text)
    text = re.sub(r"([^\d\s])\1{2,}", lambda m: m.group(1) if m.group(1) not in "哈嘿呵嘻" else m.group(0), text)
    return text.strip()


def is_filler(text: str) -> bool:
    n = norm(text)
    return 0 < len(n) <= 3 and all(c in FILLERS for c in n)


def strip_edges(text: str) -> str:
    text = text.strip(" ，,。.!！？?、")
    while text and text[0] in FILLERS:
        text = text[1:].lstrip(" ，,。.!！？?、")
    while text and text[-1] in FILLERS:
        text = text[:-1].rstrip(" ，,。.!！？?、")
    return text


def similar(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def split_text(text: str) -> list[str]:
    if len(text) <= MAX_CHARS_HARD:
        return [text]

    def protected(cut: int) -> bool:
        for term in PROTECTED:
            start = 0
            while True:
                i = text.find(term, start)
                if i < 0:
                    break
                if i < cut < i + len(term):
                    return True
                start = i + 1
        return False

    def fits(cut: int) -> bool:
        return 0 < cut < len(text) and cut <= MAX_CHARS_HARD and len(text) - cut <= MAX_CHARS_HARD and not protected(cut)

    punct = "，,。！？；：、 "
    pc = []
    for i, ch in enumerate(text):
        if ch in punct and fits(i + 1):
            if ch in ",." and 0 < i < len(text) - 1 and text[i - 1].isdigit() and text[i + 1].isdigit():
                continue
            pc.append(i + 1)
    if pc:
        cut = min(pc, key=lambda p: abs(p - len(text) / 2))
        return [text[:cut].strip(), text[cut:].strip()]

    try:
        import jieba
    except ModuleNotFoundError:
        cut = min(MAX_CHARS_HARD, len(text) - 1)
        while cut > 1 and protected(cut):
            cut -= 1
        return [text[:cut].strip(), text[cut:].strip()]
    words = list(jieba.cut(text))
    end_len, start_len = {}, {}
    pos = 0
    for i, word in enumerate(words):
        pos += len(word)
        end_len[pos] = len(word)
        if i + 1 < len(words):
            start_len[pos] = len(words[i + 1])
    bounds = []
    for p in end_len:
        if not (0 < p < len(text)) or p > MAX_CHARS_HARD or len(text) - p > MAX_CHARS_HARD:
            continue
        a, b = text[p - 1], text[p]
        if (a.isascii() and b.isascii()
                and (a.isalnum() or a in ".,:%") and (b.isalnum() or b in ".,:%")):
            continue  # inside a number/English token
        if b in "的了是在就很把被给和跟对为后上下里中前时":
            continue  # never cut immediately before these
        bounds.append(p)
    if bounds:
        target = min(MAX_CHARS, max(6, len(text) - 4))   # 仍以 12 为目标，14 才兜底
        clause = set("刚后然但所因不其我你他她这那就都也还又才")
        def penalty(p: int) -> int:
            s = abs(p - target)
            if text[p - 1] in "了着过吧呢吗啊":
                s -= 2
            if text[p] in clause:
                s -= 5
            if text[p - 1] in FUNC:
                s += 1
            if text[p - 1] in "很太非更最":
                s += 3
            if len(text) - p < 3:
                s += 4
            if p < 4:
                s += 2
            return s
        cut = min(bounds, key=lambda p: (penalty(p), abs(p - target)))
        return [text[:cut].strip(), text[cut:].strip()]

    cut = min(MAX_CHARS_HARD, len(text) - 1)
    while cut > 1 and protected(cut):
        cut -= 1
    return [text[:cut].strip(), text[cut:].strip()]


def load_text(mat: dict) -> dict:
    try:
        return json.loads(mat.get("content") or "{}")
    except json.JSONDecodeError:
        return {}


def set_text(mat: dict, text: str) -> None:
    obj = load_text(mat)
    obj["text"] = text
    if obj.get("styles"):
        obj["styles"][0]["range"] = [0, len(text)]
        obj["styles"] = obj["styles"][:1]
    mat["content"] = json.dumps(obj, ensure_ascii=False)
    mat["recognize_text"] = text
    mat["words"] = {}
    mat["current_words"] = {}
    mat["line_max_width"] = 1.0
    mat["force_apply_line_max_width"] = True


def subtitles_of(d: dict) -> list[dict]:
    idmap = {}
    for cat, arr in (d.get("materials") or {}).items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    idmap[m["id"]] = (cat, m)
    out = []
    for t in d.get("tracks") or []:
        for s in t.get("segments") or []:
            _, mat = idmap.get(s.get("material_id"), ("", {}))
            if mat.get("type") != "subtitle":
                continue
            tr = s.get("target_timerange") or {}
            out.append({
                "start": float(tr.get("start") or 0) / 1e6,
                "end": float((tr.get("start") or 0) + (tr.get("duration") or 0)) / 1e6,
                "text": load_text(mat).get("text", ""),
            })
    return sorted(out, key=lambda x: x["start"])


def parse_srt(path: str) -> list[dict]:
    import re
    text = Path(path).read_text(encoding="utf-8-sig")
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, b = lines[1].split("-->")
        def ts(s):
            h, m, rest = s.strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))
        txt = " ".join(lines[2:]).strip().replace("<br>", " ").replace("<br/>", " ")
        if txt:
            out.append({"start": ts(a), "end": ts(b), "text": txt})
    return sorted(out, key=lambda x: x["start"])


def rework(subs: list[dict], duration: float) -> tuple[list[dict], dict]:
    report = {"input": len(subs), "dropped_filler": 0, "duplicates": 0, "merged": 0,
              "corrections": 0, "split_extra": 0}
    lines = []
    for s in subs:
        text = correct(s["text"])
        if text != s["text"]:
            report["corrections"] += 1
        text = strip_edges(text)
        if not text:
            report["dropped_filler"] += 1
            continue
        if is_filler(text):
            report["dropped_filler"] += 1
            if lines:
                lines[-1]["end"] = max(lines[-1]["end"], s["end"])
            continue
        if lines:
            prev = lines[-1]
            gap = s["start"] - prev["end"]
            combined = prev["text"] + text
            # duplicate / near-duplicate
            if gap <= 0.8 and similar(prev["text"], text) >= 0.90 and len(norm(text)) >= 3:
                report["duplicates"] += 1
                prev["end"] = max(prev["end"], s["end"])
                continue
            # merge short pause / short fragment
            can_merge = not prev["text"].endswith(("吗", "呢", "吧", "。", "！", "？"))
            if text and text[0] in "我你他她它" and len(norm(prev["text"])) >= 4:
                can_merge = False
            if can_merge and gap <= 0.95 and len(combined) <= MAX_CHARS_HARD:
                prev["text"] = combined
                prev["end"] = s["end"]
                report["merged"] += 1
                continue
            if can_merge and gap <= 0.60 and len(combined) <= 15:
                prev["text"] = combined
                prev["end"] = s["end"]
                report["merged"] += 1
                continue
            if can_merge and len(norm(prev["text"])) <= 2 and gap <= 0.9 and len(combined) <= MAX_CHARS_HARD:
                prev["text"] = combined
                prev["end"] = s["end"]
                report["merged"] += 1
                continue
            # tiny fragment: merge even if it must be re-split afterwards
            if gap <= 0.60 and (len(norm(prev["text"])) <= 3 or len(norm(text)) <= 3) and len(combined) <= 18:
                prev["text"] = combined
                prev["end"] = s["end"]
                report["merged"] += 1
                continue
            if can_merge and gap <= 0.35 and len(combined) <= 16:
                prev["text"] = combined
                prev["end"] = s["end"]
                report["merged"] += 1
                continue
        lines.append({"start": s["start"], "end": s["end"], "text": text})

    # split >MAX_CHARS and allocate time
    for line in lines:
        line["text"] = strip_edges(correct(line["text"]))
    parts = []
    for line in lines:
        chunks = split_text(line["text"])
        if len(chunks) > 1:
            report["split_extra"] += len(chunks) - 1
        total = sum(len(c) for c in chunks) or 1
        cur = line["start"]
        span = max(0.12, line["end"] - line["start"])
        for i, chunk in enumerate(chunks):
            end = line["end"] if i == len(chunks) - 1 else cur + span * len(chunk) / total
            parts.append({"start": round(cur, 3), "end": round(end, 3), "text": chunk})
            cur = end

    # zero-gap / coverage. Speech pauses may stay on screen blank for at most
    # MAX_BLANK seconds: a real long pause keeps a small seam, but the previous
    # line holds the rest of it, so coverage never collapses on a short clip.
    MAX_BLANK = 1.0
    if parts:
        parts[0]["start"] = max(0.0, parts[0]["start"] - MAX_BLANK)
        for a, b in zip(parts, parts[1:]):
            if b["start"] < a["end"]:
                b["start"] = a["end"]                    # overlap -> butt them together
            elif b["start"] - a["end"] <= MAX_BLANK:
                a["end"] = b["start"]                     # short pause: 0 缝, covers the breath
            else:
                a["end"] = b["start"] - MAX_BLANK          # real long pause: keep a <=1s seam
        parts[-1]["end"] = min(duration, parts[-1]["end"] + MAX_BLANK)
        # enforce minimum duration without breaking the chain
        for i, p in enumerate(parts):
            if p["end"] - p["start"] < 0.18:
                p["end"] = min(duration, p["start"] + 0.18)
                if i + 1 < len(parts) and parts[i + 1]["start"] < p["end"]:
                    parts[i + 1]["start"] = p["end"]

    report["output"] = len(parts)
    report["coverage"] = round(sum(p["end"] - p["start"] for p in parts) / duration * 100, 1) if duration else 0
    return parts, report


def video_segments(d: dict) -> list[dict]:
    idmap = {}
    for cat, arr in (d.get("materials") or {}).items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    idmap[m["id"]] = (cat, m)
    out = []
    for t in d.get("tracks") or []:
        for s in t.get("segments") or []:
            _, mat = idmap.get(s.get("material_id"), ("", {}))
            if mat.get("type") != "video":
                continue
            tr = s.get("target_timerange") or {}
            sr = s.get("source_timerange") or {}
            out.append({
                "start": float(tr.get("start") or 0) / 1e6,
                "end": float((tr.get("start") or 0) + (tr.get("duration") or 0)) / 1e6,
                "source_start": float(sr.get("start") or 0) / 1e6,
                "source_end": float((sr.get("start") or 0) + (sr.get("duration") or 0)) / 1e6,
                "speed": float(s.get("speed") or 1.0),
            })
    return sorted(out, key=lambda x: x["start"])


def to_source(segs: list[dict], t: float) -> float | None:
    for s in segs:
        if s["start"] - 1e-6 <= t < s["end"] + 1e-6:
            return s["source_start"] + (t - s["start"]) * s["speed"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True, help="plaintext draft_content.json")
    ap.add_argument("--template", required=True, help="plaintext reference timeline draft_content.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--srt", default=None, help="use an SRT (e.g. VideoCaptioner output) instead of draft subtitles")
    ap.add_argument("--anchors", default=None, help="write source-time anchors JSON for later recut remapping")
    ap.add_argument("--preset", default=str(Path(__file__).resolve().parent.parent / "assets" / "preset.json"))
    ap.add_argument("--font-cache", default=str(Path.home() / "AppData/Local/JianyingPro/User Data/Cache"))
    ap.add_argument("--no-style-fix", action="store_true",
                    help="keep the template timeline's subtitle font/size/position instead of the fixed preset")
    args = ap.parse_args()

    PRESET = json.loads(Path(args.preset).read_text(encoding="utf-8")) if Path(args.preset).exists() else {}
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_draft import apply_subtitle_style, find_font

    font_name = (PRESET.get("subtitle") or {}).get("font") or ""
    FIXED_FONT = None if args.no_style_fix or not font_name else find_font(font_name, [Path(args.font_cache)])

    d = json.loads(Path(args.content).read_text(encoding="utf-8"))
    tpl = json.loads(Path(args.template).read_text(encoding="utf-8"))
    duration = float(d.get("duration") or 0) / 1e6
    subs = parse_srt(args.srt) if args.srt else subtitles_of(d)
    parts, report = rework(subs, duration)

    # template subtitle material/segment
    tidmap = {}
    for cat, arr in tpl.get("materials", {}).items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    tidmap[m["id"]] = (cat, m)
    sub_tpl_track = next(t for t in tpl["tracks"] if t.get("segments") and
                         tidmap.get(t["segments"][0]["material_id"], ("", {}))[1].get("type") == "subtitle")
    sub_tpl_seg = sub_tpl_track["segments"][0]
    sub_tpl_mat = tidmap[sub_tpl_seg["material_id"]][1]

    # remove old subtitle tracks/materials
    idmap = {}
    for cat, arr in d.get("materials", {}).items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and m.get("id"):
                    idmap[m["id"]] = (cat, m)
    keep_tracks = []
    for t in d.get("tracks", []):
        if t.get("segments"):
            _, mat = idmap.get(t["segments"][0].get("material_id"), ("", {}))
            if mat.get("type") == "subtitle":
                continue
        keep_tracks.append(t)

    new_segments = []
    new_ids = set()
    for p in parts:
        mat = copy.deepcopy(sub_tpl_mat)
        mat["id"] = uid()
        set_text(mat, p["text"])
        d["materials"].setdefault("texts", []).append(mat)
        new_ids.add(mat["id"])
        seg = copy.deepcopy(sub_tpl_seg)
        seg["id"] = uid()
        seg["material_id"] = mat["id"]
        seg["target_timerange"] = {"start": int(round(p["start"] * 1e6)),
                                   "duration": int(round((p["end"] - p["start"]) * 1e6))}
        seg["extra_material_refs"] = []
        seg["render_index"] = 15000
        seg["track_render_index"] = 8
        if not args.no_style_fix:
            apply_subtitle_style(mat, seg, PRESET, FIXED_FONT or sub_tpl_mat.get("font_path"))
        new_segments.append(seg)

    new_track = copy.deepcopy(sub_tpl_track)
    new_track["id"] = uid()
    new_track["segments"] = new_segments
    keep_tracks.append(new_track)
    d["tracks"] = keep_tracks

    # drop unreferenced subtitle materials
    d["materials"]["texts"] = [m for m in d["materials"].get("texts", [])
                               if m.get("type") != "subtitle" or m.get("id") in new_ids]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    if args.anchors:
        segs = video_segments(d)
        anchors = []
        for p in parts:
            a, b = to_source(segs, p["start"]), to_source(segs, p["end"])
            anchors.append({"target_start": p["start"], "target_end": p["end"],
                            "source_start": None if a is None else round(a, 3),
                            "source_end": None if b is None else round(b, 3),
                            "text": p["text"]})
        apath = Path(args.anchors)
        apath.parent.mkdir(parents=True, exist_ok=True)
        apath.write_text(json.dumps(anchors, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.report:
        rp = Path(args.report); rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
