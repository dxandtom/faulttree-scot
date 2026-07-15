#!/usr/bin/env python3
"""按条件类别(A–J)预制的"统计内核模板"+ 列解析 + 处理变换词汇。

设计:把**易错的统计逻辑**(占比/TopN、差值、阈值计数、字段匹配……)固化成测过的内核;
运行时由 LLM 产出的**绑定**(选哪个类别内核、对哪列、剥前缀/缩放等变换、阈值、路由到哪个源)
来参数化它们。内核只产**事实**:算出的量 + provenance(命中了哪列、怎么命中、nulls)+
`predicate_satisfied`(**仅对解析到的数据做字面比较**的结果)。

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
    """数值类内核(A/B/E/I)共用的参数体检:全角运算符就地归一;非法枚举/非数值阈值/require<1
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
    vals, nulls, n = _numeric_series(rows, col, b.get("transforms"))
    op, thr = b.get("op"), b.get("threshold")
    fact = {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
            "n_rows": n, "nulls": nulls, "valid": len(vals), "op": op, "threshold": thr,
            "transforms": b.get("transforms") or []}
    if not vals:
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


# ---------- B:差值计算 ----------
def k_difference(table, b, cls="B"):
    header, rows = table
    mens, subs = b.get("minuend") or [], b.get("subtrahend") or []
    if not mens or not subs:
        return _insuf(cls, "minuend/subtrahend 缺失或为空——差值两侧都必须给原始计数列"
                           "(单侧为空会算成 0-0=0、恒判不成立)", resolved_column=None)
    err = _shape_err(b)
    if err:
        return _insuf(cls, f"绑定参数不合法:{err}", resolved_column=None)
    cols, missing = {}, []
    for name in mens + subs:
        c, _ = resolve_column(header, name)
        (cols.__setitem__(name, c) if c else missing.append(name))
    if missing:
        return _insuf(cls, f"缺原始计数列:{missing}(只有成功率/无原始计数 → 无法算差值)",
                      resolved_column=None, missing=missing)
    tr = b.get("transforms")
    per, nulls = [], 0
    for r in rows:
        mv = [_num(r.get(cols[n], "")) for n in mens]
        sv = [_num(r.get(cols[n], "")) for n in subs]
        if any(x is None for x in mv + sv):
            nulls += 1
            continue
        per.append(sum(_apply_transforms(x, tr) for x in mv) - sum(_apply_transforms(x, tr) for x in sv))
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


# ---------- C / G:字段 / 日志匹配 ----------
def k_match(table, b, cls="C"):
    header, rows = table
    col, how = resolve_column(header, b.get("column", ""))
    if not col:
        return _insuf(cls, f"未找到列「{b.get('column')}」({how})", resolved_column=None)
    mode = "contains" if b.get("contains") else b.get("match", "exact")
    targets = b.get("contains") or b.get("equals") or []
    if not targets:                                     # C/G 只认 contains/equals 列表;op/target 等野字段内核不读。
        return _insuf(cls, "绑定缺 contains/equals 参数(op/target 等野字段无效)——若不拦,"
                           "空 targets 会静默 matched=0、整源变假排除;修绑定格式",
                      resolved_column=col)
    if b.get("match") is not None and str(b.get("match")) not in ("exact", "contains"):
        return _insuf(cls, f"match「{b.get('match')}」不认识(只认 exact/contains;正则请写 custom)——"
                           "旧内核会静默按 exact 算", resolved_column=col)
    if b.get("require") is not None:
        _rq = _num(b.get("require"))
        if _rq is None or _rq < 1:
            return _insuf(cls, f"require「{b.get('require')}」不合法(须为 ≥1 的数;0 会恒判成立=假阳)",
                          resolved_column=col)
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
        dist[str(v).strip()] += 1                       # 同列取值分布(给 RCA 判分量/主次)
        nv = norm(v)
        if mode == "contains":
            hits = [j for j, t in enumerate(tnorm) if t in nv]
        else:
            hits = [j for j, t in enumerate(tnorm) if nv == t]
        if hits:
            matched += 1
            for j in hits:
                by_target[str(targets[j])] += 1
            if len(samples) < 3:
                samples.append(str(r.get(idc)) if idc else f"row#{i}")
    total = sum(dist.values())                          # 纳入统计的非空行数 = 占比分母
    fact = {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
            "match": mode, "targets": targets, "matched": matched, "total": total,
            "ratio": round(matched / total, 4) if total else None, "nulls": nulls,
            "top": dist.most_common(5), "samples": samples,
            "predicate_satisfied": matched >= b.get("require", 1)}
    if len(targets) > 1:                                # 单 target 时分解=matched,冗余不输出
        # 只进校验/审计层:不在 tree_prune._FACT_KEEP 白名单、不被 question 渲染——不会流进 RCA 上下文
        fact["matched_by_target"] = {str(t): by_target.get(str(t), 0) for t in targets}
    return fact


# ---------- D:多条件组合 ----------
def k_combo(b, sources, parent_source):
    subs = b.get("sub") or []
    if not subs:                                        # 空 AND 的 any(False…)全假 → 恒"成立"(实测假阳),响亮拒绝
        return _insuf("D", "组合绑定缺 sub 子条件列表(字段名写错/被吃掉?)——空组合恒判成立,禁止")
    results = [evaluate_binding(s, sources, s.get("source", parent_source))
               for s in subs]
    comb = str(b.get("combine", "AND")).upper()
    preds = [r.get("predicate_satisfied") for r in results]
    sts = [r.get("status") for r in results]
    if comb == "AND":
        pred, st = ((False, "ok") if any(p is False for p in preds)
                    else (None, "insufficient_data") if any(s != "ok" for s in sts)
                    else (True, "ok"))
    else:  # OR
        pred, st = ((True, "ok") if any(p is True for p in preds)
                    else (None, "insufficient_data") if any(s != "ok" for s in sts)
                    else (False, "ok"))
    return {"class": "D", "status": st, "combine": comb, "predicate_satisfied": pred,
            "reason": None if st == "ok" else "组合含数据不足子条件,无法确证整体", "sub_facts": results}


# ---------- E:占比 / TopN ----------
def k_proportion(table, b, cls="E"):
    header, rows = table
    gb, _ = resolve_column(header, b.get("group_by", ""))
    if not gb:
        return _insuf(cls, f"未找到分组列「{b.get('group_by')}」", resolved_column=None)
    err = _shape_err(b)
    if err:
        return _insuf(cls, f"绑定参数不合法:{err}", resolved_column=gb)
    filt = b.get("filter") or {}
    fcol = None
    if filt.get("column"):
        fcol, _ = resolve_column(header, filt["column"])
        if not fcol:
            return _insuf(cls, f"未找到过滤列「{filt['column']}」")
    tnorm = [str(t).strip() for t in (filt.get("contains") or filt.get("equals") or [])]
    fmode = "contains" if filt.get("contains") else "exact"
    sel = []
    for r in rows:
        if fcol:
            v = str(r.get(fcol, "")).strip()
            if not (any(t in v for t in tnorm) if fmode == "contains" else v in tnorm):
                continue
        sel.append(r)
    if not sel:
        return {"class": cls, "status": "ok", "group_by": gb, "filtered_n": 0,
                "predicate_satisfied": False, "note": "过滤后无记录"}
    c = Counter(str(r.get(gb, "")).strip() for r in sel if not _is_null(r.get(gb, "")))
    if not c:
        return _insuf(cls, "分组列全空")
    top, topn = c.most_common(1)[0]
    ratio = topn / sum(c.values())
    op, thr = b.get("op", ">"), b.get("threshold", 0.7)
    return {"class": cls, "status": "ok", "group_by": gb, "filter_col": fcol,
            "filtered_n": len(sel), "valid": sum(c.values()), "top1": top, "top1_count": topn,
            "top1_ratio": round(ratio, 4), "op": op, "threshold": thr,
            "predicate_satisfied": _cmp(ratio, op, thr)}


# ---------- F:配置检查 ----------
def k_config(table, b, cls="F"):
    header, rows = table
    col, how = resolve_column(header, b.get("param", ""))
    if not col:
        return _insuf(cls, f"无配置数据源/未找到参数「{b.get('param')}」(配置核查通常需单独的配置导出)",
                      resolved_column=None)
    vals = [str(r.get(col, "")).strip() for r in rows if not _is_null(r.get(col, ""))]
    if not vals:
        return _insuf(cls, "参数列无值")
    # 故障语义:默认 equals=X → 故障="配置(全部)等于X"(predicate=all 行==X);
    # not_equals=X 或 negate:true → 故障="配置不等于X"(predicate=存在行!=X),
    # 让 predicate_satisfied 始终表示「故障成立」,省得 RCA 再反推(树里"参数≠X才算故障"很常见,如 SsbPeriod≠20ms)。
    neg = bool(b.get("negate")) or ("not_equals" in b)
    raw = b.get("not_equals") if "not_equals" in b else b.get("equals")
    if raw is None or not isinstance(raw, (str, int, float, bool)):
        # 缺参会拿 "None" 去比恒不等、列表会拿 "['X']" 去比恒不等——正极性静默假阴/负极性静默假阳(实测)
        return _insuf(cls, f"equals/not_equals 缺失或不是标量(拿到 {type(raw).__name__}: {raw!r})——"
                           "F 类只认单个标量;多个候选值改 C 类 equals 列表", resolved_column=col)
    exp = str(raw)
    eq_matched = sum(1 for v in vals if v == exp)
    matched = (len(vals) - eq_matched) if neg else eq_matched      # neg:命中=不等于exp的故障行
    satisfied = any(v != exp for v in vals) if neg else all(v == exp for v in vals)
    return {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
            "expected": raw, "compare": ("!=" if neg else "=="),
            "n": len(vals), "matched": matched, "ratio": round(matched / len(vals), 4),
            "distinct_values": list(dict.fromkeys(vals))[:5], "predicate_satisfied": satisfied}


# ---------- H:信令流程 ----------
def k_signaling(table, b, cls="H"):
    """会话级信令判定:按 key 分组,看每个会话的消息集合是否满足 has/lacks;
    可选 where:要求会话里**存在一行**同时满足若干"列=值"约束(如"FAIL 且携带 cause=X")。
    where 写法:单个 {column, equals|contains} 或其列表(列表内所有约束须落在同一行)。"""
    header, rows = table
    msgcol, _ = resolve_column(header, b.get("msg_column", "")) if b.get("msg_column") else (None, "")
    if not msgcol:
        return _insuf(cls, "无信令/消息数据源(需 trace 记录、含消息类型列 msg_column)", resolved_column=None)
    key, _ = resolve_column(header, b.get("key", "")) if b.get("key") else (None, "")
    if b.get("key") and not key:                        # 显式给了 key 却解析不到 → 全表并成一个会话,has/lacks 语义畸变(实测)
        return _insuf(cls, f"未找到会话键列「{b.get('key')}」——解析不到会把全表并成一个会话;"
                           "确要全表当一个会话请显式 key:null", resolved_column=msgcol, msg_column=msgcol)
    has, lacks = set(b.get("has") or []), set(b.get("lacks") or [])

    # 解析 where:行级约束(同一行须满足全部),会话存在这样一行才算满足
    where = b.get("where")
    constraints = []
    if where:
        for cst in (where if isinstance(where, list) else [where]):
            wc, _ = resolve_column(header, cst.get("column", ""))
            if not wc:
                return _insuf(cls, f"where 未找到列「{cst.get('column')}」", resolved_column=None)
            vals = [str(v).strip() for v in (cst.get("contains") or cst.get("equals") or [])]
            if not vals:                                # 空取值的 where 恒不满足 → 全会话假排除(实测),响亮拒绝
                return _insuf(cls, f"where 子句缺 equals/contains 取值(列「{cst.get('column')}」)——"
                                   "空约束恒不满足=假排除;修绑定格式", resolved_column=msgcol, msg_column=msgcol)
            constraints.append((wc, vals, "contains" if cst.get("contains") else "exact"))

    def _row_ok(r):
        for wc, vals, mode in constraints:
            v = str(r.get(wc, "")).strip()
            if not (any(t in v for t in vals) if mode == "contains" else v in vals):
                return False
        return True

    groups = {}  # key -> {"msgs": set, "where_ok": bool}
    for r in rows:
        k = str(r.get(key, "")).strip() if key else "_all"
        g = groups.setdefault(k, {"msgs": set(), "where_ok": not constraints})
        g["msgs"].add(str(r.get(msgcol, "")).strip())
        if constraints and not g["where_ok"] and _row_ok(r):
            g["where_ok"] = True

    # 兜底:has 里的消息名若在整列里从未出现过 → 多半是消息名写错(或本数据无此流程),
    # 不是"真没失败"。别默默给 predicate=False,改判数据不足、把真实词汇样例摆出来。
    all_msgs = set().union(*(g["msgs"] for g in groups.values())) if groups else set()
    unknown_has = sorted(t for t in has if t not in all_msgs)
    if unknown_has:
        sample = sorted(v for v in all_msgs if v)[:15]
        return _insuf(cls, f"has 里的消息名在 msg_column 从未出现:{unknown_has}——疑似消息名与数据词汇不符,"
                           f"或本数据无此流程(非'真没失败')。msg_column 实际取值样例:{sample}",
                      resolved_column=msgcol, msg_column=msgcol,
                      unknown_messages=unknown_has, msg_vocab_sample=sample)

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


# ---------- I:特征分析 ----------
def k_feature(table, b, cls="I"):
    header, rows = table
    col, how = resolve_column(header, b.get("feature") or b.get("column") or "")
    if not col:
        return _insuf(cls, f"未找到特征列「{b.get('feature') or b.get('column')}」({how})", resolved_column=None)
    metric = b.get("metric", "row_threshold")
    if metric == "row_threshold":
        err = _shape_err(b)
        if err:
            return _insuf(cls, f"绑定参数不合法:{err}", resolved_column=col)
        if b.get("threshold") is None:
            return _insuf(cls, "缺 threshold(row_threshold 必须给阈值,缺了会恒判不成立)", resolved_column=col)
        vals, nulls, n = _numeric_series(rows, col, b.get("transforms"))
        if not vals:
            return _insuf(cls, "特征列无有效数值", resolved_column=col)
        op, thr = b.get("op", "<="), b.get("threshold")
        cnt = sum(1 for v in vals if _cmp(v, op, thr))
        return {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
                "transforms": b.get("transforms") or [], "op": op, "threshold": thr,
                "valid": len(vals), "nulls": nulls, "count_satisfied": cnt,
                "ratio": round(cnt / len(vals), 4), "predicate_satisfied": cnt >= b.get("require", 1)}
    raw = [r.get(col, "") for r in rows]
    if metric == "all_null":
        return {"class": cls, "status": "ok", "resolved_column": col, "metric": metric,
                "predicate_satisfied": all(_is_null(v) for v in raw)}
    if metric == "all_same":
        nn = [str(v).strip() for v in raw if not _is_null(v)]
        return {"class": cls, "status": "ok", "resolved_column": col, "metric": metric,
                "distinct": len(set(nn)), "predicate_satisfied": len(nn) > 0 and len(set(nn)) == 1}
    return _insuf(cls, f"未支持的 metric「{metric}」")


# ---------- J:人工确认 ----------
def k_manual(b):
    return {"class": "J", "status": "manual", "predicate_satisfied": None,
            "note": b.get("note", "需人工现场确认,自动指标不可判")}


_KERNELS = {"A": k_threshold, "B": k_difference, "C": k_match, "E": k_proportion,
            "F": k_config, "G": lambda t, b: k_match(t, b, "G"), "H": k_signaling, "I": k_feature}


def _apply_filter(table, flt):
    """行级预过滤——为「EAV 窄表」(一行一个量,如 DataItem/DataValue/Unit 形状)服务:
    先把行缩到 filter 命中的子集,再让内核对子集算。子句 {column, equals:值|[值…] 或 contains:值|[值…]};
    多子句(list)= AND,同一行须全满足;大小写不敏感。语义与 E 类内建 filter、H 类 where 同族。
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


# filter 只作用于这些类:E 有内建 filter、H 有内建 where(会话内语义),各自保持不变;D/J 不碰数据。
_FILTERABLE = ("A", "B", "C", "F", "G", "I")


def evaluate_binding(b, sources, source_name):
    """按 b['class'] 分派到对应内核;D 递归,J 人工,无源/未知类别 → 数据不足。
    若绑定带 `decision`(模型生成时已判本条无法机械评估)→ 直接按该判定出 status,**不跑内核、不给判断**。
    带 `filter`(A/B/C/F/G/I)→ 先行级预过滤再算(EAV 窄表:先选中"哪个量"的行,再对值做统计)。"""
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
        return _insuf(cls or "?", f"未知条件类别「{cls}」(应为 A–J 之一)")
    flt = b.get("filter")
    if flt:
        if cls in _FILTERABLE:
            table, err = _apply_filter(table, flt)
            if err:
                return _insuf(cls, err, resolved_column=None)
            if not table[1]:                             # 过滤后 0 行 = 该指标项/取值本次没采集到
                fact = _insuf(cls, "filter 命中 0 行(该指标项/取值本次未采集),无从判定——不当'不成立'处理")
                fact["filter"] = flt
                return fact
            fact = fn(table, b)
            fact["filter"], fact["filter_rows"] = flt, len(table[1])
            return fact
        if cls == "E":
            if isinstance(flt, list):                    # E 的内建 filter 只吃单 dict,list 会崩内核 → 拦成数据不足
                return _insuf("E", "E 类内建 filter 只接受单个子句(dict);多子句请拆分或改 A/C + filter")
            # 单 dict → 交给 k_proportion 内建处理(注意其语义独立:大小写敏感、命中 0 行按'排除'算)
        else:                                            # H/未知类:静默忽略会在全表上算出假阳,响亮拒绝
            return _insuf(cls, f"filter 不作用于 {cls} 类(H 用内建 where;适用 filter 的是 A/B/C/F/G/I)")
    return fn(table, b)
