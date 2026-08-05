#!/usr/bin/env python3
"""按条件类别(A/C/G/D/J/P)预制的"统计内核模板"+ 列解析 + 取值(extract)+ 处理变换词汇。

设计:把**易错的统计逻辑**(阈值计数、字段/正则匹配、格内取值、负存在、成对比较、多条件组合)
固化成测过的内核;运行时由 LLM 产出的**绑定**(选哪个类别内核、对哪列、行过滤 filter、格内
extract、剥前缀/缩放等变换、阈值、路由到哪个源)来参数化它们。内核只产**事实**:算出的量 +
provenance(命中了哪列、怎么命中、nulls)+ `predicate_satisfied`(**仅对解析到的数据做字面
比较**的结果)。v1 的 B/E/F/H/I 内核已封存于 archive/kernels-v1(数通场景普查零命中)。

铁律(机械层就守住,别等到 RCA):
- 列解析不到 → `status:"insufficient_data"`,**绝不**给 `predicate_satisfied`,**绝不**编造值。
- `Common:`/`Voice:` 这类**组前缀**由解析器机械剥离(按首个冒号);**语义**对哪列由 LLM 在绑定里给。
- NIL/空 = 缺测,计入 `nulls`,不当类别、不当 0。
- 成立/不成立的**最终判定**(含语义是否真等、路径回溯、数据不足传播)仍由 fault-tree-rca 做;
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
_FW_OPS = {"≥": ">=", "≤": "<=", "≠": "!=", "＞": ">", "＜": "<", "＝": "=="}   # 全角/数学符 → 归一
_AGGS = {"mean", "min", "max", "sum", "last", "first"}
_SCOPES = {"aggregate", "count_rows"}
# 平台改名兼容(2026-07 起 diag 更名 cmd):同一份数据两代列名/源名互认,旧绑定/旧调优表不作废。
# 列名同义(键/值都用 _norm 归一形态):diag 的 ObjContent ↔ cmd 汇总表的 CmdResult。
_COL_ALIASES = {"objcontent": ("cmdresult",), "cmdresult": ("objcontent",)}
_SOURCE_ALIASES = {"diag": "cmd", "cmd": "diag"}         # 源名同义:绑定写哪个名都能找到数据


def alias_sources(sources: dict) -> dict:
    """源名兼容整形:sources 里只有 cmd/diag 之一时,补一个同义键指向**同一张表**(不复制)。
    绑定/调优表用旧名、数据用新名(或反之)都能对上;两名都给了(真有两份数据)则各归各,不覆盖。"""
    for a, b in _SOURCE_ALIASES.items():
        if a in sources and b not in sources:
            sources[b] = sources[a]
    return sources


def _shape_err(b, need_op=False):
    """数值类参数体检(A 类及带 op/threshold 的绑定共用):全角运算符就地归一;非法枚举/非数值阈值/require<1
    → 返回错误串(调用方响亮出"数据不足"),合法 → None。宗旨:绑定写错绝不静默降级——
    实测 op 全角/agg 拼错/scope 拼错/require:0/字符串阈值,旧内核全部无声算出方向性错误结论。"""
    if b.get("op") is not None:
        op = _FW_OPS.get(str(b["op"]).strip(), str(b["op"]).strip())
        if op not in _OPS:
            return f"op「{b['op']}」不认识(只认 < <= > >= == != =)"
        b["op"] = op
    elif need_op:
        return "缺 op"
    if b.get("threshold") is not None and _num(b["threshold"]) is None:
        return f"threshold「{b['threshold']}」不是数值(状态/字符串比较请改 C 类 equals/contains)"
    if need_op and b.get("threshold") is None:
        return "缺 threshold"
    if b.get("agg") is not None and str(b["agg"]) not in _AGGS:
        return f"agg「{b['agg']}」不认识(只认 {'/'.join(sorted(_AGGS))})"
    if b.get("scope") is not None and str(b["scope"]) not in _SCOPES:
        return f"scope「{b['scope']}」不认识(只认 aggregate/count_rows)"
    if b.get("require") is not None:
        rq = _num(b["require"])
        if rq is None or rq < 1:
            return f"require「{b['require']}」不合法(须为 ≥1 的数;0 会恒判成立)"
        if b.get("scope") != "count_rows":               # 实测:忘写 scope 时 require 被静默忽略、按均值出确定结论
            return ("require 只在 scope:\"count_rows\" 下有效——现在会被静默忽略、按 aggregate 聚合出错误结论;"
                    "补 \"scope\":\"count_rows\" 或去掉 require")
    return None


# ---------- 取值层(extract):从一格杂糅文本里抽出要的那段值,贯穿 A/C/G/P ----------
def _extract_spec_err(spec):
    """extract 参数体检:{"kv": "键名"} 或 {"regex": "正则"} 二选一;正则编译失败=响亮拒收
    (判据里的"关键字"是原料不是成品——%u/%d 是 printf 占位不是正则,绑定层要先换成 \\d+ 等)。"""
    if not isinstance(spec, dict) or (("kv" in spec) == ("regex" in spec)):
        return 'extract 只认 {"kv": "键名"} 或 {"regex": "正则"} 之一'
    if "regex" in spec:
        try:
            re.compile(str(spec["regex"]))
        except re.error as e:
            return f"extract.regex 编译失败:{e}(%u/%d 这类占位要先换成 \\d+;HTML 渣要剔掉)"
    return None


def _extract_val(text, spec):
    """从一格文本抽值:kv=「键 [:=:] 值」(值到 空白/逗号/分号 止,但「,后跟恰好3位数字」当
    千分位续上——设备回显计数「CRC: 1,234」要抽整数,不是截在逗号前的 1);regex=第一个捕获组
    (无捕获组取整体命中)。抽不到返回 None(调用方按缺测/未命中处理,绝不编值)。"""
    s = str(text)
    if "kv" in spec:
        m = re.search(re.escape(str(spec["kv"])) + r"\s*[::=]\s*([^\s,;,;]+(?:,\d{3})*)", s)
        return m.group(1) if m else None
    m = re.search(str(spec["regex"]), s)
    if not m:
        return None
    return m.group(1) if m.groups() else m.group(0)


def _decomma(s):
    """千分位归一:仅当整体呈 1,234[.56] 形态才去逗号(别误伤枚举串),供 extract 抽出的值转数用。"""
    t = str(s).strip()
    return t.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", t) else s


def _hit_snippet(s, pos, width=120):
    """命中处的原文片段:以命中位置为锚开一个窗(整格回显可达数 KB,不能整格搬)。
    换行/连排空白压成单空格,截断处加省略号——给证据引用的是**代码抓的原文**,不过模型的手。"""
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
        import xlsx_reader                               # 同目录零依赖读取器(不需要 openpyxl)
        return xlsx_reader.list_sheets(p)
    if p.suffix.lower() in (".txt", ".cfg", ".mo", ".moc"):
        try:
            from mo_config import mo_sheets
            return mo_sheets(p.read_bytes().decode("utf-8", "ignore"))   # MO 类型清单当"页签"
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
            pass            # 解析失败 → 按 csv 兜底,不崩
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        import xlsx_reader                               # 同目录零依赖读取器;页签不存在同样 ValueError 点名
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
    return x if math.isfinite(x) else None   # "nan"/"inf" 字符串=缺测:进聚合会毒化 mean→恒判不成立(实测)


def _apply_transforms(x, transforms):
    """对一个数值应用处理变换词汇(LLM 在绑定里声明,固定可审):
    scale:<f>(乘,如 /100 写 scale:0.01、156*TA/2 写 scale:78)、abs、neg、round:<n>、
    log10(x≤0 无对数 → 当缺测/null,不编值)。"""
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
    解析不到/有歧义 → (None, 原因),交由上层判数据不足,绝不乱配。"""
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
        # 取值层:格子是杂糅文本(如 "利用率:83%,速率:1G"),先抽出要的那段再转数;抽不到=缺测
        raw = [r.get(col, "") for r in rows]
        picked = [(_extract_val(v, spec) if not _is_null(v) else None) for v in raw]
        vals = [x for x in (_apply_transforms(_num(_decomma(p)), b.get("transforms"))
                            for p in picked if p is not None) if x is not None]
        nulls, n = sum(1 for p in picked if p is None), len(raw)
    else:
        vals, nulls, n = _numeric_series(rows, col, b.get("transforms"))
    op, thr = b.get("op"), b.get("threshold")
    fact = {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
            "n_rows": n, "nulls": nulls, "valid": len(vals), "op": op, "threshold": thr,
            "transforms": b.get("transforms") or []}
    if spec:
        fact["extract"] = spec
        # 命中原文:extract 场景格子是杂糅文本,把"满足比较的那几格"的原文片段抓给证据
        # (纯数值列不抓——格子本身就是那个数,引用无增量)
        hit_texts = []
        for raw_v, p in zip(raw, picked):
            if p is None or len(hit_texts) >= 3:
                continue
            x = _apply_transforms(_num(_decomma(p)), b.get("transforms"))
            if x is not None and _cmp(x, op, thr):
                s = str(raw_v).strip()
                pos = s.find(str(p))
                hit_texts.append(_hit_snippet(s, max(pos, 0)))
        if hit_texts:
            fact["hits"] = hit_texts
    if not vals:
        if spec:
            sample = next((str(r.get(col, ""))[:80] for r in rows if not _is_null(r.get(col, ""))), "")
            return _insuf(cls, f"extract 在该列一条也没抽到数值——核对 kv 键名/正则与数据格式。"
                               f"该列样例:「{sample}」", resolved_column=col, nulls=nulls)
        return _insuf(cls, "该列无有效数值(全空/NIL)", resolved_column=col, nulls=nulls)
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


# ---------- C / G:字段 / 日志匹配 ----------
def k_match(table, b, cls="C"):
    header, rows = table
    col, how = resolve_column(header, b.get("column", ""))
    if not col:
        return _insuf(cls, f"未找到列「{b.get('column')}」({how})", resolved_column=None)
    mode = ("regex" if b.get("regex") else "contains" if b.get("contains") else b.get("match", "exact"))
    targets = b.get("contains") or b.get("equals") or b.get("regex") or []
    if not targets:                                     # C/G 只认 contains/equals/regex 列表;op/target 等野字段内核不读。
        return _insuf(cls, "绑定缺 contains/equals/regex 参数(op/target 等野字段无效)——若不拦,"
                           "空 targets 会静默 matched=0、整源变假排除;修绑定格式",
                      resolved_column=col)
    if b.get("match") is not None and str(b.get("match")) not in ("exact", "contains", "regex"):
        return _insuf(cls, f"match「{b.get('match')}」不认识(只认 exact/contains/regex)——"
                           "旧内核会静默按 exact 算", resolved_column=col)
    if mode == "regex":                                 # 判据自带的"关键字"是原料不是成品:编译不过响亮拒收
        for t in targets:
            try:
                re.compile(str(t))
            except re.error as e:
                return _insuf(cls, f"regex「{t}」编译失败:{e}(%u/%d 这类 printf 占位要先换成 \\d+;"
                                   "HTML 渣要剔掉)——坏正则不拦会整条恒不命中=假排除", resolved_column=col)
    spec = b.get("extract")
    if spec:
        _e = _extract_spec_err(spec)
        if _e:
            return _insuf(cls, f"绑定参数不合法:{_e}", resolved_column=col)
    if b.get("require") is not None:
        _rq = _num(b.get("require"))
        if _rq is None or _rq < 1:
            return _insuf(cls, f"require「{b.get('require')}」不合法(须为 ≥1 的数;0 会恒判成立=假阳)",
                          resolved_column=col)
        if b.get("absent"):
            return _insuf(cls, "absent 与 require 互斥(absent=命中0行才成立,require=至少命中N行)——"
                               "语义矛盾,修绑定", resolved_column=col)
    ci = b.get("case_insensitive", mode == "contains")
    norm = (lambda x: str(x).strip().lower()) if ci else (lambda x: str(x).strip())
    tnorm = [norm(t) for t in targets]
    idc = _id_col(header)
    matched, nulls, samples, dist = 0, 0, [], Counter()
    hit_texts = []                                      # 命中处的原文片段(代码抓取,证据引用直接抄它)
    by_target = Counter()                               # 每个 target 各命中多少行(多 target 时的可审计分解)
    for i, r in enumerate(rows):
        v = r.get(col, "")
        if _is_null(v):
            nulls += 1
            continue
        if spec:                                        # 取值层:先从杂糅格里抽出值,再对抽出的值做匹配
            v = _extract_val(v, spec)
            if v is None:
                nulls += 1                              # 抽不到=该行没有这项信息,按缺测计,不当不命中
                continue
        dist[str(v).strip()[:80]] += 1                  # 同列取值分布(键截 80 字:整格回显可达数 KB,防 top 爆炸)
        nv = norm(v)
        if mode == "contains":
            hits = [j for j, t in enumerate(tnorm) if t in nv]
        elif mode == "regex":
            hits = [j for j, t in enumerate(targets) if re.search(str(t), str(v).strip())]
        else:
            hits = [j for j, t in enumerate(tnorm) if nv == t]
        if hits:
            matched += 1
            for j in hits:
                by_target[str(targets[j])] += 1
            if len(samples) < 3:
                samples.append(str(r.get(idc)) if idc else f"row#{i}")
            if len(hit_texts) < 3:                      # 命中原文:以首个命中 target 的位置为锚开窗
                s = str(v).strip()
                j = hits[0]
                if mode == "regex":
                    m = re.search(str(targets[j]), s)
                    pos = m.start() if m else 0
                else:
                    pos = (s.lower() if ci else s).find(tnorm[j])
                hit_texts.append(_hit_snippet(s, max(pos, 0)))
    total = sum(dist.values())                          # 纳入统计的非空行数 = 占比分母
    if b.get("absent") and total == 0:
        # 负存在判据(故障=「没配置X」)靠"缺行即证据",但整列无值时缺的可能是导出段而非配置——不判成立
        return _insuf(cls, "列全空/表为空,「不存在」无从证明(absent 需列里有实际取值兜底)",
                      resolved_column=col)
    fact = {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
            "match": mode, "targets": targets, "matched": matched, "total": total,
            "ratio": round(matched / total, 4) if total else None, "nulls": nulls,
            "top": dist.most_common(5), "samples": samples,
            "predicate_satisfied": (matched == 0) if b.get("absent")
                                   else matched >= b.get("require", 1)}
    if b.get("absent"):
        fact["absent"] = True                           # 极性标记:matched=0 且成立=「未配置」,让 RCA 读得懂
    if hit_texts:
        fact["hits"] = hit_texts                        # 命中原文片段(代码抓的,evidence 引用只许抄它)
    if spec:
        fact["extract"] = spec                          # 审计:值是从格内抽出来的,不是整格
    if len(targets) > 1:                                # 单 target 时分解=matched,冗余不输出
        # 只进校验/审计层:不在 tree_prune._FACT_KEEP 白名单、不被 question 渲染——不会流进 RCA 上下文
        fact["matched_by_target"] = {str(t): by_target.get(str(t), 0) for t in targets}
    return fact


# ---------- D:多条件组合 ----------
def _combine_tri(comb, results):
    """AND/OR 的三态合成(子结果 → (predicate, status))。"""
    preds = [r.get("predicate_satisfied") for r in results]
    sts = [r.get("status") for r in results]
    if comb == "AND":
        return ((False, "ok") if any(p is False for p in preds)
                else (None, "insufficient_data") if any(s != "ok" for s in sts)
                else (True, "ok"))
    return ((True, "ok") if any(p is True for p in preds)
            else (None, "insufficient_data") if any(s != "ok" for s in sts)
            else (False, "ok"))


def k_combo(b, sources, parent_source, _resolved=False):
    subs = b.get("sub") or []
    if not subs:                                        # 空 AND 的 any(False…)全假 → 恒"成立"(实测假阳),响亮拒绝
        return _insuf("D", "组合绑定缺 sub 子条件列表(字段名写错/被吃掉?)——空组合恒判成立,禁止")
    comb = str(b.get("combine", "AND")).upper()
    if comb not in ("AND", "OR"):                       # 实测:拼错(如 ANDD)会被静默按 OR 算=方向性假阳
        return _insuf("D", f"combine「{b.get('combine')}」不认识(只认 AND/OR)——"
                           "拼错不拦会被静默按 OR 算,'全部满足'悄悄变'任一满足'")

    if b.get("group_by"):
        # 分组组合:按 group_by 列切组,**每组单独跑全部子条件**,存在一个组满足 → 节点成立。
        # 治的是"整列有A也有B、但A和B不在同一接口/对象上"的假确证(业务脚本的 groupby 语义直译)。
        # 组间三态:有组全真→成立;无全真但有组数据不足→数据不足;否则不成立。
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
        groups = {}
        for r in rows:
            gv = str(r.get(gcol, "")).strip()
            if not _is_null(gv):
                groups.setdefault(gv, []).append(r)
        if not groups:
            return _insuf("D", f"分组列「{gcol}」全空,无组可判", resolved_column=gcol)
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
        if first_detail:                                 # 只带一个代表组的子结果当审计样本,防事实爆炸
            fact["sample_group"], fact["sub_facts"] = first_detail[1], first_detail[2]
        return fact

    results = [evaluate_binding(s, sources, s.get("source", parent_source), _resolved)
               for s in subs]
    pred, st = _combine_tri(comb, results)
    return {"class": "D", "status": st, "combine": comb, "predicate_satisfied": pred,
            "reason": None if st == "ok" else "组合含数据不足子条件,无法确证整体", "sub_facts": results}


# ---------- P:成对比较(本端/对端同一信息是否一致)----------
def k_pair(table, b, cls="P"):
    """两侧同一信息的一致性判定(查对端类判据的家)。行选择用通用 filter(evaluate_binding 已前置),
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
        return _insuf(cls, f"fault_when「{fw}」不认识(只认 not_equal/equal)", resolved_column=vcol)
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


# ---------- J:人工确认 ----------
def k_manual(b):
    return {"class": "J", "status": "manual", "predicate_satisfied": None,
            "note": b.get("note", "需人工现场确认,自动指标不可判")}


_KERNELS = {"A": k_threshold, "C": k_match, "G": lambda t, b: k_match(t, b, "G"), "P": k_pair}
_ARCHIVED = set("BEFHI")                                # v1 内核已封存 archive/kernels-v1(数通普查零命中)

# 顶层字段白名单:绑定带清单外的字段 = 响亮拒收("响亮拒收"铁律的 A/D 顶层补漏——实测
# require 忘配 count_rows、A 里误写 contains、combine 拼错,全都被静默忽略后算出语义错误的确定结论)。
_META_KEYS = {"id", "node", "node_id", "condition_text", "class", "source", "mapping_reason",
              "decision", "reason", "mode", "manual", "note", "param_from"}
_CLASS_KEYS = {
    "A": {"column", "op", "threshold", "scope", "agg", "transforms", "require", "filter", "extract"},
    "C": {"column", "equals", "contains", "regex", "match", "case_insensitive", "require",
          "absent", "filter", "extract"},
    "D": {"combine", "sub", "group_by", "filter"},       # D 顶层 filter 有专门的响亮拒绝(信息更准)
    "J": set(),
    "P": {"side_column", "peer_marker", "value_column", "fault_when", "filter", "extract", "pair_from"},
    "S": {"func", "file", "args"},                       # 代码类(人手调优专用):选行/抽值都在函数里做,
}                                                        # 不吃 filter/extract 顶层字段
_CLASS_KEYS["G"] = _CLASS_KEYS["C"]


def _apply_filter(table, flt):
    """行级预过滤——为「EAV 窄表」(一行一个量,如 DataItem/DataValue/Unit 形状)或"先选中某命令/
    某检查项的行"服务:先把行缩到 filter 命中的子集,再让内核对子集算。
    子句 {column, equals:值|[值…] 或 contains:值|[值…] 或 excludes:值|[值…](**不含**才保留,
    如挑「Object 不带 *」的对端行)};多子句(list)= AND,同一行须全满足;大小写不敏感。
    返回 (过滤后table, None) 或 (None, 错误串)(列解析不到 → 上层判数据不足)。"""
    header, rows = table
    clauses = flt if isinstance(flt, list) else [flt]
    resolved = []
    for c in clauses:
        if not isinstance(c, dict):                      # 畸形子句(如写成字符串)→ 数据不足,绝不乱配、绝不崩
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
        resolved.append((col, mode, tgts))
    keep = []
    for r in rows:
        for col, mode, tgts in resolved:
            raw = r.get(col, "")
            if mode == "excludes" and _is_null(raw):     # 过滤列为空的行"不含X"是空洞真:留下会污染取值
                break                                    # (对端行选择被脏续行/汇总行混入,实测能比出错值)
            v = str(raw).strip().lower()
            hit = (v in tgts) if mode == "equals" else any(t and t in v for t in tgts)
            if (mode == "excludes") == hit:              # equals/contains 要命中才留;excludes 要不命中才留
                break
        else:
            keep.append(r)
    return (header, keep), None


# filter 作用于所有吃表的内核类;D 顶层不碰数据(filter 写进各 sub 子绑定)、J 不碰数据。
_FILTERABLE = ("A", "C", "G", "P")


# ---------- 参数化查询(param_from):先抽参数、再代入——跨源/同源传参链的家 ----------
_PARAM_RE = re.compile(r"^@([A-Za-z_]\w*)$")            # @整串引用才算参数;句子里夹 @ 不误伤
_NO_SUBST_KEYS = ("mapping_reason", "condition_text", "node", "id", "note", "reason")


def _subst(obj, params):
    """深代入:任何**整串**形如 "@name" 的字符串换成 params[name],其余原样(深拷贝)。
    引用了 params 里没有的名字 → 抛 KeyError(调用方响亮出数据不足,绝不拿字面 @xxx 去查=假排除)。
    **作用域**:自带 `param_from` 的子 dict(如 D 的 sub 子绑定)整体跳过——它自成作用域,
    参数在它自己被评估时解析,不继承外层。tuple/set 同 list 处理(子代理写圆/花括号也别漏)。"""
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
        return [_subst(x, params) for x in obj]          # 顺手归一成 list(内核按 list 消费)
    if isinstance(obj, set):
        return [_subst(x, params) for x in sorted(obj, key=str)]
    if isinstance(obj, dict):
        if "param_from" in obj:                          # 子绑定自成作用域:原样深拷、自己解析
            return copy.deepcopy(obj)
        return {k: (v if k in _NO_SUBST_KEYS else _subst(v, params)) for k, v in obj.items()}
    return obj


def _find_stray_params(obj, in_doc=False):
    """找出绑定里所有 @整串引用(跳过文案字段)——没 param_from 却写了 @xxx 是要拦的脚枪。
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
    未知键响亮拒收(extract 拼成 extrct 被静默忽略=参数变整格杂糅文本,实测能把 absent 判成假阳)。"""
    if not isinstance(spec, dict):
        return None, f"取值规格应为对象 {{source, column, filter?, extract?}},收到 {type(spec).__name__}"
    unknown = set(spec) - _SPEC_KEYS
    if unknown:
        return None, (f"取值规格有不认识的键 {sorted(unknown)}(只认 source/column/filter/extract"
                      ";expand 写在参数规格顶层)——拼错会被静默忽略、抽出错误的值,拒收")
    src = spec.get("source", parent_source)
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
    header, rows = table
    col, how = resolve_column(header, spec.get("column", ""))
    if not col:
        return None, f"未找到列「{spec.get('column')}」({how},源「{src}」)"
    ex = spec.get("extract")
    if ex:
        err = _extract_spec_err(ex)
        if err:
            return None, err
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
    if not vals:
        sample = next((str(r.get(col, ""))[:80] for r in rows if not _is_null(r.get(col, ""))), "")
        return None, (f"源「{src}」列「{col}」一条值也没抽到——核对 filter/extract 与数据格式。"
                      f"该列样例:「{sample}」")
    return vals, None


_EXPAND_CAP = 32                                        # expand 逐值展开上限:防呆不防爆


def _eval_with_params(b, sources, source_name):
    """带 param_from 的绑定:按声明顺序解析各参数(后面的可引用前面的 @参数 → 多跳链),
    再把参数代入查询体(filter 值 / contains·equals·regex 目标 / pair_from 等位置)正常评估。
    至多一个参数可带 expand:"all"/"any"(多值逐个代入:all=全部成立才成立,any=任一成立即成立)。
    参数抽不到/歧义/引用未声明 → 响亮数据不足,绝不带着坏参数查出假结论。"""
    cls = str(b.get("class", "")).upper() or "?"
    pf = b.get("param_from")
    if not isinstance(pf, dict) or not pf:
        return _insuf(cls, 'param_from 应为非空对象 {"参数名": {source, column, filter?, extract?}}')
    resolved, expand = {}, None                          # expand = (参数名, mode, [值…])
    for name, spec in pf.items():
        if not re.match(r"^[A-Za-z_]\w*$", str(name)):
            return _insuf(cls, f"参数名「{name}」不合法(限字母/数字/下划线;引用时写 @{name})")
        try:
            spec_r = _subst(spec, resolved)              # 后面的参数可用前面的 → 链;只能向前引用,天然无环
        except KeyError as e:
            return _insuf(cls, f"参数「{name}」引用了 @{e.args[0]},但它没有排在前面(参数按声明顺序解析,"
                               "只能引用已解析的)")
        mode = spec_r.pop("expand", None) if isinstance(spec_r, dict) else None
        vals, err = _resolve_values(spec_r, sources, b.get("source", source_name))
        if err:
            return _insuf(cls, f"参数「{name}」抽取失败:{err}", param=name)
        if mode is None:
            if len(vals) > 1:
                return _insuf(cls, f"参数「{name}」有 {len(vals)} 个不同值:{vals[:3]}…——歧义,"
                                   "先用 filter/extract 收窄到唯一值;确要逐值遍历加 \"expand\":\"all\"/\"any\"",
                              param=name)
            resolved[name] = vals[0]
            continue
        if mode not in ("all", "any"):
            return _insuf(cls, f"参数「{name}」的 expand「{mode}」不认识(只认 all/any)")
        if expand is not None:
            return _insuf(cls, f"只允许一个参数带 expand(「{expand[0]}」和「{name}」都带了)——"
                               "两个一起展开会组合爆炸,拆成两条绑定")
        if len(vals) > _EXPAND_CAP:
            return _insuf(cls, f"参数「{name}」expand 值太多({len(vals)} > {_EXPAND_CAP})——"
                               "先用 filter 收窄再展开", param=name)
        expand = (name, mode, vals)
    qb = {k: v for k, v in b.items() if k != "param_from"}

    def _run(extra):
        try:
            q = _subst(qb, {**resolved, **extra})
        except KeyError as e:
            return _insuf(cls, f"绑定里用了 @{e.args[0]},但 param_from 没有声明它")
        # _resolved=True:代入完成的查询体不再做 @ 检查——抽出的参数值本身恰好长成 "@xxx" 时
        # 必须按字面参与匹配,不许被当成未声明参数误杀(评审实测过这条假不足)
        return evaluate_binding(q, sources, source_name, _resolved=True)

    if expand is None:
        fact = _run({})
        fact.setdefault("params", dict(resolved))        # 审计:这次查询代入了什么参数
        return fact
    name, mode, vals = expand
    per = [(v, _run({name: v})) for v in vals]
    preds = [f.get("predicate_satisfied") for _, f in per]
    sts = [f.get("status") for _, f in per]
    if mode == "all":
        pred, st = ((False, "ok") if any(p is False for p in preds)
                    else (None, "insufficient_data") if any(s != "ok" for s in sts)
                    else (True, "ok"))
    else:
        pred, st = ((True, "ok") if any(p is True for p in preds)
                    else (None, "insufficient_data") if any(s != "ok" for s in sts)
                    else (False, "ok"))
    n_true = sum(1 for p in preds if p is True)
    n_false = sum(1 for p in preds if p is False)
    n_insuf = sum(1 for _, f in per if f.get("status") != "ok")
    fact = {"class": cls, "status": st, "predicate_satisfied": pred, "params": dict(resolved),
            "expand": {"param": name, "mode": mode, "n": len(vals),
                       "n_true": n_true, "n_false": n_false, "n_insuf": n_insuf},
            "reason": None if st == "ok" else
            f"expand 的 {len(vals)} 个值里 {n_insuf} 个查不了,无法确证整体"}
    # 证据要点名到值:all 挂"谁违规",any 挂"谁命中";再带一个代表性子结果供审计
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
    if b.get("filter"):                                  # 静默忽略会让两侧取全表值(歧义/错值),响亮拒绝
        return _insuf("P", "filter 不作用于 pair_from 顶层——把 filter 分别写进 local/peer 两侧规格")
    fw = b.get("fault_when", "not_equal")
    if fw not in ("not_equal", "equal"):
        return _insuf("P", f"fault_when「{fw}」不认识(只认 not_equal/equal)")
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
# 只给人手调优用:生成期简报不介绍本类,merge_conditions 对绑定子代理产出的 S 条目机械拒收。
# 入口契约:check(table, args, load) → {"status":"ok","predicate_satisfied":bool,…} 或
#           {"status":"insufficient_data","reason":"…"};异常一律按数据不足(绝不冒充"不成立")。
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
                           "(脚本把错误当命令行处理了,如'目录不存在就 exit');"       # 不拦会炸掉整个 cross
                           "按数据不足处理,绝不冒充'不成立'", script=label)
    except Exception as e:  # noqa: BLE001
        import traceback
        frames = traceback.extract_tb(e.__traceback__)
        here_s = str(Path(__file__).resolve().parent)    # 只报 scripts/ 下的帧:stdlib 内部帧(如 re 的
        ours = [f for f in frames                        # _parser.py)定位不到病灶行,过滤掉
                if str(Path(f.filename).resolve()).startswith(here_s)]
        where = " ← ".join(f"{Path(f.filename).name}:{f.lineno}({f.name})"
                           for f in reversed((ours or frames)[-3:])) or "?"
        return _insuf("S", f"S 脚本异常({label}):{type(e).__name__}: {e}【位置 {where}】"
                           "——异常一律按数据不足,绝不冒充'不成立'", script=label)
    if isinstance(fact, dict) and fact.get("status") == "insufficient_data":
        out = dict(fact)
        out.setdefault("class", "S")
        out["script"] = label
        out.setdefault("predicate_satisfied", None)
        if not str(out.get("reason") or "").strip():
            out["reason"] = "S 脚本报数据不足但没写 reason——脚本里补一句缺什么"
        return out
    if isinstance(fact, dict) and fact.get("status") == "ok" \
            and isinstance(fact.get("predicate_satisfied"), bool):
        out = dict(fact)
        out.setdefault("class", "S")
        out["script"] = label
        out.setdefault("explain", f"S 脚本({label})判定 → "
                                  f"{'成立' if out['predicate_satisfied'] else '不成立'}")
        return out
    return _insuf("S", f"S 脚本返回不合契约({label}):须 {{status:'ok', predicate_satisfied:bool, …}} 或 "
                       f"{{status:'insufficient_data', reason:'…'}};实际收到:{str(fact)[:120]}", script=label)


def evaluate_binding(b, sources, source_name, _resolved=False):
    """按 b['class'] 分派到对应内核;D 递归,J 人工,无源/未知类别 → 数据不足。
    若绑定带 `decision`(模型生成时已判本条无法机械评估)→ 直接按该判定出 status,**不跑内核、不给判断**。
    带 `filter`(A/C/G/P)→ 先行级预过滤再算(EAV 窄表:先选中"哪个量"的行,再对值做统计)。
    _resolved(内部):True=本绑定已由 param_from 代入完成,跳过 @ 残留检查(字面 @ 值合法)。"""
    cls = str(b.get("class", "")).upper()
    decided = b.get("decision")
    if decided == "cross_source":                        # 绑定期的跨源标记:应被 merge 收进补绑候选,
        return _insuf(cls or "?",                        # 漏到运行时=补绑没做完,响亮说明而不是装没事
                      "标记为跨源判据(cross_source),应由跨源补绑环节产出 param_from 绑定——"
                      "出现在运行时说明补绑未完成,回到步骤 2.5 跑 make_dispatch --cross")
    if decided in ("insufficient_data", "manual", "not_applicable"):
        return {"class": cls or "?",
                "status": "manual" if decided == "manual" else "insufficient_data",
                "predicate_satisfied": None, "declared": True, "resolved_column": None,
                "reason": b.get("mapping_reason") or b.get("reason") or f"模型判定:{decided}"}
    if cls == "J" or b.get("manual"):
        return k_manual(b)
    if cls in _CLASS_KEYS:
        unknown = set(b) - _META_KEYS - _CLASS_KEYS[cls]
        if unknown:
            return _insuf(cls, f"绑定带不认识的字段 {sorted(unknown)}({cls} 类只认 "
                               f"{sorted(_CLASS_KEYS[cls])})——野字段会被静默忽略、"
                               "算出语义错误的确定结论,拒收;修字段名或换类别")
    if cls == "S":                                       # 代码类:在 cross 里跑,全部源经 load 可取;
        if b.get("param_from"):                          # source 可选=主表便利(缺省 table=None 全靠 load)
            return _insuf("S", "S 类不支持 param_from——参数直接写进 args,取源在函数里用 load('<源名>')")
        return k_script(sources.get(source_name), b, sources)
    if b.get("param_from"):                              # 参数化查询:先抽参数、再代入(D/P/A/C/G 通吃)
        return _eval_with_params(b, sources, source_name)
    if not _resolved:
        stray = _find_stray_params(b)
        if stray:                                        # 写了 @xxx 却没 param_from:拿字面 @ 去查=假排除,拦
            return _insuf(cls or "?", f"绑定里用了 @{stray[0]} 却没有 param_from——参数没人解析,"
                                      "查询会拿字面 @ 去匹配(恒不命中=假排除);补 param_from 或去掉 @")
    if cls == "P" and b.get("pair_from"):                # 跨源成对比较:两侧各自选行取值再比
        return k_pair_from(b, sources, source_name)
    if cls == "D":
        if b.get("filter"):                              # 静默忽略会让 sub 在全表上算出假阳,响亮拒绝
            return _insuf("D", "filter 不作用于 D 类顶层——把 filter 写进各 sub 子绑定(各自选行再合成)")
        return k_combo(b, sources, source_name, _resolved)
    table = sources.get(source_name)
    if table is None:
        hint = ("「cross」不是数据源名——cross 绑定的 source 应写最终查询落点的**真实源名**(如 config)"
                if source_name == "cross" else f"用 --data {source_name}=<文件> 提供")
        return _insuf(cls or "?", f"无数据源「{source_name}」({hint})", resolved_column=None)
    if not table[1]:                                     # 空表(仅表头、0 行):没数据 ≠ 排除——
        return _insuf(cls or "?",                        # 别让 matched=0/0 冒充"确定不成立"
                      "数据源为空表(仅表头、0 行数据),无从判定——不当'不成立'处理")
    fn = _KERNELS.get(cls)
    if not fn:
        if cls in _ARCHIVED:
            return _insuf(cls, f"类别「{cls}」属 v1 内核,已封存于 archive/kernels-v1(数通场景不用)——"
                               "改用 A/C/G/D/P 组合表达,或复活 v1 内核集")
        return _insuf(cls or "?", f"未知条件类别「{cls}」(本内核集只认 A/C/D/G/J/P)")
    flt = b.get("filter")
    if flt:
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
    return fn(table, b)
