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
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=True, data_only=True)
        names = list(wb.sheetnames)
        wb.close()
        return names
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
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=True, data_only=True)
        if sheet is not None:
            if sheet not in wb.sheetnames:
                avail = list(wb.sheetnames)
                wb.close()
                raise ValueError(f"页签「{sheet}」不存在;可用页签:{avail}")
            ws = wb[sheet]
        else:
            ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [("" if h is None else str(h)).strip() for h in next(it, [])]
        rows = [{header[i]: (("" if r[i] is None else str(r[i])) if i < len(r) else "")
                 for i in range(len(header))} for r in it]
        wb.close()
        return header, rows
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
    顺序:精确 → 剥组前缀后精确 → 归一化(去前缀/空格/大小写)唯一 → 嵌套路径取末段。
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
        dist[str(v).strip()] += 1                       # 同列取值分布(给 RCA 判分量/主次)
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


def k_combo(b, sources, parent_source):
    subs = b.get("sub") or []
    if not subs:                                        # 空 AND 的 any(False…)全假 → 恒"成立"(实测假阳),响亮拒绝
        return _insuf("D", "组合绑定缺 sub 子条件列表(字段名写错/被吃掉?)——空组合恒判成立,禁止")
    comb = str(b.get("combine", "AND")).upper()

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
            results = [evaluate_binding(s, gsources, parent_source) for s in subs]
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

    results = [evaluate_binding(s, sources, s.get("source", parent_source))
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


def _apply_filter(table, flt):
    """行级预过滤——为「EAV 窄表」(一行一个量,如 DataItem/DataValue/Unit 形状)服务:
    先把行缩到 filter 命中的子集,再让内核对子集算。子句 {column, equals:值|[值…] 或 contains:值|[值…]};
    多子句(list)= AND,同一行须全满足;大小写不敏感。
    返回 (过滤后table, None) 或 (None, 错误串)(列解析不到 → 上层判数据不足)。"""
    header, rows = table
    clauses = flt if isinstance(flt, list) else [flt]
    resolved = []
    for c in clauses:
        if not isinstance(c, dict):                      # 畸形子句(如写成字符串)→ 数据不足,绝不乱配、绝不崩
            return None, f"filter 子句应为对象 {{column, equals|contains}},收到 {type(c).__name__}: {str(c)[:40]!r}"
        col, how = resolve_column(header, c.get("column", ""))
        if not col:
            return None, f"filter 未找到列「{c.get('column')}」({how})"
        if c.get("equals") is None and c.get("contains") is None:   # 缺目标值 → 数据不足(不许退化成 contains:"none")
            return None, f"filter 子句缺 equals/contains(列「{c.get('column')}」)"
        mode = "equals" if c.get("equals") is not None else "contains"
        tgt = c.get(mode)
        tgts = [str(t).strip().lower() for t in (tgt if isinstance(tgt, list) else [tgt])]
        if not any(tgts):
            return None, f"filter 子句的 {mode} 为空(列「{c.get('column')}」)"
        resolved.append((col, mode, tgts))
    keep = []
    for r in rows:
        for col, mode, tgts in resolved:
            v = str(r.get(col, "")).strip().lower()
            if not ((v in tgts) if mode == "equals" else any(t and t in v for t in tgts)):
                break
        else:
            keep.append(r)
    return (header, keep), None


# filter 作用于所有吃表的内核类;D 顶层不碰数据(filter 写进各 sub 子绑定)、J 不碰数据。
_FILTERABLE = ("A", "C", "G", "P")


def evaluate_binding(b, sources, source_name):
    """按 b['class'] 分派到对应内核;D 递归,J 人工,无源/未知类别 → 数据不足。
    若绑定带 `decision`(模型生成时已判本条无法机械评估)→ 直接按该判定出 status,**不跑内核、不给判断**。
    带 `filter`(A/C/G/P)→ 先行级预过滤再算(EAV 窄表:先选中"哪个量"的行,再对值做统计)。"""
    cls = str(b.get("class", "")).upper()
    decided = b.get("decision")
    if decided in ("insufficient_data", "manual", "not_applicable"):
        return {"class": cls or "?",
                "status": "manual" if decided == "manual" else "insufficient_data",
                "predicate_satisfied": None, "declared": True, "resolved_column": None,
                "reason": b.get("mapping_reason") or b.get("reason") or f"模型判定:{decided}"}
    if cls == "J" or b.get("manual"):
        return k_manual(b)
    if cls == "D":
        if b.get("filter"):                              # 静默忽略会让 sub 在全表上算出假阳,响亮拒绝
            return _insuf("D", "filter 不作用于 D 类顶层——把 filter 写进各 sub 子绑定(各自选行再合成)")
        return k_combo(b, sources, source_name)
    table = sources.get(source_name)
    if table is None:
        return _insuf(cls or "?", f"无数据源「{source_name}」(用 --data {source_name}=<文件> 提供)",
                      resolved_column=None)
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
