#!/usr/bin/env python3
"""按条件类别预制的"统计内核模板"+ 列解析 + 取值(extract)+ 数值变换词汇。
类别代号:A 阈值比较、B 差值计算、C 字段匹配、D 多条件组合、E 占比/TopN、G 日志匹配、H 信令流程、
J 人工确认、P 成对比较。

设计:把**容易出错的统计逻辑**(阈值计数、字段/正则匹配、格内取值、「不存在」判据、成对比较、
多条件组合)固化成测试过的内核;运行时由 LLM 产出的**绑定**(选哪个类别内核、对哪列、行过滤 filter、
格内 extract、剥前缀/缩放等变换、阈值、路由到哪个源)来参数化它们。内核只产出**事实**:算出的量 +
provenance(命中了哪列、怎么命中、nulls)+ `predicate_satisfied`(**仅对解析到的数据做字面
比较**的结果)。v1 的 F(配置检查)、I(特征分析)两个内核仍封存于 archive/kernels-v1(已有 C/A 类的替代写法);
B/E/H 三个无线场景要用、又没有替代写法的内核已于 2026-10 恢复进本文件。

规则(在本层——不依赖模型的统计层——就要守住,不能等到 RCA 阶段):
- 列解析不到 → `status:"insufficient_data"`,**绝不**给 `predicate_satisfied`,**绝不**编造值。
- `Common:`/`Voice:` 这类**组前缀**由解析器按固定规则剥离(按首个冒号);**语义上**对应哪列由 LLM 在绑定里给。
- NIL/空 = 缺测,计入 `nulls`,不当作一个类别、也不当作 0。
- 成立/不成立的**最终判定**(含语义是否真的相等、路径回溯、数据不足传播)仍由 fault-tree-rca 做;
  本层的 `predicate_satisfied` 只是"在解析到的列上,字面比较为真/假"的输入。
"""
from __future__ import annotations

import copy
import csv
import io
import math
import operator
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 命令回显类 CSV(如数通 cmd/diag 模板)的引号包裹多行字段可达数 MB,csv 默认单字段上限 128KB 会当场抛错。
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:                                    # Windows 的 C long 是 32 位,sys.maxsize 会溢出
    csv.field_size_limit(2**31 - 1)

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")
_NULLISH = {"", "na", "n/a", "null", "none", "nil", "-"}
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
        "==": operator.eq, "=": operator.eq, "!=": operator.ne}
_ID_COLS = ("CALLID", "CallID", "Call_Id", "CellId", "cellName", "CellName",
            "Object", "id", "ID", "Index")
_FW_OPS = {"≥": ">=", "≤": "<=", "≠": "!=", "＞": ">", "＜": "<", "＝": "=="}   # 全角/数学符号 → 统一为半角写法
_AGGS = {"mean", "min", "max", "sum", "last", "first"}
_SCOPES = {"aggregate", "count_rows"}
# 平台改名兼容(2026-07 起 diag 更名 cmd):同一份数据两代列名/源名互认,旧绑定/旧调优表不作废。
# 列名同义(键/值都用 _norm 归一形态):diag 的 ObjContent ↔ cmd 汇总表的 CmdResult。
_COL_ALIASES = {"objcontent": ("cmdresult",), "cmdresult": ("objcontent",)}
_SOURCE_ALIASES = {"diag": "cmd", "cmd": "diag"}         # 源名同义:绑定写哪个名都能找到数据


def alias_sources(sources: dict) -> dict:
    """源名兼容处理:sources 里只有 cmd/diag 之一时,补一个同义键指向**同一张表**(不复制)。
    绑定/调优表用旧名、数据用新名(或反之)都能对上;两名都给了(真有两份数据)则各归各,不覆盖。"""
    for a, b in _SOURCE_ALIASES.items():
        if a in sources and b not in sources:
            sources[b] = sources[a]
    return sources


def _shape_err(b, need_op=False):
    """数值类参数检查(A 类及带 op/threshold 的绑定共用):全角运算符就地统一为半角;非法枚举/非数值阈值/require<1
    → 返回错误串(调用方据此明确报"数据不足"),合法 → None。宗旨:绑定写错绝不静默降级——
    实测 op 全角/agg 拼错/scope 拼错/require:0/字符串阈值,旧内核全部无声地算出方向相反的错误结论。"""
    if b.get("op") is not None:
        op = _FW_OPS.get(str(b["op"]).strip(), str(b["op"]).strip())
        if op not in _OPS:
            return f"op「{b['op']}」不认识(只接受 < <= > >= == != =)"
        b["op"] = op
    elif need_op:
        return "缺 op"
    if b.get("threshold") is not None and _num(b["threshold"]) is None:
        return f"threshold「{b['threshold']}」不是数值(状态/字符串比较请改 C 类 equals/contains)"
    if need_op and b.get("threshold") is None:
        return "缺 threshold"
    if b.get("agg") is not None and str(b["agg"]) not in _AGGS:
        return f"agg「{b['agg']}」不认识(只接受 {'/'.join(sorted(_AGGS))})"
    if b.get("scope") is not None and str(b["scope"]) not in _SCOPES:
        return f"scope「{b['scope']}」不认识(只接受 aggregate/count_rows)"
    if b.get("require") is not None:
        rq = _num(b["require"])
        if rq is None or rq < 1:
            return f"require「{b['require']}」不合法(须为 ≥1 的数;0 会恒判成立)"
        if b.get("scope") != "count_rows":               # 实测:忘写 scope 时 require 被静默忽略、按均值出确定结论
            return ("require 只在 scope:\"count_rows\" 下有效——现在会被静默忽略、按 aggregate 聚合出错误结论;"
                    "补 \"scope\":\"count_rows\" 或去掉 require")
    return None


# ---------- 取值层(extract):从一格混杂文本里抽出需要的那段值,A/C/G/P 都会用到 ----------
def _extract_spec_err(spec):
    """extract 参数检查:{"kv": "键名"} 或 {"regex": "正则"} 二选一;正则编译失败=明确拒收
    (判据里的"关键字"是原始材料而非可直接使用的正则——%u/%d 是 printf 占位符不是正则,绑定层要先换成 \\d+ 等)。"""
    if not isinstance(spec, dict) or (("kv" in spec) == ("regex" in spec)):
        return 'extract 只接受 {"kv": "键名"} 或 {"regex": "正则"} 之一'
    if "regex" in spec:
        try:
            re.compile(str(spec["regex"]))
        except re.error as e:
            return f"extract.regex 编译失败:{e}(%u/%d 这类占位要先换成 \\d+;HTML 渣要剔掉)"
    return None


def _extract_val(text, spec):
    """从一格文本抽值:kv=「键 [:=:] 值」(值到 空白/逗号/分号 止,但「,后跟恰好 3 位数字」当作
    千分位分隔符继续读——设备回显计数「CRC: 1,234」要抽出整数,不是截在逗号前的 1);regex=第一个捕获组
    (无捕获组取整体命中)。抽不到返回 None(调用方按缺测/未命中处理,绝不编造值)。"""
    s = str(text)
    if "kv" in spec:
        m = re.search(re.escape(str(spec["kv"])) + r"\s*[::=]\s*([^\s,;,;]+(?:,\d{3})*)", s)
        return m.group(1) if m else None
    m = re.search(str(spec["regex"]), s)
    if not m:
        return None
    return m.group(1) if m.groups() else m.group(0)


def _decomma(s):
    """千分位处理:仅当整体是 1,234[.56] 这种形态才去掉逗号(避免误伤枚举串),供 extract 抽出的值转成数值用。"""
    t = str(s).strip()
    return t.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", t) else s


def _hit_snippet(s, pos, width=120):
    """命中处的原文片段:以命中位置为中心截取一段(整格回显可达数 KB,不能整格照搬)。
    换行/连续空白压成单个空格,截断处加省略号——证据引用的是**代码截取的原文**,不经模型转述。"""
    s = str(s)
    start = max(0, pos - 30)
    end = min(len(s), start + width)
    frag = re.sub(r"\s+", " ", s[start:end]).strip()
    return ("…" if start > 0 else "") + frag + ("…" if end < len(s) else "")


# ---------- 数据加载 ----------
def _decode(raw: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def list_sheets(path: str):
    """xlsx 的页签名列表;csv 返回 []。多页签数据(如信令)用它发现各页签。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        import xlsx_reader                               # 同目录的无第三方依赖读取器(不需要 openpyxl)
        return xlsx_reader.list_sheets(p)
    if p.suffix.lower() in (".txt", ".cfg", ".mo", ".moc"):
        try:
            from mo_config import mo_sheets
            return mo_sheets(p.read_bytes().decode("utf-8", "ignore"))   # 把 MO 类型清单当作"页签"
        except Exception:
            return []
    return []


def load_table(path: str, sheet=None):
    """读 csv/xlsx/MO配置文本 → (header, rows[list[dict]])。xlsx 多页签:sheet 给页签名选表,不给则活动页;
    .txt/.cfg/.mo 当作 MO 配置文本(MOName:key=val,...;每实例一行)交 mo_config 解析成表。"""
    p = Path(path)
    if p.suffix.lower() in (".txt", ".cfg", ".mo", ".moc"):
        try:
            from mo_config import parse_mo_text
            return parse_mo_text(_decode(p.read_bytes()), sheet=sheet)   # sheet=<MO类型>→只取该类,避免同名列污染
        except Exception:
            pass            # 解析失败 → 改按 csv 读取(备用做法),不崩
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        import xlsx_reader                               # 同目录的无第三方依赖读取器;页签不存在时同样抛 ValueError 并指出页签名
        return xlsx_reader.read_xlsx(p, sheet)
    reader = csv.DictReader(io.StringIO(_decode(p.read_bytes())))
    header = list(reader.fieldnames or [])
    rows = [{(k or ""): ("" if v is None else str(v)) for k, v in row.items()} for row in reader]
    return header, rows


# ---------- 基础工具 ----------
def _is_null(v) -> bool:
    return str(v).strip().lower() in _NULLISH


def _num(v):
    s = str(v).strip().rstrip("%")
    if s.lower() in _NULLISH:
        return None
    try:
        x = float(s)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None   # "nan"/"inf" 字符串=缺测:进入聚合会让 mean 变成 NaN→恒判不成立(实测)


def _apply_transforms(x, transforms):
    """对一个数值应用数值变换词汇(LLM 在绑定里声明,词汇固定、可审计):
    scale:<f>(乘,如 /100 写 scale:0.01、156*TA/2 写 scale:78)、abs、neg、round:<n>、
    log10(x≤0 无对数 → 当作缺测/null,不编造值)。"""
    if x is None:
        return None
    for t in transforms or []:
        if t.startswith("scale:"):
            x = x * float(t.split(":", 1)[1])
        elif t == "abs":
            x = abs(x)
        elif t == "neg":
            x = -x
        elif t.startswith("round:"):
            x = round(x, int(t.split(":", 1)[1]))
        elif t == "log10":
            if x <= 0:
                return None
            x = math.log10(x)
    return x


def _strip_prefix(name: str) -> str:
    """剥组前缀:'Common:SA QoSFlow Drop Rate' → 'SA QoSFlow Drop Rate'(按首个冒号)。"""
    s = str(name)
    return s.split(":", 1)[1].strip() if ":" in s else s


def _norm(s: str) -> str:
    return _strip_prefix(s).strip().lower().replace(" ", "")


def resolve_column(header, wanted):
    """把绑定里写的列名解析到真实表头。返回 (列名 | None, 命中方式)。
    顺序:精确 → 剥组前缀后精确 → 归一化(去前缀/空格/大小写)唯一 → 已知同义列名 → 嵌套路径取末段。
    解析不到/有歧义 → (None, 原因),交由上层判数据不足,绝不随意匹配。"""
    w = str(wanted or "").strip()
    if not w:
        return None, "empty"
    if w in header:
        return w, "exact"
    for h in header:
        if _strip_prefix(h).strip() == w:
            return h, "stripped_prefix"
    wn = _norm(w)
    cand = [h for h in header if _norm(h) == wn]
    if len(cand) == 1:
        return cand[0], "normalized"
    if len(cand) > 1:
        return None, f"ambiguous:{cand[:5]}"
    for al in _COL_ALIASES.get(wn, ()):                  # 平台改名兼容:绑定写旧名、数据是新名(或反之)也能对上
        hit = [h for h in header if _norm(h) == al]
        if len(hit) == 1:
            return hit[0], f"alias:{w}"
    if "->" in w:  # 嵌套路径(如 A->B->DCInnerCause)取末段再试
        return resolve_column(header, w.split("->")[-1].strip())
    return None, "not_found"


def _numeric_series(rows, col, transforms):
    raw = [r.get(col, "") for r in rows]
    nulls = sum(1 for v in raw if _is_null(v))
    vals = [_apply_transforms(_num(v), transforms) for v in raw if _num(v) is not None]
    return vals, nulls, len(raw)


def _agg(vals, how):
    if not vals:
        return None
    return {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals),
            "sum": sum(vals), "last": vals[-1], "first": vals[0]}.get(how, sum(vals) / len(vals))


def _cmp(a, op, b):
    f = _OPS.get(op)
    return None if (f is None or a is None or b is None) else bool(f(a, b))


def _insuf(cls, reason, **extra):
    return dict({"class": cls, "status": "insufficient_data", "reason": reason}, **extra)


def _id_col(header):
    return next((c for c in _ID_COLS if c in header), None)


# ---------- A:阈值比较 ----------
def _threshold_extract_series(rows, col, spec, transforms):
    """extract 取值层的数值序列:每格先抽出需要的那段再转数值。返回 (vals, nulls, n, raw, picked)。"""
    # 取值层:格子是混杂文本(如 "利用率:83%,速率:1G"),先抽出需要的那段再转成数值;抽不到=缺测
    raw = [r.get(col, "") for r in rows]
    picked = [(_extract_val(v, spec) if not _is_null(v) else None) for v in raw]
    vals = [x for x in (_apply_transforms(_num(_decomma(p)), transforms)
                        for p in picked if p is not None) if x is not None]
    nulls, n = sum(1 for p in picked if p is None), len(raw)
    return vals, nulls, n, raw, picked


def _threshold_hit_texts(raw, picked, transforms, op, thr):
    """extract 场景的命中原文:截取"满足比较的那几格"的原文片段(至多 3 条)。"""
    # 命中原文:extract 场景下格子是混杂文本,把"满足比较的那几格"的原文片段截取出来给证据
    # (纯数值列不截取——格子本身就是那个数,引用不带来额外信息)
    hit_texts = []
    for raw_v, p in zip(raw, picked):
        if p is None or len(hit_texts) >= 3:
            continue
        x = _apply_transforms(_num(_decomma(p)), transforms)
        if x is not None and _cmp(x, op, thr):
            s = str(raw_v).strip()
            pos = s.find(str(p))
            hit_texts.append(_hit_snippet(s, max(pos, 0)))
    return hit_texts


def _threshold_no_values(cls, col, rows, spec, nulls):
    """该列一个有效数值都没有 → 数据不足(extract 场景附该列样例,便于核对键名/正则)。"""
    if spec:
        sample = next((str(r.get(col, ""))[:80] for r in rows if not _is_null(r.get(col, ""))), "")
        return _insuf(cls, f"extract 在该列一条也没抽到数值——核对 kv 键名/正则与数据格式。"
                           f"该列样例:「{sample}」", resolved_column=col, nulls=nulls)
    return _insuf(cls, "该列无有效数值(全空/NIL)", resolved_column=col, nulls=nulls)


def _threshold_verdict(fact, vals, b, op, thr):
    """按 scope 出判定:count_rows=满足比较的行数≥require;aggregate=聚合值与阈值比较。"""
    if b.get("scope") == "count_rows":
        cnt = sum(1 for v in vals if _cmp(v, op, thr))
        need = b.get("require", 1)
        fact.update(scope="count_rows", count_satisfied=cnt, ratio=round(cnt / len(vals), 4),
                    predicate_satisfied=cnt >= need)
    else:
        agg = b.get("agg", "mean")
        value = _agg(vals, agg)
        fact.update(scope="aggregate", agg=agg, value=round(value, 4),
                    predicate_satisfied=_cmp(value, op, thr))
    return fact


def k_threshold(table, b, cls="A"):
    header, rows = table
    col, how = resolve_column(header, b.get("column", ""))
    if not col:
        return _insuf(cls, f"未找到列「{b.get('column')}」({how})", resolved_column=None)
    err = _shape_err(b, need_op=True)
    if err:
        return _insuf(cls, f"绑定参数不合法:{err}", resolved_column=col)
    spec = b.get("extract")
    if spec:
        err = _extract_spec_err(spec)
        if err:
            return _insuf(cls, f"绑定参数不合法:{err}", resolved_column=col)
        vals, nulls, n, raw, picked = _threshold_extract_series(rows, col, spec, b.get("transforms"))
    else:
        vals, nulls, n = _numeric_series(rows, col, b.get("transforms"))
    op, thr = b.get("op"), b.get("threshold")
    fact = {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
            "n_rows": n, "nulls": nulls, "valid": len(vals), "op": op, "threshold": thr,
            "transforms": b.get("transforms") or []}
    if spec:
        fact["extract"] = spec
        hit_texts = _threshold_hit_texts(raw, picked, b.get("transforms"), op, thr)
        if hit_texts:
            fact["hits"] = hit_texts
    if not vals:
        return _threshold_no_values(cls, col, rows, spec, nulls)
    return _threshold_verdict(fact, vals, b, op, thr)


# ---------- C / G:字段 / 日志匹配 ----------
def _match_spec_err(b, mode, targets):
    """C/G 绑定参数检查(targets/match/regex/extract/require/absent):非法 → 错误串,合法 → None。"""
    if not targets:                                     # C/G 只接受 contains/equals/regex 列表;op/target 这类清单外的字段内核不读。
        return ("绑定缺 contains/equals/regex 参数(op/target 等野字段无效)——若不拦,"
                "空 targets 会静默 matched=0、整源变假排除;修绑定格式")
    if b.get("match") is not None and str(b.get("match")) not in ("exact", "contains", "regex"):
        return (f"match「{b.get('match')}」不认识(只接受 exact/contains/regex)——"
                "旧内核会静默按 exact 算")
    if mode == "regex":                                 # 判据自带的"关键字"是原始材料而非现成正则:编译不过就明确拒收
        for t in targets:
            try:
                re.compile(str(t))
            except re.error as e:
                return (f"regex「{t}」编译失败:{e}(%u/%d 这类 printf 占位要先换成 \\d+;"
                        "HTML 渣要剔掉)——坏正则不拦会整条恒不命中=假排除")
    spec = b.get("extract")
    if spec:
        _e = _extract_spec_err(spec)
        if _e:
            return f"绑定参数不合法:{_e}"
    if b.get("require") is not None:
        _rq = _num(b.get("require"))
        if _rq is None or _rq < 1:
            return f"require「{b.get('require')}」不合法(须为 ≥1 的数;0 会恒判成立=假阳)"
        if b.get("absent"):
            return ("absent 与 require 互斥(absent=命中0行才成立,require=至少命中N行)——"
                    "语义矛盾,修绑定")
    return None


def _match_row_hits(v, nv, mode, targets, tnorm):
    """一格取值对各 target 的命中下标列表:contains=子串、regex=搜索、exact=整串相等。"""
    if mode == "contains":
        return [j for j, t in enumerate(tnorm) if t in nv]
    if mode == "regex":
        return [j for j, t in enumerate(targets) if re.search(str(t), str(v).strip())]
    return [j for j, t in enumerate(tnorm) if nv == t]


def _match_hit_pos(s, j, mode, targets, tnorm, ci):
    """第 j 个 target 在原文 s 里的命中位置(regex 取搜索起点,其余按子串查找),供截取片段用。"""
    if mode == "regex":
        m = re.search(str(targets[j]), s)
        return m.start() if m else 0
    return (s.lower() if ci else s).find(tnorm[j])


def _match_scan(rows, col, spec, mode, targets, tnorm, norm, ci, idc):
    """逐行统计:缺测计 nulls、取值分布 dist、命中行数 matched、各 target 命中数、样例 id、命中原文片段。"""
    matched, nulls, samples, dist = 0, 0, [], Counter()
    hit_texts = []                                      # 命中处的原文片段(代码截取,证据引用直接照抄它)
    by_target = Counter()                               # 每个 target 各命中多少行(多 target 时的可审计分解)
    for i, r in enumerate(rows):
        v = r.get(col, "")
        if _is_null(v):
            nulls += 1
            continue
        if spec:                                        # 取值层:先从混杂文本的格子里抽出值,再对抽出的值做匹配
            v = _extract_val(v, spec)
            if v is None:
                nulls += 1                              # 抽不到=该行没有这项信息,按缺测计,不当作未命中
                continue
        dist[str(v).strip()[:80]] += 1                  # 同列取值分布(键截 80 字:整格回显可达数 KB,防止 top 过大)
        hits = _match_row_hits(v, norm(v), mode, targets, tnorm)
        if hits:
            matched += 1
            for j in hits:
                by_target[str(targets[j])] += 1
            if len(samples) < 3:
                samples.append(str(r.get(idc)) if idc else f"row#{i}")
            if len(hit_texts) < 3:                      # 命中原文:以首个命中的 target 的位置为中心截取片段
                s = str(v).strip()
                pos = _match_hit_pos(s, hits[0], mode, targets, tnorm, ci)
                hit_texts.append(_hit_snippet(s, max(pos, 0)))
    return matched, nulls, samples, dist, hit_texts, by_target


def _match_fact(cls, col, how, b, mode, targets, scan):
    """组装 C/G 事实:matched/total/ratio/top/samples + 判定(absent 反向)+ 可选的 hits/extract/matched_by_target。"""
    matched, nulls, samples, dist, hit_texts, by_target = scan
    total = sum(dist.values())                          # 纳入统计的非空行数 = 占比分母
    if b.get("absent") and total == 0:
        # 「不存在」判据(故障=「没配置X」)靠"缺行即证据",但整列无值时缺的可能是导出内容而非配置——不判成立
        return _insuf(cls, "列全空/表为空,「不存在」无从证明(absent 需要列里有实际取值作为依据)",
                      resolved_column=col)
    fact = {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
            "match": mode, "targets": targets, "matched": matched, "total": total,
            "ratio": round(matched / total, 4) if total else None, "nulls": nulls,
            "top": dist.most_common(5), "samples": samples,
            "predicate_satisfied": (matched == 0) if b.get("absent")
                                   else matched >= b.get("require", 1)}
    if b.get("absent"):
        fact["absent"] = True                           # 标记判定方向:matched=0 且成立=「未配置」,让 RCA 能正确理解
    if hit_texts:
        fact["hits"] = hit_texts                        # 命中原文片段(代码截取的,evidence 引用只许照抄它)
    if b.get("extract"):
        fact["extract"] = b.get("extract")              # 审计:值是从格内抽出来的,不是整格
    if len(targets) > 1:                                # 单 target 时分解=matched,冗余不输出
        # 只进校验/审计层:不在 tree_prune._FACT_KEEP 白名单、不被 question 渲染——不会流进 RCA 上下文
        fact["matched_by_target"] = {str(t): by_target.get(str(t), 0) for t in targets}
    return fact


def k_match(table, b, cls="C"):
    header, rows = table
    col, how = resolve_column(header, b.get("column", ""))
    if not col:
        return _insuf(cls, f"未找到列「{b.get('column')}」({how})", resolved_column=None)
    mode = ("regex" if b.get("regex") else "contains" if b.get("contains") else b.get("match", "exact"))
    targets = b.get("contains") or b.get("equals") or b.get("regex") or []
    err = _match_spec_err(b, mode, targets)
    if err:
        return _insuf(cls, err, resolved_column=col)
    ci = b.get("case_insensitive", mode == "contains")
    norm = (lambda x: str(x).strip().lower()) if ci else (lambda x: str(x).strip())
    tnorm = [norm(t) for t in targets]
    scan = _match_scan(rows, col, b.get("extract"), mode, targets, tnorm, norm, ci, _id_col(header))
    return _match_fact(cls, col, how, b, mode, targets, scan)


# ---------- D:多条件组合 ----------
def _combine_tri(comb, results):
    """AND/OR 的三态合成(子结果 → (predicate, status))。三态指 predicate 的三个取值:True/False/None(None=数据不足)。"""
    preds = [r.get("predicate_satisfied") for r in results]
    sts = [r.get("status") for r in results]
    if comb == "AND":
        return ((False, "ok") if any(p is False for p in preds)
                else (None, "insufficient_data") if any(s != "ok" for s in sts)
                else (True, "ok"))
    return ((True, "ok") if any(p is True for p in preds)
            else (None, "insufficient_data") if any(s != "ok" for s in sts)
            else (False, "ok"))


def _combo_groups(rows, gcol):
    """按分组列切组(保持首次出现顺序;分组值为空/NIL 的行不入组)。"""
    groups = {}
    for r in rows:
        gv = str(r.get(gcol, "")).strip()
        if not _is_null(gv):
            groups.setdefault(gv, []).append(r)
    return groups


def _combo_scan_groups(subs, comb, sources, parent_source, header, groups, _resolved):
    """每组单独跑全部子条件并三态合成。返回 (全真组名列表, 数据不足组名列表, 代表组 (类别, 组名, 子结果))。"""
    matched, insuf_g, first_detail = [], [], None
    for gv, grows in groups.items():
        gsources = dict(sources)
        gsources[parent_source] = (header, grows)
        results = [evaluate_binding(s, gsources, parent_source, _resolved) for s in subs]
        pred, st = _combine_tri(comb, results)
        if pred is True:
            matched.append(gv)
            if first_detail is None or first_detail[0] != "matched":
                first_detail = ("matched", gv, results)
        elif st != "ok":
            insuf_g.append(gv)
            if first_detail is None:
                first_detail = ("insufficient", gv, results)
        elif first_detail is None:
            first_detail = ("unmatched", gv, results)
    return matched, insuf_g, first_detail


def _combo_group_fact(comb, gcol, groups, matched, insuf_g, first_detail):
    """组间三态合成成事实:有组全真→成立;无全真但有组数据不足→数据不足;否则不成立。"""
    if matched:
        pred, st = True, "ok"
    elif insuf_g:
        pred, st = None, "insufficient_data"
    else:
        pred, st = False, "ok"
    fact = {"class": "D", "status": st, "combine": comb, "group_by": gcol,
            "groups": len(groups), "matched_groups": sorted(matched)[:5],
            "predicate_satisfied": pred,
            "reason": None if st == "ok" else
            f"无任何组全部满足,且 {len(insuf_g)} 个组存在数据不足子条件,无法确证整体"}
    if len(matched) > 5:
        fact["matched_groups_total"] = len(matched)
    if first_detail:                                 # 只带一个代表组的子结果当审计样本,防止事实过多
        fact["sample_group"], fact["sub_facts"] = first_detail[1], first_detail[2]
    return fact


def _combo_grouped(b, subs, comb, sources, parent_source, _resolved):
    """带 group_by 的组合:切组、逐组评估、组间合成;守卫跨源 sub/无源/分组列缺失/分组列全空。"""
    # 分组组合:按 group_by 列切组,**每组单独跑全部子条件**,存在一个组满足 → 节点成立。
    # 解决的是"整列有A也有B、但A和B不在同一接口/对象上"的错误确证(对应业务脚本里 groupby 的语义)。
    # 组间三态合成:有组全真→成立;无全真但有组数据不足→数据不足;否则不成立。
    if any(s.get("source", parent_source) != parent_source for s in subs):
        return _insuf("D", "group_by 组合的 sub 不得跨源(各源行没有对齐关系,跨源分组无定义)"
                           "——跨源判据拆成外层 D(不带 group_by)+ 各源内层 D(带 group_by)")
    table = sources.get(parent_source)
    if table is None:
        return _insuf("D", f"无数据源「{parent_source}」(group_by 需要本源数据切组)")
    header, rows = table
    gcol, how = resolve_column(header, b["group_by"])
    if not gcol:
        return _insuf("D", f"未找到分组列「{b['group_by']}」({how})", resolved_column=None)
    groups = _combo_groups(rows, gcol)
    if not groups:
        return _insuf("D", f"分组列「{gcol}」全空,无组可判", resolved_column=gcol)
    matched, insuf_g, first_detail = _combo_scan_groups(subs, comb, sources, parent_source,
                                                        header, groups, _resolved)
    return _combo_group_fact(comb, gcol, groups, matched, insuf_g, first_detail)


def k_combo(b, sources, parent_source, _resolved=False):
    subs = b.get("sub") or []
    if not subs:                                        # 空 AND 时 any(False…)全为假 → 恒"成立"(实测出过假阳性),明确拒绝
        return _insuf("D", "组合绑定缺 sub 子条件列表(字段名写错/被丢掉?)——空组合恒判成立,禁止")
    comb = str(b.get("combine", "AND")).upper()
    if comb not in ("AND", "OR"):                       # 实测:拼错(如 ANDD)会被静默按 OR 算=方向相反的假阳性
        return _insuf("D", f"combine「{b.get('combine')}」不认识(只接受 AND/OR)——"
                           "拼错不拦会被静默按 OR 算,'全部满足'悄悄变'任一满足'")

    if b.get("group_by"):
        return _combo_grouped(b, subs, comb, sources, parent_source, _resolved)

    results = [evaluate_binding(s, sources, s.get("source", parent_source), _resolved)
               for s in subs]
    pred, st = _combine_tri(comb, results)
    return {"class": "D", "status": st, "combine": comb, "predicate_satisfied": pred,
            "reason": None if st == "ok" else "组合含数据不足子条件,无法确证整体", "sub_facts": results}


# ---------- P:成对比较(本端/对端同一信息是否一致)----------
def k_pair(table, b, cls="P"):
    """两侧同一信息的一致性判定(查对端类判据用它)。行选择用通用 filter(evaluate_binding 已提前做完),
    参数:side_column(分侧列)+ peer_marker(含此标记=本端,默认 "*")、value_column(取值列)、
    可选 extract(格内抽值)、fault_when:"not_equal"=两侧不等算故障成立(默认)/"equal" 反之。
    守卫:任一侧无行/无值 → 数据不足(「查不到对端」不是不成立);一侧多个不同值 → 数据不足(歧义附样例)。"""
    header, rows = table
    scol, how_s = resolve_column(header, b.get("side_column", ""))
    if not scol:
        return _insuf(cls, f"未找到分侧列「{b.get('side_column')}」({how_s})", resolved_column=None)
    vcol, how_v = resolve_column(header, b.get("value_column", ""))
    if not vcol:
        return _insuf(cls, f"未找到取值列「{b.get('value_column')}」({how_v})", resolved_column=None)
    fw = b.get("fault_when", "not_equal")
    if fw not in ("not_equal", "equal"):
        return _insuf(cls, f"fault_when「{fw}」不认识(只接受 not_equal/equal)", resolved_column=vcol)
    spec = b.get("extract")
    if spec:
        err = _extract_spec_err(spec)
        if err:
            return _insuf(cls, f"绑定参数不合法:{err}", resolved_column=vcol)
    marker = str(b.get("peer_marker", "*"))
    local_vals, peer_vals = [], []
    for r in rows:
        sv, vv = str(r.get(scol, "")), r.get(vcol, "")
        if _is_null(sv) or _is_null(vv):
            continue
        val = _extract_val(vv, spec) if spec else str(vv).strip()
        if val is None:
            continue
        (local_vals if marker in sv else peer_vals).append(str(val).strip())
    if not local_vals or not peer_vals:
        side = "本端" if not local_vals else "对端"
        return _insuf(cls, f"查不到{side}的数据(「{scol}」按标记「{marker}」分侧后{side}无取值)"
                           "——对端不可查是判不了,不是'不成立'", resolved_column=vcol)
    lset, pset = sorted(set(local_vals)), sorted(set(peer_vals))
    if len(lset) > 1 or len(pset) > 1:
        return _insuf(cls, f"取值有歧义:本端 {lset[:3]} / 对端 {pset[:3]}(一侧多个不同值)"
                           "——先用 filter 缩小行范围或改 extract 抽得更准", resolved_column=vcol)
    same = lset[0] == pset[0]
    fact = {"class": cls, "status": "ok", "resolved_column": vcol, "side_column": scol,
            "local": lset[0], "peer": pset[0], "equal": same, "fault_when": fw,
            "n_local": len(local_vals), "n_peer": len(peer_vals),
            "predicate_satisfied": (not same) if fw == "not_equal" else same}
    if spec:
        fact["extract"] = spec
    return fact


# ---------- B:差值计算(Σ被减列 − Σ减列,逐操作数变换后与阈值比较;2026-10 自 v1 恢复,无线话统判据用)----------
def _difference_columns(header, mens, subs):
    """解析差值两侧的原始计数列 → ({列名: 真实表头}, 缺失列表)。"""
    cols, missing = {}, []
    for name in mens + subs:
        c, _ = resolve_column(header, name)
        if c:
            cols[name] = c
        else:
            missing.append(name)
    return cols, missing


def _difference_series(rows, cols, mens, subs, tr):
    """逐行算 Σ被减 − Σ减(逐操作数先做 transforms)→ (差值列表, 含空值的行数)。"""
    per, nulls = [], 0
    for r in rows:
        mv = [_num(r.get(cols[n], "")) for n in mens]
        sv = [_num(r.get(cols[n], "")) for n in subs]
        if any(x is None for x in mv + sv):
            nulls += 1
            continue
        per.append(sum(_apply_transforms(x, tr) for x in mv) - sum(_apply_transforms(x, tr) for x in sv))
    return per, nulls


def k_difference(table, b, cls="B"):
    header, rows = table
    mens, subs = b.get("minuend") or [], b.get("subtrahend") or []
    if not mens or not subs:
        return _insuf(cls, "minuend/subtrahend 缺失或为空——差值两侧都必须给原始计数列"
                           "(单侧为空会算成 0-0=0、恒判不成立)", resolved_column=None)
    err = _shape_err(b)
    if err:
        return _insuf(cls, f"绑定参数不合法:{err}", resolved_column=None)
    cols, missing = _difference_columns(header, mens, subs)
    if missing:
        return _insuf(cls, f"缺原始计数列:{missing}(只有成功率/无原始计数 → 无法算差值)",
                      resolved_column=None, missing=missing)
    per, nulls = _difference_series(rows, cols, mens, subs, b.get("transforms"))
    if not per:
        return _insuf(cls, "无有效行(计数列空/NIL)", nulls=nulls)
    op, thr = b.get("op", ">"), b.get("threshold", 0)
    fact = {"class": cls, "status": "ok", "resolved_columns": cols, "nulls": nulls,
            "valid": len(per), "op": op, "threshold": thr}
    if b.get("scope") == "count_rows":
        cnt = sum(1 for v in per if _cmp(v, op, thr))
        fact.update(scope="count_rows", count_satisfied=cnt, ratio=round(cnt / len(per), 4),
                    predicate_satisfied=cnt >= b.get("require", 1))
    else:
        agg = b.get("agg", "sum")
        value = _agg(per, agg)
        fact.update(scope="aggregate", agg=agg, value=round(value, 4),
                    predicate_satisfied=_cmp(value, op, thr))
    return fact


# ---------- E:占比/TopN(按某列分组,Top1 组占比与阈值比较;2026-10 自 v1 恢复,CHR 判据用)----------
# 行级 filter 不再内建:统一走 _run_filtered(多子句、excludes 都支持),过滤后再分组。
def k_proportion(table, b, cls="E"):
    header, rows = table
    gb, _ = resolve_column(header, b.get("group_by", ""))
    if not gb:
        return _insuf(cls, f"未找到分组列「{b.get('group_by')}」", resolved_column=None)
    err = _shape_err(b)
    if err:
        return _insuf(cls, f"绑定参数不合法:{err}", resolved_column=gb)
    sel = rows
    c = Counter(str(r.get(gb, "")).strip() for r in sel if not _is_null(r.get(gb, "")))
    if not c:
        return _insuf(cls, "分组列全空")
    top, topn = c.most_common(1)[0]
    ratio = topn / sum(c.values())
    op, thr = b.get("op", ">"), b.get("threshold", 0.7)
    return {"class": cls, "status": "ok", "group_by": gb,
            "filtered_n": len(sel), "valid": sum(c.values()), "top1": top, "top1_count": topn,
            "top1_ratio": round(ratio, 4), "op": op, "threshold": thr,
            "predicate_satisfied": _cmp(ratio, op, thr)}


# ---------- H:信令流程(会话级 has/lacks/where;2026-10 自 v1 恢复,信令 trace 判据用)----------
def _parse_where(header, where):
    """解析 H 的 where 约束 → (约束列表 [(列, 取值, 模式)], None) 或 (None, 数据不足原因)。"""
    constraints = []
    for cst in (where if isinstance(where, list) else [where]):
        wc, _ = resolve_column(header, cst.get("column", ""))
        if not wc:
            return None, f"where 未找到列「{cst.get('column')}」"
        vals = [str(v).strip() for v in (cst.get("contains") or cst.get("equals") or [])]
        if not vals:                                     # 空取值的 where 恒不满足 → 全会话假排除,明确拒绝
            return None, (f"where 子句缺 equals/contains 取值(列「{cst.get('column')}」)——"
                          "空约束恒不满足=假排除;修绑定格式")
        constraints.append((wc, vals, "contains" if cst.get("contains") else "exact"))
    return constraints, None


def _row_meets(r, constraints) -> bool:
    """一行是否同时满足全部 where 约束。"""
    for wc, vals, mode in constraints:
        v = str(r.get(wc, "")).strip()
        if not (any(t in v for t in vals) if mode == "contains" else v in vals):
            return False
    return True


def _group_sessions(rows, key, msgcol, constraints) -> dict:
    """按会话键分组:{key: {"msgs": 消息集合, "where_ok": 是否有一行满足全部 where 约束}}。"""
    groups = {}
    for r in rows:
        k = str(r.get(key, "")).strip() if key else "_all"
        g = groups.setdefault(k, {"msgs": set(), "where_ok": not constraints})
        g["msgs"].add(str(r.get(msgcol, "")).strip())
        if constraints and not g["where_ok"] and _row_meets(r, constraints):
            g["where_ok"] = True
    return groups


def _resolve_session_cols(header, b, cls):
    """解析 H 的消息列与会话键列 → ((msgcol, key), None) 或 (None, 数据不足事实)。"""
    msgcol = resolve_column(header, b.get("msg_column", ""))[0] if b.get("msg_column") else None
    if not msgcol:
        return None, _insuf(cls, "无信令/消息数据源(需 trace 记录、含消息类型列 msg_column)", resolved_column=None)
    key = resolve_column(header, b.get("key", ""))[0] if b.get("key") else None
    if b.get("key") and not key:                        # 显式给了 key 却解析不到 → 全表并成一个会话,has/lacks 语义畸变
        return None, _insuf(cls, f"未找到会话键列「{b.get('key')}」——解析不到会把全表并成一个会话;"
                                 "确要全表当一个会话请显式 key:null", resolved_column=msgcol, msg_column=msgcol)
    return (msgcol, key), None


def _unknown_has_fact(cls, has, all_msgs, msgcol):
    """has 里的消息名在整列从未出现 → 多半是消息名写错或本数据无此流程,改判数据不足并摆出词汇样例。"""
    unknown_has = sorted(t for t in has if t not in all_msgs)
    if not unknown_has:
        return None
    sample = sorted(v for v in all_msgs if v)[:15]
    return _insuf(cls, f"has 里的消息名在 msg_column 从未出现:{unknown_has}——疑似消息名与数据词汇不符,"
                       f"或本数据无此流程(非'真没失败')。msg_column 实际取值样例:{sample}",
                  resolved_column=msgcol, msg_column=msgcol,
                  unknown_messages=unknown_has, msg_vocab_sample=sample)


def k_signaling(table, b, cls="H"):
    """会话级信令判定:按 key 分组,看每个会话的消息集合是否满足 has/lacks;
    可选 where:要求会话里**存在一行**同时满足若干"列=值"约束(如"FAIL 且携带 cause=X")。
    where 写法:单个 {column, equals|contains} 或其列表(列表内所有约束须落在同一行)。"""
    header, rows = table
    cols, fact = _resolve_session_cols(header, b, cls)
    if fact is not None:
        return fact
    msgcol, key = cols
    has, lacks = set(b.get("has") or []), set(b.get("lacks") or [])
    constraints, err = _parse_where(header, b.get("where")) if b.get("where") else ([], None)
    if err:
        return _insuf(cls, err, resolved_column=msgcol, msg_column=msgcol)
    groups = _group_sessions(rows, key, msgcol, constraints)
    all_msgs = set().union(*(g["msgs"] for g in groups.values())) if groups else set()
    fact = _unknown_has_fact(cls, has, all_msgs, msgcol)
    if fact is not None:
        return fact
    matched = sum(1 for g in groups.values()
                  if has <= g["msgs"] and not (lacks & g["msgs"]) and g["where_ok"])
    out = {"class": cls, "status": "ok", "key": key, "msg_column": msgcol, "groups": len(groups),
           "matched_sessions": matched, "predicate_satisfied": matched > 0}
    # lacks 里的消息名若整列从未出现 → lacks 约束恒成立、退化成"只要有 has 就命中" → 可能假阳性。
    # 不改判定(免得压掉真信号),但标注出来,让 RCA 对这条"成立"降置信、写进 caveat。
    unknown_lacks = sorted(t for t in lacks if t not in all_msgs)
    if unknown_lacks:
        out["lacks_never_seen"] = unknown_lacks
    if constraints:
        out["where"] = [{"column": wc, "values": vals, "match": mode} for wc, vals, mode in constraints]
    return out


# ---------- J:人工确认 ----------
def k_manual(b):
    return {"class": "J", "status": "manual", "predicate_satisfied": None,
            "note": b.get("note", "需人工现场确认,自动指标不可判")}


_KERNELS = {"A": k_threshold, "B": k_difference, "C": k_match, "E": k_proportion,
            "G": lambda t, b: k_match(t, b, "G"), "H": k_signaling, "P": k_pair}
_ARCHIVED = set("FI")                                # v1 内核已封存于 archive/kernels-v1(数通场景全面核查中一次也没用到)

# 顶层字段白名单:绑定带清单外的字段 = 明确拒收("明确拒收"规则在 A/D 顶层的补漏——实测
# require 忘配 count_rows、A 里误写 contains、combine 拼错,全都被静默忽略后算出语义错误的确定结论)。
_META_KEYS = {"id", "node", "node_id", "condition_text", "class", "source", "mapping_reason",
              "decision", "reason", "mode", "manual", "note", "param_from"}
_CLASS_KEYS = {
    "A": {"column", "op", "threshold", "scope", "agg", "transforms", "require", "filter", "extract"},
    "B": {"minuend", "subtrahend", "op", "threshold", "scope", "agg", "transforms", "require", "filter"},
    "E": {"group_by", "op", "threshold", "filter"},
    "H": {"msg_column", "key", "has", "lacks", "where"},     # 顶层 filter 不收:会话内行级约束用 where
    "C": {"column", "equals", "contains", "regex", "match", "case_insensitive", "require",
          "absent", "filter", "extract"},
    "D": {"combine", "sub", "group_by", "filter"},       # D 顶层 filter 有专门的明确拒绝(提示信息更准确)
    "J": set(),
    "P": {"side_column", "peer_marker", "value_column", "fault_when", "filter", "extract", "pair_from"},
    "S": {"func", "file", "args"},                       # 代码类(人手调优专用):选行/抽值都在函数里做,
}                                                        # 不接受 filter/extract 顶层字段
_CLASS_KEYS["G"] = _CLASS_KEYS["C"]


def _apply_filter(table, flt):
    """行级预过滤——为「EAV 窄表」(一行只放一个量,即实体/属性/值三列,如 DataItem/DataValue/Unit 这种结构)或"先选中某命令/
    某检查项的行"服务:先把行缩到 filter 命中的子集,再让内核对子集算。
    子句 {column, equals:值|[值…] 或 contains:值|[值…] 或 excludes:值|[值…](**不含**才保留,
    如挑「Object 不带 *」的对端行)};多子句(list)= AND,同一行须全满足;大小写不敏感。
    返回 (过滤后table, None) 或 (None, 错误串)(列解析不到 → 上层判数据不足)。"""
    header, rows = table
    clauses = flt if isinstance(flt, list) else [flt]
    resolved = []
    for c in clauses:
        rc, err = _filter_clause(header, c)
        if err:
            return None, err
        resolved.append(rc)
    keep = [r for r in rows if _filter_row_keep(r, resolved)]
    return (header, keep), None


def _filter_clause(header, c):
    """解析一条 filter 子句 → ((列名, 模式, 归一化目标列表), None) 或 (None, 错误串)。"""
    if not isinstance(c, dict):                      # 格式不对的子句(如写成字符串)→ 数据不足,绝不随意匹配、绝不崩
        return None, f"filter 子句应为对象 {{column, equals|contains|excludes}},收到 {type(c).__name__}: {str(c)[:40]!r}"
    col, how = resolve_column(header, c.get("column", ""))
    if not col:
        return None, f"filter 未找到列「{c.get('column')}」({how})"
    given = [m for m in ("equals", "contains", "excludes") if c.get(m) is not None]
    if len(given) != 1:                              # 缺/多给 → 数据不足(不许退化、不许歧义)
        return None, (f"filter 子句须给 equals/contains/excludes 之一(列「{c.get('column')}」,"
                      f"收到 {given or '空'})")
    mode = given[0]
    tgt = c.get(mode)
    tgts = [str(t).strip().lower() for t in (tgt if isinstance(tgt, list) else [tgt])]
    if not any(tgts):
        return None, f"filter 子句的 {mode} 为空(列「{c.get('column')}」)"
    return (col, mode, tgts), None


def _filter_row_keep(r, resolved):
    """一行是否通过全部子句(AND):equals/contains 要命中才留;excludes 要不命中才留。"""
    for col, mode, tgts in resolved:
        raw = r.get(col, "")
        if mode == "excludes" and _is_null(raw):     # 过滤列为空的行"不含X"只是形式上为真:留下会污染取值
            return False                             # (对端行选择混入残缺的续行/汇总行,实测会比出错误值)
        v = str(raw).strip().lower()
        hit = (v in tgts) if mode == "equals" else any(t and t in v for t in tgts)
        if (mode == "excludes") == hit:              # equals/contains 要命中才留;excludes 要不命中才留
            return False
    return True


# filter 作用于所有读表的内核类;D 顶层不碰数据(filter 写进各 sub 子绑定)、J 不碰数据。
_FILTERABLE = ("A", "C", "G", "P")


# ---------- 参数化查询(param_from):先抽参数、再代入——跨源/同源传参链都在这里实现 ----------
_PARAM_RE = re.compile(r"^@([A-Za-z_]\w*)$")            # @整串引用才算参数;句子里夹 @ 不误伤
_NO_SUBST_KEYS = ("mapping_reason", "condition_text", "node", "id", "note", "reason")


def _subst(obj, params):
    """深代入:任何**整串**形如 "@name" 的字符串换成 params[name],其余原样(深拷贝)。
    引用了 params 里没有的名字 → 抛 KeyError(调用方据此明确报数据不足,绝不拿字面 @xxx 去查——那会错误地判为排除)。
    **作用域**:自带 `param_from` 的子 dict(如 D 的 sub 子绑定)整体跳过——它自成作用域,
    参数在它自己被评估时解析,不继承外层。tuple/set 同 list 处理(子代理写成圆括号/花括号的也不能漏)。"""
    if isinstance(obj, str):
        m = _PARAM_RE.match(obj)
        if m:
            if m.group(1) not in params:
                raise KeyError(m.group(1))
            return params[m.group(1)]
        return obj
    if isinstance(obj, list):
        return [_subst(x, params) for x in obj]
    if isinstance(obj, tuple):
        return [_subst(x, params) for x in obj]          # 顺带统一成 list(内核按 list 处理)
    if isinstance(obj, set):
        return [_subst(x, params) for x in sorted(obj, key=str)]
    if isinstance(obj, dict):
        if "param_from" in obj:                          # 子绑定自成作用域:原样深拷贝、由它自己解析
            return copy.deepcopy(obj)
        return {k: (v if k in _NO_SUBST_KEYS else _subst(v, params)) for k, v in obj.items()}
    return obj


def _find_stray_params(obj, in_doc=False):
    """找出绑定里所有 @整串引用(跳过文案字段)——没有 param_from 却写了 @xxx 是要拦下的易错写法。
    自带 param_from 的子 dict 跳过(自成作用域,由它自己的评估检查);tuple/set 同 list。"""
    if isinstance(obj, str):
        m = _PARAM_RE.match(obj)
        return [] if (in_doc or not m) else [m.group(1)]
    if isinstance(obj, (list, tuple, set)):
        return [n for x in obj for n in _find_stray_params(x, in_doc)]
    if isinstance(obj, dict):
        if "param_from" in obj:
            return []
        return [n for k, v in obj.items() for n in _find_stray_params(v, in_doc or k in _NO_SUBST_KEYS)]
    return []


_SPEC_KEYS = {"source", "column", "filter", "extract"}


def _resolve_values(spec, sources, parent_source):
    """按 spec{source?, column, filter?, extract?} 从某源抽出值列表(非空、去重、保序)。
    返回 (values, None) 或 (None, 错误串)。供 param_from 与 P 的 pair_from 共用。
    未知键明确拒收(extract 拼成 extrct 被静默忽略=参数变成整格混杂文本,实测能把 absent 判成假阳性)。"""
    if not isinstance(spec, dict):
        return None, f"取值规格应为对象 {{source, column, filter?, extract?}},收到 {type(spec).__name__}"
    unknown = set(spec) - _SPEC_KEYS
    if unknown:
        return None, (f"取值规格有不认识的键 {sorted(unknown)}(只接受 source/column/filter/extract"
                      ";expand 写在参数规格顶层)——拼错会被静默忽略、抽出错误的值,拒收")
    src = spec.get("source", parent_source)
    table, err = _spec_table(spec, sources, src)
    if err:
        return None, err
    header, rows = table
    col, how = resolve_column(header, spec.get("column", ""))
    if not col:
        return None, f"未找到列「{spec.get('column')}」({how},源「{src}」)"
    ex = spec.get("extract")
    if ex:
        err = _extract_spec_err(ex)
        if err:
            return None, err
    vals = _distinct_values(rows, col, ex)
    if not vals:
        sample = next((str(r.get(col, ""))[:80] for r in rows if not _is_null(r.get(col, ""))), "")
        return None, (f"源「{src}」列「{col}」一条值也没抽到——核对 filter/extract 与数据格式。"
                      f"该列样例:「{sample}」")
    return vals, None


def _spec_table(spec, sources, src):
    """取值规格对应的表(源缺失/空表 → 错误;带 filter 先过滤,命中 0 行也是错误)。返回 (table, None) 或 (None, 错误串)。"""
    table = sources.get(src)
    if table is None:
        hint = ("「cross」不是数据源名——source 应写最终查询落点/取值来源的**真实源名**"
                if src == "cross" else f"用 --data {src}=<文件> 提供")
        return None, f"无数据源「{src}」({hint})"
    if not table[1]:
        return None, f"数据源「{src}」为空表(仅表头、0 行),无从抽值"
    flt = spec.get("filter")
    if flt:
        table, err = _apply_filter(table, flt)
        if err:
            return None, f"(源「{src}」){err}"
        if not table[1]:
            return None, f"filter 命中 0 行(源「{src}」),无从抽值"
    return table, None


def _distinct_values(rows, col, ex):
    """按列(可选 extract)收集非空取值,去重、保序。"""
    vals, seen = [], set()
    for r in rows:
        v = r.get(col, "")
        if _is_null(v):
            continue
        if ex:
            v = _extract_val(v, ex)
            if v is None:
                continue
        v = str(v).strip()
        if v and v not in seen:
            seen.add(v)
            vals.append(v)
    return vals


_EXPAND_CAP = 32                                        # expand 逐值展开的上限:用于拦住误用,不是性能上限


def _eval_with_params(b, sources, source_name):
    """带 param_from 的绑定:按声明顺序解析各参数(后面的可引用前面的 @参数 → 多跳链),
    再把参数代入查询体(filter 值 / contains·equals·regex 目标 / pair_from 等位置)正常评估。
    至多一个参数可带 expand:"all"/"any"(多值逐个代入:all=全部成立才成立,any=任一成立即成立)。
    参数抽不到/歧义/引用未声明 → 明确报数据不足,绝不带着错误参数查出假结论。"""
    cls = str(b.get("class", "")).upper() or "?"
    pf = b.get("param_from")
    if not isinstance(pf, dict) or not pf:
        return _insuf(cls, 'param_from 应为非空对象 {"参数名": {source, column, filter?, extract?}}')
    resolved, expand, bad = _resolve_params(cls, pf, sources, b.get("source", source_name))
    if bad is not None:
        return bad
    qb = {k: v for k, v in b.items() if k != "param_from"}
    if expand is None:
        fact = _run_substituted(cls, qb, dict(resolved), sources, source_name)
        fact.setdefault("params", dict(resolved))        # 审计:这次查询代入了什么参数
        return fact
    name, mode, vals = expand
    per = [(v, _run_substituted(cls, qb, {**resolved, name: v}, sources, source_name)) for v in vals]
    return _expand_fact(cls, resolved, name, mode, vals, per)


def _param_values(cls, name, spec, resolved, sources, src):
    """解析一个参数:校验参数名、代入已解析的前置参数、抽值。返回 (vals, expand模式, None) 或 (None, None, 数据不足事实)。"""
    if not re.match(r"^[A-Za-z_]\w*$", str(name)):
        return None, None, _insuf(cls, f"参数名「{name}」不合法(限字母/数字/下划线;引用时写 @{name})")
    try:
        spec_r = _subst(spec, resolved)              # 后面的参数可用前面的 → 链;只能向前引用,天然无环
    except KeyError as e:
        return None, None, _insuf(cls, f"参数「{name}」引用了 @{e.args[0]},但它没有排在前面(参数按声明顺序解析,"
                                       "只能引用已解析的)")
    mode = spec_r.pop("expand", None) if isinstance(spec_r, dict) else None
    vals, err = _resolve_values(spec_r, sources, src)
    if err:
        return None, None, _insuf(cls, f"参数「{name}」抽取失败:{err}", param=name)
    return vals, mode, None


def _resolve_params(cls, pf, sources, src):
    """按声明顺序解析 param_from 全部参数。返回 (resolved, expand, None) 或 (None, None, 数据不足事实);
    expand = (参数名, mode, [值…]) 或 None(至多一个参数可带 expand)。"""
    resolved, expand = {}, None                          # expand = (参数名, mode, [值…])
    for name, spec in pf.items():
        vals, mode, bad = _param_values(cls, name, spec, resolved, sources, src)
        if bad is not None:
            return None, None, bad
        if mode is None:
            if len(vals) > 1:
                return None, None, _insuf(cls, f"参数「{name}」有 {len(vals)} 个不同值:{vals[:3]}…——歧义,"
                                               "先用 filter/extract 收窄到唯一值;确要逐值遍历加 \"expand\":\"all\"/\"any\"",
                                          param=name)
            resolved[name] = vals[0]
            continue
        if mode not in ("all", "any"):
            return None, None, _insuf(cls, f"参数「{name}」的 expand「{mode}」不认识(只接受 all/any)")
        if expand is not None:
            return None, None, _insuf(cls, f"只允许一个参数带 expand(「{expand[0]}」和「{name}」都带了)——"
                                           "两个一起展开会组合爆炸,拆成两条绑定")
        if len(vals) > _EXPAND_CAP:
            return None, None, _insuf(cls, f"参数「{name}」expand 值太多({len(vals)} > {_EXPAND_CAP})——"
                                           "先用 filter 收窄再展开", param=name)
        expand = (name, mode, vals)
    return resolved, expand, None


def _run_substituted(cls, qb, params, sources, source_name):
    """把参数代入查询体后评估一次;查询体引用了未声明的 @名 → 数据不足。"""
    try:
        q = _subst(qb, params)
    except KeyError as e:
        return _insuf(cls, f"绑定里用了 @{e.args[0]},但 param_from 没有声明它")
    # _resolved=True:代入完成的查询体不再做 @ 检查——抽出的参数值本身恰好也是 "@xxx" 形式时
    # 必须按字面参与匹配,不许被当成未声明参数误拦(评审实测过这种误报数据不足)
    return evaluate_binding(q, sources, source_name, _resolved=True)


def _expand_fact(cls, resolved, name, mode, vals, per):
    """expand 逐值结果的合成:all=全部成立才成立、any=任一成立即成立(三态),附计数、具体值与代表性子结果。"""
    preds = [f.get("predicate_satisfied") for _, f in per]
    pred, st = _combine_tri("AND" if mode == "all" else "OR", [f for _, f in per])
    n_true = sum(1 for p in preds if p is True)
    n_false = sum(1 for p in preds if p is False)
    n_insuf = sum(1 for _, f in per if f.get("status") != "ok")
    fact = {"class": cls, "status": st, "predicate_satisfied": pred, "params": dict(resolved),
            "expand": {"param": name, "mode": mode, "n": len(vals),
                       "n_true": n_true, "n_false": n_false, "n_insuf": n_insuf},
            "reason": None if st == "ok" else
            f"expand 的 {len(vals)} 个值里 {n_insuf} 个查不了,无法确证整体"}
    return _expand_evidence(fact, mode, per, n_true, n_false)


def _expand_evidence(fact, mode, per, n_true, n_false):
    """expand 事实的证据字段:all 附"哪些值不满足"、any 附"哪些值命中",再带一个代表性子结果。"""
    # 证据要指明具体的值:all 附上"哪些值不满足",any 附上"哪些值命中";再带一个代表性子结果供审计
    if mode == "all" and n_false:
        fact["fail_values"] = [v for v, f in per if f.get("predicate_satisfied") is False][:5]
    if mode == "any" and n_true:
        fact["hit_values"] = [v for v, f in per if f.get("predicate_satisfied") is True][:5]
    sample = next((x for x in per if x[1].get("predicate_satisfied") is (False if mode == "all" else True)),
                  next((x for x in per if x[1].get("status") != "ok"), per[0]))
    fact["sample_value"], fact["sub_facts"] = sample[0], [sample[1]]
    return fact


def k_pair_from(b, sources, parent_source):
    """P 类的跨源形态:两侧各自「选行→取值」再比较(本端/对端在不同文件时用它;
    同表带标记分侧的仍用 side_column 形态)。pair_from = {"local": 取值规格, "peer": 取值规格}。"""
    pf = b.get("pair_from")
    if not isinstance(pf, dict) or "local" not in pf or "peer" not in pf:
        return _insuf("P", 'pair_from 应为 {"local": {source, column, …}, "peer": {…}} 两侧规格')
    if b.get("filter"):                                  # 静默忽略会让两侧取全表值(歧义/错值),明确拒绝
        return _insuf("P", "filter 不作用于 pair_from 顶层——把 filter 分别写进 local/peer 两侧规格")
    fw = b.get("fault_when", "not_equal")
    if fw not in ("not_equal", "equal"):
        return _insuf("P", f"fault_when「{fw}」不认识(只接受 not_equal/equal)")
    got = {}
    for side, zh in (("local", "本端"), ("peer", "对端")):
        vals, err = _resolve_values(pf[side], sources, parent_source)
        if err:
            return _insuf("P", f"查不到{zh}的数据:{err}——{zh}不可查是判不了,不是'不成立'")
        if len(vals) > 1:
            return _insuf("P", f"{zh}取值有歧义:{vals[:3]}…(多个不同值)——先用 filter/extract 收窄")
        got[side] = vals[0]
    same = got["local"] == got["peer"]
    return {"class": "P", "status": "ok", "local": got["local"], "peer": got["peer"],
            "equal": same, "fault_when": fw,
            "local_source": pf["local"].get("source", parent_source),
            "peer_source": pf["peer"].get("source", parent_source),
            "resolved_column": str(pf["local"].get("column", "")),
            "predicate_satisfied": (not same) if fw == "not_equal" else same}


# ---------- S:代码类(人手调优专用)——预制函数库 / 单节点脚本,产出与内核同形的事实 ----------
# 只给人手调优用:生成期的任务说明不介绍本类,merge_conditions 对绑定子代理产出的 S 条目一律拒收。
# 入口约定:check(table, args, load) → {"status":"ok","predicate_satisfied":bool,…} 或
#           {"status":"insufficient_data","reason":"…"};异常一律按数据不足(绝不当作"不成立")。
_SCRIPT_CACHE = {}


def _load_py(path: Path, tag: str):
    key = str(path)
    if key not in _SCRIPT_CACHE:
        import importlib.util
        spec = importlib.util.spec_from_file_location(tag, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SCRIPT_CACHE[key] = mod
    return _SCRIPT_CACHE[key]


def _resolve_script(b):
    """S 绑定 → (callable, 标签, None) 或 (None, None, 错误串)。func=custom_lib 注册表;file=单节点脚本。"""
    here = Path(__file__).resolve().parent
    func, file = b.get("func"), b.get("file")
    if bool(func) == bool(file):
        return None, None, "S 类须 func(函数库函数名)/file(scripts 下脚本路径)二选一,且只选一个"
    if func:
        lib = here / "custom_lib.py"
        if not lib.exists():
            return None, None, "scripts/custom_lib.py 不存在(产物缺函数库,重跑 assemble 补齐)"
        try:
            mod = _load_py(lib, "custom_lib")
        except Exception as e:  # noqa: BLE001
            return None, None, f"custom_lib.py 加载失败:{type(e).__name__}: {e}"
        reg = getattr(mod, "REGISTRY", {})
        fn = reg.get(str(func))
        if not callable(fn):
            return None, None, f"函数「{func}」未注册——custom_lib.REGISTRY 现有:{sorted(reg) or '(空)'}"
        return fn, f"func:{func}", None
    p = (here / str(file)).resolve()
    if not str(p).startswith(str(here)):
        return None, None, f"file 路径越界(必须在 scripts/ 之下,如 custom/<节点>.py):{file}"
    if not p.exists():
        return None, None, f"脚本文件不存在:{file}(相对 scripts/ 目录,如 custom/n31_xxx.py)"
    try:
        mod = _load_py(p, f"s_{p.stem}")
    except Exception as e:  # noqa: BLE001
        return None, None, f"脚本 {file} 加载失败:{type(e).__name__}: {e}"
    fn = getattr(mod, "check", None)
    if not callable(fn):
        return None, None, f"脚本 {file} 没有入口函数 check(table, args, load)"
    return fn, f"file:{file}", None


def k_script(table, b, sources):
    fn, label, err = _resolve_script(b)
    if err:
        return _insuf("S", err)

    def load(name):
        return sources.get(str(name))

    try:
        fact = fn(table, b.get("args") or {}, load)
    except SystemExit as e:                              # CLI 型脚本把"出错"写成 print+sys.exit:SystemExit
        return _insuf("S", f"S 脚本调用了 sys.exit({e.code})——库调用不该退出进程"    # 不是 Exception 子类,
                           "(脚本把错误当命令行处理了,如'目录不存在就 exit');"       # 不拦会让整个 cross 中止
                           "按数据不足处理,绝不当作'不成立'", script=label)
    except Exception as e:  # noqa: BLE001
        import traceback
        frames = traceback.extract_tb(e.__traceback__)
        here_s = str(Path(__file__).resolve().parent)    # 只报 scripts/ 下的帧:stdlib 内部帧(如 re 的
        ours = [f for f in frames                        # _parser.py)定位不到出错行,过滤掉
                if str(Path(f.filename).resolve()).startswith(here_s)]
        where = " ← ".join(f"{Path(f.filename).name}:{f.lineno}({f.name})"
                           for f in reversed((ours or frames)[-3:])) or "?"
        return _insuf("S", f"S 脚本异常({label}):{type(e).__name__}: {e}【位置 {where}】"
                           "——异常一律按数据不足,绝不当作'不成立'", script=label)
    if isinstance(fact, dict) and fact.get("status") == "insufficient_data":
        out = dict(fact)
        out.setdefault("class", "S")
        out.setdefault("script", label)
        out.setdefault("predicate_satisfied", None)
        if not str(out.get("reason") or "").strip():
            out["reason"] = "S 脚本报数据不足但没写 reason——脚本里补一句缺什么"
        return out
    if isinstance(fact, dict) and fact.get("status") == "ok" \
            and isinstance(fact.get("predicate_satisfied"), bool):
        out = dict(fact)
        out.setdefault("class", "S")
        out.setdefault("script", label)                  # fault_check 已写入被判脚本的实际路径(相对路径)时不覆盖
                                                         # (label 是 func:fault_check,没有信息量)
        out.setdefault("explain", f"S 脚本({label})判定 → "
                                  f"{'成立' if out['predicate_satisfied'] else '不成立'}")
        return out
    return _insuf("S", f"S 脚本返回不符合约定格式({label}):须 {{status:'ok', predicate_satisfied:bool, …}} 或 "
                       f"{{status:'insufficient_data', reason:'…'}};实际收到:{str(fact)[:120]}", script=label)


def evaluate_binding(b, sources, source_name, _resolved=False):
    """按 b['class'] 分派到对应内核;D 递归,J 人工,无源/未知类别 → 数据不足。
    若绑定带 `decision`(模型生成时已判定本条无法自动评估)→ 直接按该判定出 status,**不跑内核、不给判断**。
    带 `filter`(A/C/G/P)→ 先行级预过滤再算(EAV 窄表:先选中"哪个量"的行,再对值做统计)。
    _resolved(内部):True=本绑定已由 param_from 代入完成,跳过 @ 残留检查(字面 @ 值合法)。"""
    cls = str(b.get("class", "")).upper()
    fact = _declared_fact(cls, b)
    if fact is not None:
        return fact
    if cls == "J" or b.get("manual"):
        return k_manual(b)
    fact = _unknown_keys_fact(cls, b)
    if fact is not None:
        return fact
    if cls == "S":                                       # 代码类:在 cross 里跑,全部源经 load 可取;
        if b.get("param_from"):                          # source 可选,只是方便取主表(缺省时 table=None,全靠 load)
            return _insuf("S", "S 类不支持 param_from——参数直接写进 args,取源在函数里用 load('<源名>')")
        return k_script(sources.get(source_name), b, sources)
    if b.get("param_from"):                              # 参数化查询:先抽参数、再代入(D/P/A/C/G 都适用)
        return _eval_with_params(b, sources, source_name)
    if not _resolved:
        stray = _find_stray_params(b)
        if stray:                                        # 写了 @xxx 却没有 param_from:拿字面 @ 去查会错误地判为排除,拦下
            return _insuf(cls or "?", f"绑定里用了 @{stray[0]} 却没有 param_from——参数没人解析,"
                                      "查询会拿字面 @ 去匹配(恒不命中=假排除);补 param_from 或去掉 @")
    if cls == "P" and b.get("pair_from"):                # 跨源成对比较:两侧各自选行取值再比
        return k_pair_from(b, sources, source_name)
    if cls == "D":
        if b.get("filter"):                              # 静默忽略会让 sub 在全表上算出假阳性,明确拒绝
            return _insuf("D", "filter 不作用于 D 类顶层——把 filter 写进各 sub 子绑定(各自选行再合成)")
        return k_combo(b, sources, source_name, _resolved)
    table, fact = _source_table(cls, sources, source_name)
    if fact is not None:
        return fact
    fn, fact = _kernel_fn(cls)
    if fact is not None:
        return fact
    flt = b.get("filter")
    if flt:
        return _run_filtered(cls, fn, table, b, flt)
    return fn(table, b)


def _declared_fact(cls, b):
    """绑定带 decision(模型生成时已判定本条无法自动评估)→ 直接按该判定出事实,不跑内核;没带 → None。"""
    decided = b.get("decision")
    if decided == "cross_source":                        # 绑定阶段的跨源标记:应由 merge 收进补充绑定的候选清单,
        return _insuf(cls or "?",                        # 漏到运行时=补充绑定没做完,明确说明而不是当作没事
                      "标记为跨源判据(cross_source),应由跨源补绑环节产出 param_from 绑定——"
                      "出现在运行时说明补绑未完成,回到步骤 2.5 跑 make_dispatch --cross")
    if decided in ("insufficient_data", "manual", "not_applicable"):
        return {"class": cls or "?",
                "status": "manual" if decided == "manual" else "insufficient_data",
                "predicate_satisfied": None, "declared": True, "resolved_column": None,
                "reason": b.get("mapping_reason") or b.get("reason") or f"模型判定:{decided}"}
    return None


def _unknown_keys_fact(cls, b):
    """顶层字段白名单检查:绑定带清单外的字段 → 数据不足事实(明确拒收);合法 → None。"""
    if cls in _CLASS_KEYS:
        unknown = set(b) - _META_KEYS - _CLASS_KEYS[cls]
        if unknown:
            return _insuf(cls, f"绑定带不认识的字段 {sorted(unknown)}({cls} 类只接受 "
                               f"{sorted(_CLASS_KEYS[cls])})——野字段会被静默忽略、"
                               "算出语义错误的确定结论,拒收;修字段名或换类别")
    return None


def _source_table(cls, sources, source_name):
    """取绑定的数据源表:无源/空表 → (None, 数据不足事实);否则 (table, None)。"""
    table = sources.get(source_name)
    if table is None:
        hint = ("「cross」不是数据源名——cross 绑定的 source 应写最终查询落点的**真实源名**(如 config)"
                if source_name == "cross" else f"用 --data {source_name}=<文件> 提供")
        return None, _insuf(cls or "?", f"无数据源「{source_name}」({hint})", resolved_column=None)
    if not table[1]:                                     # 空表(仅表头、0 行):没数据 ≠ 排除——
        return None, _insuf(cls or "?",                  # 别让 matched=0/0 被当作"确定不成立"
                            "数据源为空表(仅表头、0 行数据),无从判定——不当'不成立'处理")
    return table, None


def _kernel_fn(cls):
    """按类别取读表内核:封存类/未知类 → (None, 数据不足事实);否则 (内核函数, None)。"""
    fn = _KERNELS.get(cls)
    if not fn:
        if cls in _ARCHIVED:
            return None, _insuf(cls, f"类别「{cls}」属 v1 内核,仍封存于 archive/kernels-v1——"
                                     "F 改用 C 类 equals/extract,I 改用 A 类 count_rows")
        return None, _insuf(cls or "?", f"未知条件类别「{cls}」(本内核集只接受 A/B/C/D/E/G/H/J/P)")
    return fn, None


def _run_filtered(cls, fn, table, b, flt):
    """带 filter 的内核调用:先行级预过滤再对子集算,并把 filter/filter_rows 记入事实。"""
    table, err = _apply_filter(table, flt)
    if err:
        return _insuf(cls, err, resolved_column=None)
    if not table[1]:                                 # 过滤后 0 行 = 该指标项/取值本次没采集到
        fact = _insuf(cls, "filter 命中 0 行(该指标项/取值本次未采集),无从判定——不当'不成立'处理")
        fact["filter"] = flt
        return fact
    fact = fn(table, b)
    fact["filter"], fact["filter_rows"] = flt, len(table[1])
    return fact
