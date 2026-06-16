#!/usr/bin/env python3
"""多树故障注入(三段可测)—— 在多棵不同大小的真实/合成拓扑上,合成【数据 + 金标准绑定】,
跑【真实内核】算出事实,剪枝成 rca_input。于是同一套场景能测三段:
  · 绑定:模型(树判据+数据列)→ CONDITIONS  vs  金标准(score_binding.py)
  · 执行:数据+绑定 → 事实 → rca_input(这里就是用真实内核 evaluate_binding 算的,自带验证)
  · RCA :rca_input → 根因  vs  truth(score_accuracy.py)

每场景产出:rca_input.json(RCA 题)、binding_question.json(绑定题)、data/<源>.csv(数据)。
答案放 _answers/<场景>.json(truth/node_states/reference_rca/gold_binding,做题时勿读)。

用法: python inject_on_tree.py   (先 make_trees.py 备好 trees/)
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from binding_personas import (PERSONAS, gold_binding, evaluate_binding, _table,  # noqa: E402
                              persona_cols, own_series, shared_series, sessions)

TREES = HERE.parent / "trees"
DEFAULT_OUT = HERE.parent / "suite_real"
# 各类别归到哪个数据源(更像真实:告警进 alarm、信令进 sig…)
SRC_BY_CLASS = {"A": "kpi", "B": "kpi", "C": "chr", "E": "chr", "G": "alarm", "H": "sig"}


def _persona_map(nodes: dict) -> dict:
    ids = sorted(nodes)
    return {nid: PERSONAS[i % len(PERSONAS)] for i, nid in enumerate(ids)}


def path_to_root(nodes, nid):
    p = [nid]
    while nodes[p[-1]].get("parent"):
        p.append(nodes[p[-1]]["parent"])
    return list(reversed(p))


def nearest_break(nodes, decoy, on_path):
    for anc in reversed(path_to_root(nodes, decoy)[:-1]):
        if anc not in on_path and not nodes[anc].get("pass_through"):
            return anc
    return None


def auto_specs(tree):
    nodes, root = tree["nodes"], tree["root"]
    leaves = [n for n in sorted(nodes) if not nodes[n]["children"] and not nodes[n].get("pass_through")]
    if len(leaves) < 3:
        return []

    def l2(n):
        p = n
        while nodes[p].get("parent") and nodes[p]["parent"] != root:
            p = nodes[p]["parent"]
        return p

    deep = sorted(leaves, key=lambda n: (-nodes[n]["level"], n))
    shallow = sorted(leaves, key=lambda n: (nodes[n]["level"], n))

    def decoys(tgt, k):
        b = l2(tgt)
        return [l for l in deep if l2(l) != b and l != tgt][:k]

    def noise(excl, kf, ki):
        pool = [l for l in leaves if l not in excl]
        return pool[:kf], pool[kf:kf + ki]

    specs = []
    t = deep[0]
    dc = decoys(t, 2); nf, ni = noise(set([t]) | set(dc), 2, 2)
    specs.append({"kind": "confirmed_deep", "category": "confirmed", "target": t, "decoys": dc, "noise_fail": nf, "noise_insuf": ni})
    t = shallow[0]
    if t != deep[0]:
        dc = decoys(t, 1); nf, ni = noise(set([t]) | set(dc), 1, 1)
        specs.append({"kind": "confirmed_shallow", "category": "confirmed", "target": t, "decoys": dc, "noise_fail": nf, "noise_insuf": ni})
    t = deep[1] if len(deep) > 1 else deep[0]
    dc = decoys(t, 4); nf, ni = noise(set([t]) | set(dc), 1, 1)
    specs.append({"kind": "fp_heavy", "category": "false_positive_resist", "target": t, "decoys": dc, "noise_fail": nf, "noise_insuf": ni})
    cand = [l for l in deep if nodes[l].get("parent") and not nodes[nodes[l]["parent"]].get("pass_through")]
    if cand:
        t = cand[0]; dc = decoys(t, 1); nf, ni = noise(set([t]) | set(dc), 1, 0)
        specs.append({"kind": "probable", "category": "probable", "target": t, "decoys": dc, "noise_fail": nf, "noise_insuf": ni})
    specs.append({"kind": "honest", "category": "honest_insufficient", "target": None, "decoys": [], "noise_fail": [], "noise_insuf": deep[:6]})
    specs.append({"kind": "excluded", "category": "excluded", "target": None, "decoys": [], "noise_fail": deep[:6], "noise_insuf": []})
    return specs


def _states(spec, tree):
    nodes = tree["nodes"]
    state = {}
    truth = {"root_cause_node_id": None, "root_cause_name": None, "tier": "undetermined", "must_not_report": []}

    def set_path_hold(leaf):
        for p in path_to_root(nodes, leaf):
            if not nodes[p].get("pass_through"):
                state[p] = "hold"

    cat = spec["category"]
    if cat in ("confirmed", "false_positive_resist"):
        tgt = spec["target"]; set_path_hold(tgt); state[tgt] = "hold"
        truth.update(root_cause_node_id=tgt, tier="confirmed")
    elif cat == "probable":
        leaf = spec["target"]; mid = nodes[leaf]["parent"]
        while mid and nodes[mid].get("pass_through"):
            mid = nodes[mid]["parent"]
        set_path_hold(mid); state[leaf] = "insuf"
        truth.update(root_cause_node_id=mid, tier="probable")
    else:
        truth.update(tier=("undetermined" if cat == "honest_insufficient" else "excluded"))

    on_path = {k for k, v in state.items() if v == "hold"}
    for d in spec.get("decoys", []):
        state[d] = "hold"
        brk = nearest_break(nodes, d, on_path)
        if brk:
            state[brk] = "fail"; truth["must_not_report"].append(brk)
        truth["must_not_report"].append(d)
    for d in spec.get("noise_fail", []):
        state.setdefault(d, "fail")
    for d in spec.get("noise_insuf", []):
        state.setdefault(d, "insuf")
    if cat in ("honest_insufficient", "excluded"):
        truth["must_not_report"] = [k for k, v in state.items() if v in ("hold", "insuf")]
    return state, truth


N_ROWS = 40   # 每个列式数据源的行数(让数据看着像真实话统/CHR,不是几行空表)


def build(spec, tree):
    """→ (剪枝后的工作树索引 widx, 各源事实 results, truth, node_states, 各源密集数据行, 金标准绑定)。"""
    nodes = tree["nodes"]
    state, truth = _states(spec, tree)
    # 本场景的事实节点分配【互不相同】的人设(避免同源同列冲突),并补 truth 根因名
    fact_nodes = sorted(state)
    pmap = {nid: PERSONAS[i % len(PERSONAS)] for i, nid in enumerate(fact_nodes)}
    if truth["root_cause_node_id"]:
        truth["root_cause_name"] = pmap[truth["root_cause_node_id"]]["name"]

    widx = copy.deepcopy(tree)
    items_by_src = {}
    for nid in fact_nodes:
        p = pmap[nid]; src = SRC_BY_CLASS.get(p["cls"], "kpi")
        widx["nodes"][nid]["name"] = p["name"]
        widx["nodes"][nid]["condition"] = p["tree_cond"]
        items_by_src.setdefault(src, []).append((nid, p, state[nid], gold_binding(p, nid, p["name"], src)))

    # 造每个源的【密集】数据表:列式源每行填满所有列;信令源是会话日志
    data_by_source = {}
    for src, items in items_by_src.items():
        if any(it[1]["kind"] == "sess" for it in items):
            rows = []
            for j, (nid, p, st, b) in enumerate(items):
                rows += sessions(p, st, (j + 1) * 1000)
            data_by_source[src] = rows
        else:
            colmap = {}
            for (nid, p, st, b) in items:
                if st == "insuf":                       # insuf:数据里就没这列 → 模型/内核该判数据不足
                    continue
                for c in persona_cols(p):
                    colmap.setdefault(c, []).append((p, st))
            cs = {c: (own_series(o[0][0], o[0][1], c, N_ROWS) if len(o) == 1 else shared_series(o, N_ROWS))
                  for c, o in colmap.items()}
            data_by_source[src] = [{c: cs[c][i] for c in cs} for i in range(N_ROWS)] if cs else []

    # 跑真实内核算事实(insuf 直接判数据不足)
    results, gold = {}, []
    for src, items in items_by_src.items():
        tbl = _table(data_by_source.get(src, []))
        for (nid, p, st, b) in items:
            if st == "insuf":
                fact = {"class": p["cls"], "status": "insufficient_data", "resolved_column": None, "reason": p["insuf"]}
                gold.append({**b, "decision": "insufficient_data"})
            else:
                fact = evaluate_binding(b, {src: tbl}, src)
                gold.append(b)
            results.setdefault(src, {"indicator": src, "source": src, "conditions": [], "summary": {}})
            results[src]["conditions"].append({"id": b["id"], "node_id": nid, "node": p["name"],
                                               "condition_text": p["tree_cond"], "class": p["cls"],
                                               "mapping_reason": "合成注入", "fact": fact})
    for r in results.values():
        cc = r["conditions"]
        r["summary"] = {"total": len(cc), "ok": sum(c["fact"]["status"] == "ok" for c in cc),
                        "insufficient_data": sum(c["fact"]["status"] == "insufficient_data" for c in cc), "manual": 0}
    return widx, results, truth, state, data_by_source, gold


def _actual_str(fact: dict) -> str:
    for k in ("value", "matched_sessions"):
        if fact.get(k) is not None:
            return f"{fact.get('resolved_column','')}={fact[k]}" if k == "value" else f"命中会话 {fact[k]} 个"
    if fact.get("top1_ratio") is not None:
        return f"TOP1={fact.get('top1')} 占比 {fact['top1_ratio']}"
    if fact.get("matched") is not None:
        return f"{fact.get('resolved_column','')} 命中 {fact['matched']}/{fact.get('total')}"
    return "满足"


def build_reference(truth, widx, results):
    nodes = widx["nodes"]
    fact_by_node = {c["node_id"]: c["fact"] for r in results.values() for c in r["conditions"]}
    out = {"rca_result": [], "probable_causes": [], "undetermined": [], "tree_issues": {}}
    nid = truth["root_cause_node_id"]

    def ep(path):
        return [{"node": nodes[p]["name"], "node_id": p, "condition": nodes[p].get("condition", ""),
                 "actual": _actual_str(fact_by_node.get(p, {})), "judgment": "成立"}
                for p in path if not nodes[p].get("pass_through")]

    if truth["tier"] == "confirmed":
        path = path_to_root(nodes, nid)
        out["rca_result"] = [{"Root_cause": truth["root_cause_name"],
                              "Fault_tree_path": [nodes[p]["name"] for p in path if not nodes[p].get("pass_through")],
                              "evidence_path": ep(path)}]
    elif truth["tier"] == "probable":
        path = path_to_root(nodes, nid)
        out["probable_causes"] = [{"Root_cause": truth["root_cause_name"], "blocked_at": "叶子因(数据不足)",
                                   "caveats": "叶子因数据不足,已定位到该层、叶子待定", "support": "中间层判据成立",
                                   "evidence_path": ep(path)}]
    else:
        out["undetermined"] = [{"note": "无闭合成立路径,数据不足/条件不成立,未定位到根因"}]
    return out


def _overview(data_by_source: dict) -> dict:
    """各源的列概览(列名 + 几个样例值)——给绑定题的模型看,据此把大白话判据映射到系统列。"""
    ov = {}
    for src, rows in data_by_source.items():
        cols = {}
        for r in rows:
            for k, v in r.items():
                cols.setdefault(k, [])
                if v not in cols[k] and len(cols[k]) < 4 and v not in ("", None):
                    cols[k].append(v)
        ov[src] = {"columns": [{"name": c, "samples": s} for c, s in cols.items()], "n_rows": len(rows)}
    return ov


def _write_csv(path: Path, rows: list):
    fields = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="多树故障注入(数据+绑定+RCA 三段)")
    ap.add_argument("--trees", default=str(TREES))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    sys.path.insert(0, str(HERE.parent.parent / "cs-scot-execute" / "scripts"))
    from tree_prune import prune  # noqa: E402

    out = Path(args.out)
    ans = out / "_answers"
    ans.mkdir(parents=True, exist_ok=True)
    total = 0
    for tf in sorted(Path(args.trees).glob("*.tree_index.json")):
        tree = json.loads(tf.read_text(encoding="utf-8"))
        tag = tf.stem.replace(".tree_index", "")
        print(f"\n■ {tag} · {len(tree['nodes'])} 节点")
        for spec in auto_specs(tree):
            widx, results, truth, state, data_by_source, gold = build(spec, tree)
            pr = prune(widx, results)
            payload = {"fault_tree": {"scenario": pr["scenario"], "root": pr["root"],
                                      "outline": pr["outline"], "pruned_nodes": pr["pruned_nodes"]},
                       "unmapped_facts": pr["unmapped"], "tree_issues": pr["coverage"]}
            name = f"{tag}__{spec['kind']}"
            d = out / name
            (d / "data").mkdir(parents=True, exist_ok=True)
            (d / "rca_input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            for src, rows in data_by_source.items():
                _write_csv(d / "data" / f"{src}.csv", rows)
            # 绑定题:要绑的判据(node+大白话条件)+ 各源列概览;模型据此产 CONDITIONS
            bq = {"scenario": name,
                  "to_bind": [{"node_id": c["node_id"], "node": c["node"], "condition_text": c["condition_text"],
                               "source": r["source"]} for r in results.values() for c in r["conditions"]],
                  "data_overview": _overview(data_by_source)}
            (d / "binding_question.json").write_text(json.dumps(bq, ensure_ascii=False, indent=2), encoding="utf-8")
            (ans / f"{name}.json").write_text(json.dumps(
                {"scenario": name, "tree": tag, "tree_nodes": len(tree["nodes"]), "category": spec["category"],
                 "truth": truth, "node_states": state, "reference_rca": build_reference(truth, widx, results),
                 "gold_binding": gold}, ensure_ascii=False, indent=2), encoding="utf-8")
            total += 1
            print(f"   {spec['kind']:<18} 埋因 {truth['root_cause_node_id'] or '—':<5} "
                  f"剪枝 {pr['coverage']['kept_nodes']}/{pr['coverage']['total_nodes']} "
                  f"绑定题 {len(bq['to_bind'])} 条 · 数据源 {list(data_by_source)}")
    (out / "README.md").write_text(
        "# 冻结考题(多树注入 · 数据+绑定+RCA 三段)\n\n"
        "可信侧预生成、本地勿重生成。每场景:\n"
        "- `rca_input.json`(RCA 题,与生产一致)+ `data/*.csv`(数据)+ `binding_question.json`(绑定题);\n"
        "- `_answers/<场景>.json` 是答案(truth/node_states/reference_rca/gold_binding),**做题时勿读**。\n\n"
        "测 RCA:逐场景模型读 rca_input → 写 rca.json,再 `score_accuracy.py`。\n"
        "测绑定:逐场景模型读 binding_question + data → 写 binding.json(CONDITIONS),再 `score_binding.py`。\n", encoding="utf-8")
    print(f"\n→ {out}:{total} 个场景(RCA + 绑定 两套题,数据已造)")


if __name__ == "__main__":
    main()
