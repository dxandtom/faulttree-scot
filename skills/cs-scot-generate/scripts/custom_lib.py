#!/usr/bin/env python3
"""S 类(代码类)预制函数库——人手调优专用。

绑定 JSON 用 `{"class":"S","source":"<源>","func":"<函数名>","args":{…}}` 引用这里的函数。
每个函数统一签名 `fn(table, args, load)`:
  table = 绑定 source 那个源的表,形态 (header, rows):header=列名列表,rows=一行一个 dict;
  args  = 绑定 JSON 里的 args 原样传入(dict);
  load  = 函数:load("config") 取其他源的表(同样 (header, rows)),该源没喂返回 None。
返回与内核同形的事实(二选一,内核 wrapper 会校验形状、兜异常):
  {"status": "ok", "predicate_satisfied": True/False, …证据字段…, "explain": "一句人话"}
  {"status": "insufficient_data", "reason": "缺什么"}
新函数照下面的样子写 + @register("名字") 挂进注册表;别 print、别写文件、别自己读 excel。
"""
from __future__ import annotations

REGISTRY = {}


def register(name):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def pick_values(table, spec):
    """工具:按取值子句 {filter?, column, extract?} 从表抽出值列表(复用内核的 filter/extract 语义;
    抽不到返回 [])。spec 与绑定手册第二节的取值子句同形。"""
    import condition_kernels as _k
    if not table:
        return []                                        # 没声明 source(table=None)/该源没喂:抽不到
    header, rows = table
    flt = spec.get("filter")
    if flt:
        t, err = _k._apply_filter((header, rows), flt)
        if err:
            return []
        header, rows = t
    col, _how = _k.resolve_column(header, spec.get("column", ""))
    if not col:
        return []
    out = []
    for r in rows:
        v = r.get(col, "")
        if _k._is_null(v):
            continue
        ex = spec.get("extract")
        if ex:
            v = _k._extract_val(v, ex)
            if v is None:
                continue
        v = str(v).strip()
        if v:
            out.append(v)
    return out


@register("sum_extracts_vs_threshold")
def sum_extracts_vs_threshold(table, args, load):
    """两处各抽一个数,相加比阈值(DSL 无加法时的正牌解,如 BGP+ASBR 路由数)。
    args: {"a": 取值子句, "b": 取值子句, "op": ">="(< <= > >= == 之一), "threshold": 数}"""
    nums = []
    for key in ("a", "b"):
        spec = args.get(key)
        if not isinstance(spec, dict):
            return {"status": "insufficient_data", "reason": f"args 缺取值子句「{key}」"}
        vals = pick_values(table, spec)
        if not vals:
            return {"status": "insufficient_data",
                    "reason": f"args[{key}] 一个值也没抽到(filter/extract 与数据对不上,或对应命令未采集)"}
        try:
            nums.append(float(vals[0].replace(",", "")))
        except ValueError:
            return {"status": "insufficient_data",
                    "reason": f"args[{key}] 抽到的不是数:「{vals[0][:40]}」"}
    op = str(args.get("op", ">="))
    try:
        thr = float(args["threshold"])
    except (KeyError, TypeError, ValueError):
        return {"status": "insufficient_data", "reason": "args.threshold 缺失或不是数"}
    cmp = {"<": nums[0] + nums[1] < thr, "<=": nums[0] + nums[1] <= thr,
           ">": nums[0] + nums[1] > thr, ">=": nums[0] + nums[1] >= thr,
           "==": nums[0] + nums[1] == thr}
    if op not in cmp:
        return {"status": "insufficient_data", "reason": f"op「{op}」不认识(只认 < <= > >= ==)"}
    total, ok = nums[0] + nums[1], cmp[op]
    return {"status": "ok", "predicate_satisfied": ok,
            "value": total, "op": op, "threshold": thr, "parts": nums,
            "explain": f"{nums[0]:g}+{nums[1]:g}={total:g} {op} {thr:g} → {'成立' if ok else '不成立'}"}


@register("latest_by_seq_then_match")
def latest_by_seq_then_match(table, args, load):
    """先按 seq 列取"最新一轮采集"的行,再在目标列里找特征串("取最新一次再判"的口径,DSL 表达不了)。
    args: {"filter": 选行(可选,同 filter 子句), "seq_column": "CmdSeq",
           "column": "DataValue", "contains": "Down"}"""
    import condition_kernels as _k
    if not table:
        return {"status": "insufficient_data",
                "reason": "本函数要主表:绑定里声明 source=<要判的源>(或该源本轮没喂数据)"}
    header, rows = table
    flt = args.get("filter")
    if flt:
        t, err = _k._apply_filter((header, rows), flt)
        if err:
            return {"status": "insufficient_data", "reason": f"filter 失败:{err}"}
        header, rows = t
    if not rows:
        return {"status": "insufficient_data", "reason": "filter 后 0 行(该指标项本次未采集)"}
    scol, _ = _k.resolve_column(header, str(args.get("seq_column", "CmdSeq")))
    vcol, _ = _k.resolve_column(header, str(args.get("column", "")))
    if not scol or not vcol:
        return {"status": "insufficient_data",
                "reason": f"找不到列(seq_column={args.get('seq_column')}/column={args.get('column')})"}

    def _seq_key(r):
        s = str(r.get(scol, "")).strip()
        try:
            return (1, float(s))
        except ValueError:
            return (0, s)
    latest_seq = max((str(r.get(scol, "")).strip() for r in rows), key=lambda s: _seq_key({scol: s}))
    latest = [r for r in rows if str(r.get(scol, "")).strip() == latest_seq]
    target = str(args.get("contains", ""))
    if not target:
        return {"status": "insufficient_data", "reason": "args.contains 为空(要找的特征串)"}
    bad = [r for r in latest if target in str(r.get(vcol, ""))]
    return {"status": "ok", "predicate_satisfied": bool(bad),
            "matched": len(bad), "total": len(latest), "seq": latest_seq,
            "hits": [str(r.get(vcol, ""))[:80] for r in bad[:3]],
            "explain": f"最新一轮采集({scol}={latest_seq})共 {len(latest)} 行,"
                       f"{vcol} 含「{target}」的 {len(bad)} 行 → {'成立' if bad else '不成立'}"}
