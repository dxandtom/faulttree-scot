#!/usr/bin/env python3
"""v2 地基:把合成树拓扑写成【真实 excel_to_tree 能解析的故障树 CSV】+ 金标准答案(按 code 索引)。

为什么:v1 评测自己算 rca_input(虽复用真实内核,但绕过了 generate/execute 技能壳)。v2 要让
**真 generate/execute** 端到端跑,再评测它们的输出 → 零重写 gap。本脚本产 v2 的"输入 + 答案":
  · `<树>.fault_tree.csv` —— 真实导出格式(Ln(Chinese)/Ln Code/Judgment Condition(Chinese)),
    条件用大白话(语义鸿沟),喂给真 excel_to_tree 会还原出同一棵树;
  · `<树>.gold_tree.json` —— 每个节点(按 **code** 这个 join key 索引)的 金标准绑定 + 源 + 人设,
    供后续把"真 excel_to_tree 产出的新 id"映射回答案。

固定:一棵树 = 一份 Excel + 一份固定条件/绑定(场景靠"数据"变,不靠改条件)——更贴近生产。
用法: python tree_to_excel.py [--trees ../trees] [--out ../suite_v2]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from binding_personas import PERSONAS, gold_binding  # noqa: E402

TREES = HERE.parent / "trees"
DEFAULT_OUT = HERE.parent / "suite_v2"
SRC_BY_CLASS = {"A": "kpi", "B": "kpi", "C": "chr", "E": "chr", "G": "alarm", "H": "sig"}


def assign(nodes: dict, root: str) -> dict:
    """给每个【非根、非 pass_through】节点固定挂一个人设(按 id 顺序),得到固定条件/绑定。"""
    out = {}
    i = 0
    for nid in sorted(nodes):
        if nid == root or nodes[nid].get("pass_through"):
            continue
        out[nid] = PERSONAS[i % len(PERSONAS)]
        i += 1
    return out


def _path(nodes, nid):
    p = [nid]
    while nodes[p[-1]].get("parent"):
        p.append(nodes[p[-1]]["parent"])
    return list(reversed(p))


def to_excel_rows(tree: dict, persona_of: dict, codes: dict):
    """叶子行铺全拓扑 + 中间带条件节点行(把条件挂到该节点为最深的那一行)。"""
    nodes = tree["nodes"]
    rows = []

    def mkrow(path_ids, judg):
        r = {}
        for nid in path_ids:
            lvl = nodes[nid]["level"]
            r[f"L{lvl}(Chinese)"] = nodes[nid]["name"]
            r[f"L{lvl} Code"] = codes[nid]
        if judg and persona_of.get(judg):
            r["Judgment Condition(Chinese)"] = persona_of[judg]["tree_cond"]
        return r

    for nid in nodes:
        if not nodes[nid]["children"]:
            rows.append(mkrow(_path(nodes, nid), nid if nid in persona_of else None))
    for nid in nodes:
        if nodes[nid]["children"] and nid in persona_of:
            rows.append(mkrow(_path(nodes, nid), nid))
    return rows


def emit(tree: dict, tag: str, out: Path):
    nodes, root = tree["nodes"], tree["root"]
    persona_of = assign(nodes, root)
    codes = {nid: f"FT-{tag}-{i:04d}" for i, nid in enumerate(sorted(nodes))}
    rows = to_excel_rows(tree, persona_of, codes)
    fields = sorted({k for r in rows for k in r})
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"{tag}.fault_tree.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    gold = {"scenario_root": nodes[root]["name"], "tree": tag, "by_code": {}}
    for nid, p in persona_of.items():
        src = SRC_BY_CLASS.get(p["cls"], "kpi")
        b = gold_binding(p, "<filled-after-parse>", p["name"], src)
        b.pop("node_id", None)
        gold["by_code"][codes[nid]] = {"name": p["name"], "source": src, "persona": p["name"], "binding": b}
    (out / f"{tag}.gold_tree.json").write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    return persona_of, codes


def roundtrip_check(tree: dict, tag: str, out: Path) -> tuple:
    """跑真 excel_to_tree 解析回来,核对节点数/父子/条件全一致(可行性自检)。"""
    sys.path.insert(0, str(HERE.parent.parent / "cs-scot-generate" / "scripts"))
    import excel_to_tree as e2t
    nodes, root = tree["nodes"], tree["root"]
    persona_of = assign(nodes, root)
    headers, rrows = e2t._read_table(str(out / f"{tag}.fault_tree.csv"))
    idx = e2t.build_index(rrows, e2t.find_level_columns(headers), e2t.find_code_columns(headers),
                          2, nodes[root]["name"], e2t.find_judgment_column(headers), "")
    rn = idx["nodes"]
    oe = {nodes[c]["name"]: (nodes[nodes[c]["parent"]]["name"] if nodes[c]["parent"] else None) for c in nodes}
    re_ = {rn[c]["name"]: (rn[rn[c]["parent"]]["name"] if rn[c]["parent"] else None) for c in rn}
    oc = {p["tree_cond"] for p in persona_of.values()}
    rc = {rn[c]["condition"] for c in rn if rn[c].get("condition")}
    return len(nodes) == len(rn), oe == re_, oc <= rc


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="合成树 → 真实故障树 Excel(CSV)+ 金标准")
    ap.add_argument("--trees", default=str(TREES))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--check", action="store_true", help="跑真 excel_to_tree 解析回来自检一致性")
    args = ap.parse_args()
    out = Path(args.out)
    for tf in sorted(Path(args.trees).glob("*.tree_index.json")):
        tree = json.loads(tf.read_text(encoding="utf-8"))
        tag = tf.stem.replace(".tree_index", "")
        emit(tree, tag, out)
        msg = ""
        if args.check:
            n, e, c = roundtrip_check(tree, tag, out)
            msg = f"  自检: 节点{'✓' if n else '✗'} 父子{'✓' if e else '✗'} 条件{'✓' if c else '✗'}"
        print(f"  {tag:<24} {len(tree['nodes']):>4} 节点 → {tag}.fault_tree.csv + .gold_tree.json{msg}")
    print(f"\n→ {out}(v2 输入:真故障树 Excel + 金标准;下一步喂真 generate/execute 端到端)")


if __name__ == "__main__":
    main()
