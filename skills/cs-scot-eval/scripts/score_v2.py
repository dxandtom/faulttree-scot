#!/usr/bin/env python3
"""v2 打分 —— 对【真 generate/execute 跑出的产物】同时评【绑定】与【RCA】两段。

v2 = 评测 skill 调用真 excel_to_tree+assemble+runner(见 v2_run.py),再评它们的输出:
  · 绑定:模型 binding.json(CONDITIONS) vs 金标准 gold_tree.json —— 复用 score_binding 的判据;
  · RCA :逐场景 模型 rca.json vs truth.json —— 复用 score_accuracy 的判据(命中/档位/无假阳/诚实)。
join key 是 **code**:gold_tree / truth 都按 code 索引,真 excel_to_tree 重分配的 id(n1,n2…)用
产物里重解析出的 tree_index 的 code→id 映射对回来。多根因(truth 里 roots 是列表)逐根判。

用法:
  python score_v2.py [--suite ../suite_v2] [--use-reference]
  --use-reference:绑定用金标准、RCA 用"由 truth 构造的参考答案"自测 —— 验证打分链路本身(应 100%)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from score_accuracy import _entry_ids, _covers, _judged_hold_ids        # noqa: E402
from score_binding import _cols, _cls, _is_insuf, _params, _norm, _model_list  # noqa: E402

DEFAULT_SUITE = HERE.parent / "suite_v2"


def _code2id(tidx: dict) -> dict:
    return {n["code"]: nid for nid, n in tidx["nodes"].items() if n.get("code")}


def _path_ids(nodes: dict, nid: str) -> list:
    p = [nid]
    while nodes[p[-1]].get("parent"):
        p.append(nodes[p[-1]]["parent"])
    return list(reversed(p))


# ---------------- 绑定打分(模型 CONDITIONS vs 金标准) ----------------
def score_binding(model_list: list, gold_list: list) -> dict:
    mby = {str(m.get("node_id")): m for m in model_list if isinstance(m, dict)}
    n = cls = col = par = full = 0
    misses = []
    for g in gold_list:
        nid = str(g.get("node_id"))
        n += 1
        m = mby.get(nid)
        if not m:
            misses.append(f"{nid}(漏绑)")
            continue
        c_ok = _cls(m) == _cls(g) and bool(_cls(g))
        l_ok = _cols(m) == _cols(g) and bool(_cols(g))
        p_ok = _params(m) >= _params(g)
        cls += c_ok; col += l_ok; par += p_ok; full += (c_ok and l_ok and p_ok)
        if not (c_ok and l_ok and p_ok):
            why = [x for x, ok in (("类别", c_ok), ("选列", l_ok), ("参数", p_ok)) if not ok]
            misses.append(f"{nid}({'/'.join(why)}错;选列{sorted(_cols(m))}vs{sorted(_cols(g))})")
    return {"n": n, "cls": cls, "col": col, "par": par, "full": full,
            "ok": full == n and n > 0, "misses": misses}


# ---------------- RCA 打分(逐场景,多根因感知) ----------------
def _seclist(rca: dict, *keys) -> list:
    """取某一档的记录列表,认契约 key 及常见同义写法(模型把 rca_result 写成 root_cause_nodes
    不该把"推理对了"判成空)。返回第一个是 list 的 key 值。"""
    if not isinstance(rca, dict):
        return []
    for k in keys:
        v = rca.get(k)
        if isinstance(v, list):
            return v
    return []


def build_ref_rca(roots: set, tier: str, id_states: dict, nodes: dict) -> dict:
    """由 truth 构造"满分参考答案":确证→逐根因列成立路径;疑似→列疑似;排除/不足→不报确证。
    evidence_path 只放 hold 节点(诚实:不在 fail/insuf 节点上写"成立")。"""
    conf, prob, undet = [], [], []

    def ep(path):
        return [{"node": nodes[p]["name"], "node_id": p, "judgment": "成立"}
                for p in path if id_states.get(p) == "hold"]
    if tier == "confirmed":
        for r in roots:
            path = _path_ids(nodes, r)
            conf.append({"Root_cause": nodes[r]["name"],
                         "Fault_tree_path": [nodes[p]["name"] for p in path if not nodes[p].get("pass_through")],
                         "evidence_path": ep(path)})
    elif tier == "probable":
        for r in roots:
            path = _path_ids(nodes, r)
            prob.append({"Root_cause": nodes[r]["name"], "blocked_at": "叶子因数据不足",
                         "caveats": "中间层成立、叶子待定", "evidence_path": ep(path)})
    else:
        undet = [{"note": "无闭合成立路径,数据不足/条件不成立,未定位到根因"}]
    return {"rca_result": conf, "probable_causes": prob, "undetermined": undet}


def score_rca(rca: dict, roots: set, mnr: set, tier: str, id_states: dict, nodes: dict) -> dict:
    conf = _seclist(rca, "rca_result", "root_cause_nodes", "root_causes", "confirmed",
                    "confirmed_root_causes", "results", "roots")
    prob = _seclist(rca, "probable_causes", "probable", "suspected_causes", "suspected")
    reported = set()
    for e in conf + prob:
        reported |= _entry_ids(e)[0]
    fp = bool(mnr & reported)

    def covered(entries, rid):
        return any(_covers(e, rid, nodes[rid]["name"]) for e in entries)
    if tier == "confirmed":
        hit = bool(roots) and all(covered(conf, r) for r in roots)
        tier_ok = hit
        no_fp = not fp
    elif tier == "probable":
        hit = bool(roots) and all(covered(prob, r) for r in roots)
        tier_ok = hit and not any(covered(conf, r) for r in roots)
        no_fp = not fp
    else:                                                  # excluded / undetermined
        hit = tier_ok = (len(conf) == 0)
        no_fp = (len(conf) == 0) and not fp
    planted_hold = {nid for nid, s in id_states.items() if s == "hold"}
    # 诚实只查"在带条件节点上伪造成立":pass_through 节点无条件,模型把它标"成立"(如根节点路过)
    # 是无害的结构标注、不是伪造证据 → 从判定集里剔除,免得误伤诚实档。真 fail/insuf 节点判成立仍被抓。
    judged = {n for n in _judged_hold_ids(conf, prob) if not nodes.get(n, {}).get("pass_through")}
    honest = judged <= planted_hold
    why = [m for m, ok in (("没命中应在档位", hit), ("档位错", tier_ok),
                           ("假阳性", no_fp), ("诚实未过", honest)) if not ok]
    return {"hit": hit, "tier": tier_ok, "no_fp": no_fp, "honest": honest,
            "ok": hit and tier_ok and no_fp and honest, "why": "; ".join(why) or "全过"}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="v2 打分(绑定 + RCA,对真 generate/execute 产物)")
    ap.add_argument("--suite", default=str(DEFAULT_SUITE))
    ap.add_argument("--use-reference", action="store_true", help="金标准/参考答案自测打分链路(应 100%)")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    suite = Path(args.suite)
    tidx_files = list((suite / "scot产物").glob("*/tree/*.tree_index.json"))
    if not tidx_files:
        print("缺 scot产物/<场景>/tree/*.tree_index.json —— 先跑 v2_run.py(真 excel_to_tree+assemble+runner)", file=sys.stderr)
        return 2
    tidx = json.loads(tidx_files[0].read_text(encoding="utf-8"))
    nodes = tidx["nodes"]
    c2i = _code2id(tidx)
    gold = json.loads((suite / "gold_tree.json").read_text(encoding="utf-8"))
    gold_list = []
    for code, g in gold["by_code"].items():
        b = dict(g["binding"]); b["node_id"] = c2i[code]; b["node"] = g["name"]
        gold_list.append(b)

    L = ["=" * 86, "v2 评测(真 excel_to_tree+assemble+runner 端到端 → 评绑定 + RCA)"
         + ("· 金标准/参考自测" if args.use_reference else ""), "=" * 86]

    # —— 绑定 ——
    if args.use_reference:
        model_b = gold_list
    else:
        bf = suite / "binding.json"
        model_b = _model_list(json.loads(bf.read_text(encoding="utf-8"))) if bf.exists() else None
    L.append("\n【绑定阶段】大白话树判据 → 系统列(模型 CONDITIONS vs 金标准)")
    if model_b is None:
        L.append("  —— 待模型产出 binding.json(读 binding_question.json + scot产物/<场景>/tree/*.md)——")
    else:
        b = score_binding(model_b, gold_list)
        L.append(f"  判据 {b['n']} 条:类别 {b['cls']}/{b['n']}、选列 {b['col']}/{b['n']}、参数 {b['par']}/{b['n']}、"
                 f"完全正确 {b['full']}/{b['n']} = {100*b['full']//max(b['n'],1)}%")
        if b["misses"]:
            L.append("  未过:" + "; ".join(b["misses"][:6]))

    # —— RCA(逐场景)——
    L.append("\n【RCA 阶段】rca_input(真 runner 用金标准绑定产出)→ 根因  vs  truth")
    L.append(f"{'场景':<30}{'类别':<22}{'命中':<5}{'档位':<5}{'无假阳':<7}{'诚实':<5}{'综合'}")
    M = lambda x: " ✓  " if x else " ✗  "
    scns = sorted(d for d in (suite / "scenarios").iterdir() if d.is_dir())
    agg = {k: 0 for k in ("hit", "tier", "no_fp", "honest", "ok")}
    scored = pend = 0
    for d in scns:
        truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
        disp = f"{d.name} {truth.get('desc', '')}".strip()   # 目录名不透明,描述从 truth(模型读不到)取来给人看
        roots = {c2i[c] for c in truth.get("root_cause_codes", [])}
        mnr = {c2i[c] for c in truth.get("must_not_report_codes", [])}
        id_states = {c2i[c]: s for c, s in (truth.get("node_states") or {}).items() if c in c2i}
        tier = truth["tier"]
        if args.use_reference:
            rca = build_ref_rca(roots, tier, id_states, nodes)
        else:
            rf = d / "rca.json"
            if not rf.exists():
                L.append(f"{disp:<30}{truth.get('category',''):<22}—— 待模型产出 rca.json ——")
                pend += 1
                continue
            rca = json.loads(rf.read_text(encoding="utf-8"))
        r = score_rca(rca, roots, mnr, tier, id_states, nodes)
        scored += 1
        for k in agg:
            agg[k] += r[k]
        L.append(f"{disp:<30}{truth.get('category',''):<22}{M(r['hit'])}{M(r['tier'])}{M(r['no_fp'])+'  '}"
                 f"{M(r['honest'])}{'✓ 通过' if r['ok'] else '✗ ' + r['why']}")
    L.append("-" * 86)
    if scored:
        L.append(f"RCA 合计({scored} 场景): 命中 {agg['hit']}/{scored}、档位 {agg['tier']}/{scored}、"
                 f"无假阳 {agg['no_fp']}/{scored}、诚实 {agg['honest']}/{scored}")
        L.append(f"RCA 综合准确率(四项全过): {agg['ok']}/{scored} = {100*agg['ok']//scored}%")
    if pend:
        L.append(f"待评分 {pend} 个(先让模型逐场景产出 rca.json)")
    L.append("注:RCA 用金标准绑定隔离绑定误差,单测大树推理(判条件/导航/抗诱饵/诚实);绑定阶段单独评。受控合成,必要不充分。")
    L.append("=" * 86)
    report = "\n".join(L)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
        print(f"\n[score_v2] → {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
