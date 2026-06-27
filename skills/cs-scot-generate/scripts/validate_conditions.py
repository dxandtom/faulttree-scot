#!/usr/bin/env python3
"""逐条件事实自检(机械兜底):在数据进 RCA 之前,先揪"静默编造"。

不依赖运行模型的自觉,直接扫 condition_facts.json:
1. **无中生有**:列没解析到(resolved_column 为空)或 status=insufficient_data,却给了 predicate_satisfied。
2. **缺理由**:status=insufficient_data 但没写 reason(说不清为啥缺,等于黑箱)。
3. **越权判定**:predicate_satisfied 出现了非布尔(本层只做字面比较,True/False/缺省三态)。
4. **类别缺失**:condition 没有 class,或 J 类却不是 manual。

用法:python validate_conditions.py <condition_facts.json>   # 不合格 → 退出码 1
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ---- 代理"必要条件"自检:只逮"任何语义下都错"的三类;绝不判"语义是否相等"(那是模型的活、写在 mapping_reason 可审) ----
_PROC_ALIAS = {"uecntx": "uecontext", "uecontext": "uecontext", "rrc": "rrc",
               "ngsig": "ngsig", "ngsignaling": "ngsig", "qosflow": "qosflow",
               "pdusession": "pdusession", "paging": "paging"}
_KNOWN_PROCS = ("uecontext", "rrc", "ngsig", "qosflow", "pdusession", "paging")


def _counter_proc(text):
    """条件正文里 N.<Proc>. / L.<Proc>. 的流程段 → 规范名(认得才返回,认不得返回 None,绝不误伤)。"""
    for m in re.finditer(r"\b[NL]\.([A-Za-z]+)\.", text or ""):
        p = _PROC_ALIAS.get(re.sub(r"[^a-z0-9]", "", m.group(1).lower()))
        if p:
            return p
    return None


def _col_proc(col):
    """列名里出现的已知流程关键词 → 规范名(认不得返回 None)。"""
    norm = re.sub(r"[^a-z0-9]", "", (col or "").lower())
    return next((p for p in _KNOWN_PROCS if p in norm), None)


def _is_rate_col(col):
    return bool(col) and ("rate" in col.lower() or "率" in col)


def _is_subcause(text):
    """计数器含 .FailEst.<原因> / .Fail<原因> = 特定失败子因。"""
    return bool(re.search(r"\.Fail(Est)?\.[A-Za-z]", text or ""))


def _is_setup_cond(text):
    return bool(text and (re.search(r"\.Est\.|\.ConnEst\.|Setup", text) or "建立" in text))


def _is_drop_col(col):
    c = (col or "").lower()
    return "drop" in c or "abnorm" in c or "release" in c or "掉线" in (col or "")


def _coverage_warnings(conditions):
    """覆盖度对账(泛化、零写死):对每个 C/G 类绑定用到的分类列,把"数据里出现过、却没被任何绑定
    `targets` 认领"的取值(+记录数)列出来。只看"这次绑了哪些列、列里有哪些值、哪些没被认领"——
    不认识任何具体列名/取值,换树换场景逻辑不变。用来逮"一整支故障没人绑、记录被悄悄漏诊"。
    数据来自事实本身:C/G 事实带 `targets`(认领集) + `top`(同列取值分布,Counter.most_common)。"""
    by_col = {}
    for c in conditions:
        f = c.get("fact") or {}
        if f.get("status") != "ok":
            continue
        if str(c.get("class") or f.get("class") or "").upper() not in ("C", "G"):
            continue
        col = f.get("resolved_column")
        if not col or f.get("targets") is None:
            continue
        ent = by_col.setdefault(col, {"binds": [], "dist": {}, "total": 0})
        ent["binds"].append((f.get("match", "contains"),
                             [str(t).strip().lower() for t in (f.get("targets") or [])]))
        ent["total"] = max(ent["total"], f.get("total") or 0)
        for val, cnt in (f.get("top") or []):              # top = [[值, 计数], ...]
            ent["dist"][str(val)] = max(ent["dist"].get(str(val), 0), cnt)
    warns = []
    for col, ent in by_col.items():
        tot = ent["total"] or sum(ent["dist"].values())
        uncovered = []
        for val, cnt in sorted(ent["dist"].items(), key=lambda kv: -kv[1]):
            v = val.strip().lower()
            if tot and cnt >= 0.5 * tot:     # 占比畸高的取值多半是该列"正常/健康"值(如 Normal)→ 跳过免噪
                continue
            covered = any((any(t in v for t in tgts) if mode == "contains" else v in tgts)
                          for mode, tgts in ent["binds"])
            if not covered and v:
                uncovered.append((val, cnt))
        if uncovered:
            warns.append((col, sum(cnt for _, cnt in uncovered),
                          "、".join(f"{val}(×{cnt})" for val, cnt in uncovered[:8])))
    return warns


def _proxy_warnings(conditions):
    """代理三 signal(软告警、泛化、零写死),全部从事实本身算:
    (a) 恒成立:某 ok 绑定命中占比 ratio≥0.9 → 在正常样本上也几乎恒为真、不分故障(如某值占满整列);
    (b) 一值多挂:同一(列,取值集)被挂到 ≥2 个不同 node_id → 一把尺子量多个节点(也含'本源分不开这些子因');
    (c) 共用阈值:多条**数值**绑定共用同一(列,op,阈值)≥5 → 把多判据塞给同一通用阈值。
    关键:多条 C 类绑**不同取值**到同一列是**正常**的(逐码区分故障),绝不该报——所以 (c) 只数**有真实 op/阈值**的数值绑定,
    不再像旧版那样按 (列,op,阈值) 把所有 C 类(op/阈值都是 None)算成'共用一列'而误伤。"""
    from collections import defaultdict
    heng, by_val, numkey = [], defaultdict(set), defaultdict(int)
    for c in conditions:
        f = c.get("fact") or {}
        if f.get("status") != "ok":
            continue
        col, nid = f.get("resolved_column"), c.get("node_id")
        cid = c.get("id") or nid or c.get("node") or "?"
        ratio = f.get("ratio")
        if ratio is None and f.get("total"):
            ratio = (f.get("matched") or 0) / f["total"]
        hit = f.get("matched") or f.get("count_satisfied") or 0
        if ratio is not None and ratio >= 0.9 and hit:          # (a) 恒成立
            heng.append((cid, nid, col, round(ratio, 3), hit, f.get("total")))
        tgts = tuple(sorted(str(t).strip().lower() for t in (f.get("targets") or [])))
        if col and nid and tgts:                                # (b) 一值多挂:按(列,取值集)聚 node_id
            by_val[(col, tgts)].add(str(nid))
        if col and f.get("op") is not None and f.get("threshold") is not None:  # (c) 只数真有阈值的数值绑定
            numkey[(col, f.get("op"), f.get("threshold"))] += 1
    multi = [(col, list(tgts), sorted(nids)) for (col, tgts), nids in by_val.items() if len(nids) >= 2]
    num = [(k, n) for k, n in numkey.items() if n >= 5]
    return heng, multi, num


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("用法:python validate_conditions.py <condition_facts.json>", file=sys.stderr)
        return 2
    try:
        doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"无法读取/解析: {e}", file=sys.stderr)
        return 2

    problems = []
    for c in doc.get("conditions", []):
        cid = c.get("id") or c.get("node") or c.get("condition_text", "?")
        f = c.get("fact") or {}
        st = f.get("status")
        pred = f.get("predicate_satisfied")
        cls = c.get("class") or f.get("class")

        if not cls:
            problems.append(f"[类别缺失]「{cid}」没有 class(应为 A–J)")
        if str(cls).upper() == "J" and st != "manual":
            problems.append(f"[J类越权]「{cid}」J 类应 status=manual,实际 {st!r}")

        # 数据不足 / 列没解析到,却给了判断 → 编造
        if st == "insufficient_data" and pred is not None:
            problems.append(f"[无中生有]「{cid}」数据不足却给了 predicate_satisfied={pred!r}")
        # 各内核"解析到了什么"的痕迹字段(A/F/I:resolved_column;B:resolved_columns;D:sub_facts;
        # H:msg_column;E:group_by)。一个都没有却给了判断,才算无中生有。
        _prov = ("resolved_column", "resolved_columns", "sub_facts", "msg_column", "group_by")
        if st == "ok" and pred is not None and not any(f.get(k) is not None for k in _prov):
            problems.append(f"[无中生有]「{cid}」没解析到任何列/痕迹却给了 predicate_satisfied={pred!r}")
        if st == "insufficient_data" and not f.get("reason"):
            problems.append(f"[缺理由]「{cid}」数据不足但没写 reason")
        if pred is not None and not isinstance(pred, bool):
            problems.append(f"[越权判定]「{cid}」predicate_satisfied 应为布尔,实际 {pred!r}")

        # 代理三类必要条件(只逮"任何语义下都错"的):流程不匹配 / 建立配掉线 / 子因配总率
        ct, col = c.get("condition_text") or "", f.get("resolved_column")
        if st == "ok" and _is_rate_col(col):
            cp, lp = _counter_proc(ct), _col_proc(col)
            if cp and lp and cp != lp:
                problems.append(f"[流程不匹配]「{cid}」条件计数器流程={cp},却绑到 {lp} 流程的率列「{col}」——不同流程不能互代,改数据不足或换源")
            if _is_setup_cond(ct) and _is_drop_col(col):
                problems.append(f"[建立配掉线]「{cid}」建立类条件绑到掉线/异常释放率列「{col}」——建立≠掉线,数据不足")
            if _is_subcause(ct):
                problems.append(f"[子因配总率]「{cid}」特定失败子因(.FailEst.)绑到总率列「{col}」——总率分不出子因,改 C 类查错误码字段或数据不足")

        # 丢了区分量:condition_text 提到 RSRP/SINR 等无线量,这条却是单一 A/C 类、绑到非无线列、无 D 类组合
        #   → 多半把"X 且 弱覆盖/干扰"绑成了裸 X(丢区分条件、会恒成立、几个不同节点一起命中)。
        if st == "ok" and col and str(cls).upper() in ("A", "C") and not c.get("sub"):
            _radio = ("rsrp", "sinr", "rsrq", "rssi")
            if any(k in ct.lower() for k in _radio) and not any(k in col.lower() for k in _radio):
                problems.append(f"[丢了区分量]「{cid}」条件正文提到 RSRP/SINR 等无线量,绑定却只查了「{col}」——多半把'X 且 弱覆盖/干扰'绑成裸 X(丢区分条件、恒成立)。改 D 类组合(连无线量一起绑)或 decision:insufficient_data")

    # 代理软告警(不致命):恒成立 / 一值多挂 / 共用数值阈值——从事实算,零写死(修正旧版按列误伤多取值 C 类)
    heng_w, multi_w, num_w = _proxy_warnings(doc.get("conditions", []))

    # 覆盖度对账(软告警,始终显示):分类列里出现却没被任何绑定认领的取值 → 多半有一整支故障没人绑、记录被漏诊
    for col, tot, lst in _coverage_warnings(doc.get("conditions", [])):
        print(f"⚠ 覆盖度:列「{col}」有 {tot} 条记录的取值未被任何绑定覆盖:{lst}")
        print("   → 确认这些取值是否该各自路由到某个节点(或归入树里'非典型场景/其他'兜底节点),别让记录悄悄漏诊。")

    if problems:
        print(f"不合格:发现 {len(problems)} 处问题:")
        for p in problems:
            print("  - " + p)
        print("\n修法:列解析不到/数据不足就只给 status+reason,绝不给 predicate_satisfied;"
              "J 类用 manual;成立/不成立的最终裁决留给 fault-tree-rca。")
        return 1

    for cid, nid, col, ratio, hit, tot in heng_w:
        print(f"⚠ 代理(恒成立):「{cid}」(node {nid}) 在「{col}」命中 {hit}/{tot}≈{ratio},几乎全体为真——"
              "正常样本上也成立的值分不出故障,核实或改 decision:insufficient_data。")
    for col, tgts, nids in multi_w:
        print(f"⚠ 代理(一值多挂):同一「列={col} 取值={'/'.join(tgts[:3])}」被挂到 {len(nids)} 个节点 {nids}——"
              "一把尺子量多个节点(恒成立+丢区分,或本源分不开这些子因)。最多留一个,其余换专属判据或 decision:insufficient_data。")
    for (col, op, thr), n in num_w:
        print(f"⚠ 代理(共用阈值):{n} 条数值绑定共用「列={col} {op} {thr}」——多判据塞同一通用阈值。对不上专属量的应 decision:insufficient_data。")
    s = doc.get("summary", {})
    print(f"通过:{s.get('total','?')} 条事实无静默编造(ok={s.get('ok')} "
          f"数据不足={s.get('insufficient_data')} 人工={s.get('manual')})。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
