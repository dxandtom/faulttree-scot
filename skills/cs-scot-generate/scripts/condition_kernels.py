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
import operator
from collections import Counter, defaultdict
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")
_NULLISH = {"", "na", "n/a", "null", "none", "nil", "-"}
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
        "==": operator.eq, "=": operator.eq, "!=": operator.ne}
_ID_COLS = ("CALLID", "CallID", "Call_Id", "CellId", "cellName", "CellName",
            "Object", "id", "ID", "Index")


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
    return []


def load_table(path: str, sheet=None):
    """读 csv/xlsx → (header, rows[list[dict]])。xlsx 多页签:sheet 给页签名选表,不给则活动页。"""
    p = Path(path)
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
        return float(s)
    except (TypeError, ValueError):
        return None


def _apply_transforms(x, transforms):
    """对一个数值应用处理变换词汇(LLM 在绑定里声明,固定可审):
    scale:<f>(乘,如 /100 写 scale:0.01、156*TA/2 写 scale:78)、abs、neg、round:<n>。"""
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
                    predicate_satisfied=cnt > 0)
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
    ci = b.get("case_insensitive", mode == "contains")
    norm = (lambda x: str(x).strip().lower()) if ci else (lambda x: str(x).strip())
    tnorm = [norm(t) for t in targets]
    idc = _id_col(header)
    matched, nulls, samples = 0, 0, []
    for i, r in enumerate(rows):
        v = r.get(col, "")
        if _is_null(v):
            nulls += 1
            continue
        nv = norm(v)
        hit = any(t in nv for t in tnorm) if mode == "contains" else (nv in tnorm)
        if hit:
            matched += 1
            if len(samples) < 3:
                samples.append(str(r.get(idc)) if idc else f"row#{i}")
    return {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
            "match": mode, "targets": targets, "matched": matched, "nulls": nulls,
            "samples": samples, "predicate_satisfied": matched >= b.get("require", 1)}


# ---------- D:多条件组合 ----------
def k_combo(b, sources, parent_source):
    results = [evaluate_binding(s, sources, s.get("source", parent_source))
               for s in (b.get("sub") or [])]
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
    exp = str(b.get("equals"))
    return {"class": cls, "status": "ok", "resolved_column": col, "resolve": how, "expected": b.get("equals"),
            "distinct_values": list(dict.fromkeys(vals))[:5], "predicate_satisfied": all(v == exp for v in vals)}


# ---------- H:信令流程 ----------
def k_signaling(table, b, cls="H"):
    header, rows = table
    msgcol, _ = resolve_column(header, b.get("msg_column", "")) if b.get("msg_column") else (None, "")
    if not msgcol:
        return _insuf(cls, "无信令/消息数据源(需 trace 记录、含消息类型列 msg_column)", resolved_column=None)
    key, _ = resolve_column(header, b.get("key", "")) if b.get("key") else (None, "")
    has, lacks = set(b.get("has") or []), set(b.get("lacks") or [])
    groups = defaultdict(set)
    for r in rows:
        groups[str(r.get(key, "")).strip() if key else "_all"].add(str(r.get(msgcol, "")).strip())
    matched = sum(1 for ms in groups.values() if has <= ms and not (lacks & ms))
    return {"class": cls, "status": "ok", "key": key, "msg_column": msgcol, "groups": len(groups),
            "matched_sessions": matched, "predicate_satisfied": matched > 0}


# ---------- I:特征分析 ----------
def k_feature(table, b, cls="I"):
    header, rows = table
    col, how = resolve_column(header, b.get("feature") or b.get("column") or "")
    if not col:
        return _insuf(cls, f"未找到特征列「{b.get('feature') or b.get('column')}」({how})", resolved_column=None)
    metric = b.get("metric", "row_threshold")
    if metric == "row_threshold":
        vals, nulls, n = _numeric_series(rows, col, b.get("transforms"))
        if not vals:
            return _insuf(cls, "特征列无有效数值", resolved_column=col)
        op, thr = b.get("op", "<="), b.get("threshold")
        cnt = sum(1 for v in vals if _cmp(v, op, thr))
        return {"class": cls, "status": "ok", "resolved_column": col, "resolve": how,
                "transforms": b.get("transforms") or [], "op": op, "threshold": thr,
                "valid": len(vals), "nulls": nulls, "count_satisfied": cnt,
                "ratio": round(cnt / len(vals), 4), "predicate_satisfied": cnt > 0}
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


def evaluate_binding(b, sources, source_name):
    """按 b['class'] 分派到对应内核;D 递归,J 人工,无源/未知类别 → 数据不足。"""
    cls = str(b.get("class", "")).upper()
    if cls == "J" or b.get("manual"):
        return k_manual(b)
    if cls == "D":
        return k_combo(b, sources, source_name)
    table = sources.get(source_name)
    if table is None:
        return _insuf(cls or "?", f"无数据源「{source_name}」(用 --data {source_name}=<文件> 提供)",
                      resolved_column=None)
    fn = _KERNELS.get(cls)
    if not fn:
        return _insuf(cls or "?", f"未知条件类别「{cls}」(应为 A–J 之一)")
    return fn(table, b)
