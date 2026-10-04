# -*- coding: utf-8 -*-
"""
题库重排工具：把《底层经典问题集.md》的段落式排版，转为可扫读的卡片式排版。

用法：
    python reflow_interview_bank.py            # 重写目标文件（自动备份到 archive/）
    python reflow_interview_bank.py --dry      # 只预览前 3 题 + 生成的索引区

改脚本前请先读「设计约束」注释：
1. 只做格式转换，**不改动题目内容的任何一个字**，所有原文照搬。
2. 交叉引用（"见 Q59"、"接 Q35"）依赖题号 —— 因此题号**不补零**，避免引用失效。
3. 代码块（```）无条件透传，不做任何处理。
4. 每次重写前自动备份到 archive/ 并带时间戳，可随时回滚。
"""
import re
import sys
import shutil
import datetime
from pathlib import Path

ROOT = Path(r"D:\STM32F407VET6")
SRC = ROOT / "底层经典问题集.md"
ARCHIVE = ROOT / "archive"

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

# 每次转换收集到的题元信息，用于生成顶部的索引区
COLLECTED = []


# ---------------------------------------------------------------- 行级格式化

def _strip_outer_bold(s: str) -> str:
    """'**整句加粗**' -> '整句加粗'。仅在整段被一层 ** 包裹时去除。"""
    if s.startswith("**") and s.endswith("**") and s.count("**") == 2:
        return s[2:-2]
    return s


def split_colon_head(text: str) -> str:
    """把『语义：xxx』的开头短语加粗，形成左列视觉锚点。
    仅在 head 足够短且不含句读时使用 —— 长句加粗反而更难扫读。"""
    text = text.strip()
    if text.startswith("**"):
        return text
    for sep in ("：", ":"):
        pos = text.find(sep)
        if 0 < pos <= 26:
            head = text[:pos]
            if not any(c in head for c in "，。？！；、"):
                rest = _strip_outer_bold(text[pos + len(sep):])
                return f"**{head}**{sep}{rest}"
        if pos > 0:
            break
    return text


def parse_point(raw: str) -> str:
    """'① xxx' -> 'xxx'（编号在渲染时重排）。"""
    raw = raw.lstrip()
    m = re.match(r"^([" + CIRCLED + r"])\s*(.*)$", raw)
    return split_colon_head(m.group(2) if m else raw)


def parse_chain(raw: str) -> str:
    """'→ 问？（答）' -> '- **问？** → 答'"""
    text = raw.lstrip()
    if text.startswith("→"):
        text = text[1:].lstrip()
    text = re.sub(r"^(->|—>)\s*", "", text).strip()
    pos = text.find("？")
    if pos == -1:
        pos = text.find("?")
    if pos == -1:
        return "- " + text
    q = text[: pos + 1].strip()
    a = text[pos + 1:].strip()
    if a.startswith("（") and a.endswith("）"):
        a = a[1:-1]
    elif a.startswith("(") and a.endswith(")"):
        a = a[1:-1]
    return f"- **{q}** → {a}"


def parse_meta(line: str):
    """'**频率**：★★★ ｜ **岗位**：通用（**必考**）' -> ('★★★', ['通用','必考'])"""
    freq, tags = "", []
    body = line.split("：", 1)[1] if "：" in line else line
    for idx, p in enumerate([x.strip() for x in body.split("｜")]):
        clean = p.replace("**", "").strip()
        if clean.startswith("频率"):
            freq = clean.replace("频率", "").lstrip(" ：").strip()
        elif clean.startswith("岗位"):
            role = re.sub(r"^岗位\s*[：:]?\s*", "", clean).strip()
            m = re.match(r"^(.*?)（(.*?)）$", role)
            if m:
                tags += [m.group(1).strip(), m.group(2).strip()]
            elif role:
                tags.append(role)
        elif idx == 0 and not freq and clean:
            # 没有「频率：」前缀时，第一段就是频率
            freq = clean.lstrip(" ：").strip()
    return freq, [t for t in tags if t]


def parse_score(line: str):
    """'**★加分 / ⚠️减分**：★ xxx ｜ ⚠️ yyy' -> ('xxx','yyy')"""
    body = line.split("：", 1)[1] if "：" in line else line
    if "｜ ⚠️" in body:
        plus, minus = body.split("｜ ⚠️", 1)
    elif "｜⚠️" in body:
        plus, minus = body.split("｜⚠️", 1)
    else:
        m = re.search(r"\s⚠️\s*", body)
        if m:
            plus, minus = body[: m.start()], body[m.end():]
        else:
            return body.strip(), ""
    return re.sub(r"^[★\s]+", "", plus).strip(), minus.strip()


# ---------------------------------------------------------------- 块级渲染

def render_qblock(block: list) -> list:
    m = re.match(r"^### (Q\d+)\.\s*(.*)$", block[0])
    qid, title = m.group(1), m.group(2).strip()

    freq, tags, ask = "", [], ""
    points, chains = [], []
    plus, minus = "", ""
    state = None

    for line in block[1:]:
        s = line.rstrip()
        if not s:
            continue
        if s.startswith("**频率**"):
            freq, tags = parse_meta(s)
            state = None
        elif s.startswith("**面试官**"):
            ask = s.split("：", 1)[1].strip() if "：" in s else ""
            state = None
        elif s.startswith("**得分点**"):
            state = "points"
        elif s.startswith("**追问链**"):
            state = "chain"
        elif s.startswith("**★加分"):
            plus, minus = parse_score(s)
            state = None
        elif state == "points":
            if re.match(r"^\s{2,}", s) and points:
                points[-1] += "\n" + s
            else:
                points.append(parse_point(s))
        elif state == "chain":
            if re.match(r"^\s{2,}", s) and chains:
                chains[-1] += "\n   " + s.lstrip()
            else:
                chains.append(parse_chain(s))

    COLLECTED.append((qid, title, freq, tags))

    out = [f"### {qid} · {title}", ""]
    if freq or tags:
        out.append(" ".join(([f"`{freq}`"] if freq else []) + [f"`{t}`" for t in tags]))
        out.append("")
    if ask:
        out.append(f"> **面试官**：{ask}")
        out.append("")

    if points:
        out += ["**得分点**", ""]
        for idx, p in enumerate(points, 1):
            first, *rest = p.split("\n")
            out.append(f"{idx}. {first}")
            out += rest
        out.append("")

    if chains:
        out += ["**追问链**", ""] + chains + [""]

    if plus:
        out.append(f"**加分** — {plus}")
    if minus:
        out.append(f"**避坑** — {minus}")
    if plus or minus:
        out.append("")

    out += ["**自测** [ ] 得分点能全部说出　[ ] 追问答穿 2 层", ""]
    return out


# ---------------------------------------------------------------- 顶部索引区

def build_header(lines: list) -> list:
    """用可读的索引区替换原本塞在一起的 quote 块。"""
    modules = []
    for line in lines:
        m = re.match(r"^# ([一二三四五六七八九十]+)、(.+?)（(Q\d+)\s*~\s*(Q\d+)）", line)
        if m:
            modules.append((m.group(1), f"{m.group(1)}、{m.group(2).strip()}",
                            m.group(3), m.group(4)))
            continue
        m2 = re.match(r"^# ([一二三四五六七八九十]+)、(.+)$", line)
        if m2:  # 「十二、收尾」这类没有题号区间的章节
            modules.append((m2.group(1), f"{m2.group(1)}、{m2.group(2).strip()}", "—", "—"))

    star3 = [(q, t) for q, t, f, _ in COLLECTED if f.count("★") >= 3]
    star2 = [(q, t) for q, t, f, _ in COLLECTED if f.count("★") == 2]

    head = [
        "# 嵌入式社招面试题库 —— 底层 / 电机控制方向",
        "",
        "| 项 | 内容 |",
        "|---|---|",
        "| 定位 | **社招（非校招）**——考深度追问、权衡取舍、项目所有权、排障方法论 |",
        f"| 规模 | **{len(COLLECTED)} 题 / {len(modules)} 模块**；每题 = 面试官原话 + 得分点 + 追问链 + 加分/避坑 + 自测勾选 |",
        "| 内容版本 | 2026-09-12（高危数值已对照 ARM TRM / ST RM / 内核源码核查，**修正 Q25 位带错误**）|",
        "| 排版版本 | 卡片式重排（脚本 `tools/reflow_interview_bank.py`，可重复执行、自动备份）|",
        "| 配套 | 工程实战向题库《底层工程实践问题集.md》——**就业后再用**；本库只服务「先拿到 offer」|",
        "",
        "**怎么用**：第一遍扫一眼下面的 ★★★ 表，看哪些题你「听不懂问题」——那就是知识缺口；"
        "第二遍照着卡片答题，**讲出声**；第三遍专攻没打勾的自测项。",
        "",
        "---",
        "",
        "## 目录",
        "",
        "| 模块 | 题号 | 说明 |",
        "|---|---|---|",
    ]
    weights = {
        "一": "一面必考，要挖到「编译器视角」才算过关",
        "二": "ROM / RAM 是怎么被切出来的",
        "三": "内核行为 + HardFault 定位",
        "四": "要能算得出来，不只是会配",
        "五": "选型理由与排障方法论",
        "六": "**方向核心**，决定你和别的候选人有没有区别",
        "七": "裸机选手最容易失分的地方",
        "八": "笔试 / 白板，写完要能自己 review 边界",
        "九": "**社招决胜**，项目讲不出就没有对手答卷",
        "十": "**差异化优势项**，直接对应你的在职双核项目",
        "十一": "主管面 / 交叉面加分，决定你像不像「圈里人」",
        "十二": "考前最后一天用：反问清单 + 翻车点自检 + 冲刺表",
    }
    for num, name, a, b in modules:
        head.append(f"| {name} | {a} ~ {b} | {weights.get(num, '')} |")

    head += ["", "---", "", f"## ★★★ 速查表（{len(star3)} 题）", ""]
    head.append("> 面试前一晚、候场、前一轮刚答崩时的唯一清单。"
                "**看题号能不能立刻想起 3 个得分点**，想不起直接跳到正文。")
    head += ["", "| 题号 | 问题 | 题号 | 问题 |", "|---|---|---|---|"]
    for i in range(0, len(star3), 2):
        row = [star3[i][0], star3[i][1]]
        if i + 1 < len(star3):
            row += [star3[i + 1][0], star3[i + 1][1]]
        else:
            row += ["", ""]
        head.append("| " + " | ".join(row) + " |")

    if star2:
        head += ["", f"## ★★ 补充题（{len(star2)} 题）", "",
                 "| 题号 | 问题 | 题号 | 问题 |", "|---|---|---|---|"]
        for i in range(0, len(star2), 2):
            row = [star2[i][0], star2[i][1]]
            row += ([star2[i + 1][0], star2[i + 1][1]] if i + 1 < len(star2) else ["", ""])
            head.append("| " + " | ".join(row) + " |")

    head += ["", "---", ""]
    return head


# ---------------------------------------------------------------- 主流程

def is_boundary(line: str) -> bool:
    return bool(re.match(r"^#{1,3} ", line) or re.match(r"^---\s*$", line))


def reflow(text: str) -> str:
    lines = text.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if re.match(r"^### (Q\d+)\.", line):
            block = [line]
            j = i + 1
            while j < n and not is_boundary(lines[j]):
                block.append(lines[j])
                j += 1
            out.extend(render_qblock(block))
            i = j
        else:
            out.append(line)
            i += 1

    # 用索引区替换原来的 quote 抬头（正文首个 ## 之前的内容）
    try:
        end = out.index("## 〇、先读这一节：社招的规则不同于校招")
    except ValueError:
        end = 0
    reflowed = build_header(out) + out[end:]
    return "\n".join(reflowed)


def main():
    args = set(sys.argv[1:])
    dry = "--dry" in args
    raw = SRC.read_text(encoding="utf-8")

    # ---- 模式二：只按现有正文重建顶部索引（幂等，不动正文一个字节）----
    if "--rebuild-index" in args:
        header, body = rebuild_index(raw)
        if dry:
            print("\n".join(header))
            print(f"[dry] 顶部索引：{count_stars(body, 3)} 个 ★★★ / "
                  f"{count_stars(body, 2)} 个 ★★")
            return
        backup_path = backup(raw)
        write_text_lf("\n".join(header + body))
        print(f"[ok] 备份 -> {backup_path}")
        print(f"[ok] 重建顶部索引 -> {SRC}（正文未改动）")
        return

    # ---- 安全闸：已排版文件绝不能走单向转换，否则顶部索引会被清空 ----
    if re.search(r"^### Q\d+ · ", raw, re.M):
        print("[abort] 检测到文件已是「卡片式排版」（形如 `### Q1 · ...`）。")
        print("        本模式的转换是【单向】的：段落式源文本 -> 卡片式。")
        print("        对已排版文件重跑会清空顶部索引（规模变 0 题、速查表变空），故已中止。")
        print("        若只是想按当前正文重建顶部索引，请执行：")
        print("            python tools/reflow_interview_bank.py --rebuild-index")
        sys.exit(1)

    if dry:
        new = reflow(raw)
        i = new.find("### Q1 ·")
        j = new.find("### Q4 ·")
        print(new[:i])
        print("<<< 正文样例 >>>")
        print(new[i:j])
        print(f"\n[统计] 共收集 {len(COLLECTED)} 题")
        print(f"[统计] ★★★ {sum(1 for _,_,f,_ in COLLECTED if f.count('★')>=3)} 题")
        return

    new = reflow(raw)
    backup_path = backup(raw)
    write_text_lf(new)
    n3 = sum(1 for _, _, f, _ in COLLECTED if f.count("★") >= 3)
    print(f"[ok] 备份 -> {backup_path}")
    print(f"[ok] 重写 -> {SRC}")
    print(f"[统计] {len(COLLECTED)} 题；★★★ {n3} 题")


def write_text_lf(text: str):
    """以 UTF-8 + LF 写回，避免 Windows 下 text 模式把 \\n 翻成 CRLF（仓库统一 LF）。"""
    with open(SRC, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ------------------------------------------------- 顶部索引重建（对已排版文件幂等）

INDEX_WEIGHTS = {
    "一": "一面必考，要挖到「编译器视角」才算过关",
    "二": "ROM / RAM 是怎么被切出来的",
    "三": "内核行为 + HardFault 定位",
    "四": "要能算得出来，不只是会配",
    "五": "选型理由与排障方法论",
    "六": "**方向核心**，决定你和别的候选人有没有区别",
    "七": "裸机选手最容易失分的地方",
    "八": "笔试 / 白板，写完要能自己 review 边界",
    "九": "**社招决胜**，项目讲不出就没有对手答卷",
    "十": "**差异化优势项**，直接对应你的在职双核项目",
    "十一": "主管面 / 交叉面加分，决定你像不像「圈里人」",
    "十二": "**2026-10 增补**：补齐短板（主线深化 + 通用缺口 + 硬件/系统实战）",
    "十三": "考前最后一天用：反问清单 + 翻车点自检 + 冲刺表",
}

INDEX_VERSION = ("| 内容版本 | 2026-10-05（**模块十二扩充为 Q94 ~ Q111**：频域/滤波/轨迹规划/参数辨识/无感实现、"
                 "Flash/内存池/低功耗/内存屏障、力控与阻抗、驱动硬件、EtherCAT/CiA402、电源、边缘 AI 落地）；"
                 "原 93 题的高危数值已对照 ARM TRM / ST RM / 内核源码核查（**修正 Q25 位带错误**）|")
INDEX_LAYOUT = ("| 排版版本 | 卡片式手工排版。顶部索引可用 `python tools/reflow_interview_bank.py --rebuild-index` "
                "从正文自动重建（**幂等、只改顶部、不动正文**）；不带参数的单向转换只适用于段落式源文本，"
                "对已排版文件会自动中止 |")


def backup(raw: str):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    ARCHIVE.mkdir(exist_ok=True)
    path = ARCHIVE / f"底层经典问题集-排版v1-{ts}.md"
    shutil.copy2(SRC, path)
    return path


def count_stars(lines, n: int) -> int:
    return sum(1 for l in lines if re.match(r"^`" + "★" * n + r"`$", l.strip()[: n + 2]))


def collect_from_rendered(lines: list):
    """从已排版正文里抽出模块与题目元信息（标题行 + 紧随的 `★★★` 元信息行）。"""
    modules, questions = [], []
    for i, line in enumerate(lines):
        m = re.match(r"^# ([一二三四五六七八九十]+)、(.+)$", line)
        if m:
            num, title = m.group(1), m.group(2).strip()
            rng = re.search(r"（(Q\d+)\s*~\s*(Q\d+)）", title)
            name = re.sub(r"（Q\d+\s*~\s*Q\d+）", "", title)
            name = re.sub(r"\s*★.*$", "", name).strip()
            modules.append((num, f"{num}、{name}",
                            rng.group(1) if rng else "—", rng.group(2) if rng else "—"))
            continue
        m = re.match(r"^### (Q\d+) · (.+)$", line)
        if m:
            qid, title = m.group(1), m.group(2).strip()
            freq = ""
            for s in lines[i + 1: i + 6]:
                s = s.strip()
                if not s:
                    continue
                fm = re.match(r"^`(★+)`", s)
                if fm:
                    freq = fm.group(1)
                break
            questions.append((qid, title, freq))
    return modules, questions


def _pair_rows(items: list):
    rows = []
    for i in range(0, len(items), 2):
        row = [items[i][0], items[i][1]]
        row += [items[i + 1][0], items[i + 1][1]] if i + 1 < len(items) else ["", ""]
        rows.append("| " + " | ".join(row) + " |")
    return rows


def rebuild_index(text: str):
    """返回 (顶部索引行, 正文行)；正文行原样返回。"""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("## 〇、")), None)
    if start is None:
        raise SystemExit("[abort] 找不到正文起点（## 〇、先读这一节：社招的规则不同于校招），未做改动")
    body = lines[start:]
    modules, questions = collect_from_rendered(body)
    star3 = [(q, t) for q, t, f in questions if f.count("★") >= 3]
    star2 = [(q, t) for q, t, f in questions if f.count("★") == 2]

    header = [
        "# 嵌入式社招面试题库 —— 底层 / 电机控制方向",
        "",
        "| 项 | 内容 |",
        "|---|---|",
        "| 定位 | **社招（非校招）**——考深度追问、权衡取舍、项目所有权、排障方法论 |",
        f"| 规模 | **{len(questions)} 题 / {len(modules)} 模块**；"
        "每题 = 面试官原话 + 得分点 + 追问链 + 加分/避坑 + 自测勾选 |",
        INDEX_VERSION,
        INDEX_LAYOUT,
        "| 配套 | 工程实战向题库《底层工程实践问题集.md》——**就业后再用**；本库只服务「先拿到 offer」|",
        "",
        "**怎么用**：第一遍扫一眼下面的 ★★★ 表，看哪些题你「听不懂问题」——那就是知识缺口；"
        "第二遍照着卡片答题，**讲出声**；第三遍专攻没打勾的自测项。",
        "",
        "---",
        "",
        "## 目录",
        "",
        "| 模块 | 题号 | 说明 |",
        "|---|---|---|",
    ]
    for num, name, a, b in modules:
        header.append(f"| {name} | {a} ~ {b} | {INDEX_WEIGHTS.get(num, '')} |")

    header += ["", "---", "", f"## ★★★ 速查表（{len(star3)} 题）", "",
               "> 面试前一晚、候场、前一轮刚答崩时的唯一清单。"
               "**看题号能不能立刻想起 3 个得分点**，想不起直接跳到正文。",
               "", "| 题号 | 问题 | 题号 | 问题 |", "|---|---|---|---|"]
    header += _pair_rows(star3)

    if star2:
        header += ["", f"## ★★ 补充题（{len(star2)} 题）", "",
                   "| 题号 | 问题 | 题号 | 问题 |", "|---|---|---|---|"]
        header += _pair_rows(star2)

    header += ["", "---", ""]
    return header, body


if __name__ == "__main__":
    main()
