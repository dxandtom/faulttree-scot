#!/usr/bin/env python3
"""闭卷·纠错版(纠错卷):给模型【可能有错】的逐节点判定 + 原始事实,看它会不会用数据复核、纠正错误结论,
而不是盲信标签。模拟"程序算错 / 绑定错"时,模型能否兜底。

注两类错(只改"判定结论",原始事实保持真实):
  · 假阴:把一个真【成立(hold)】的节点标成【不成立(fail)】—— 盲信→真路径被掐断,会漏根因;
  · 假阳:把一个真【不成立(fail)】的节点标成【成立(hold)】—— 盲信→冒出假路径,会误报。
模型应对照每个节点的原始事实复核,以数据为准做诊断,并在 corrections 字段列出纠正了哪些节点。

用法:
  python corrupt_rca.py <rca_input.json>     # → 同目录 rca_input_corrupt.json,返回注入清单
  python corrupt_rca.py --suite <目录>        # 批量(供 v3_run 调用)
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

NOTE = ("纠错卷:下面每个节点都给了一个【判定结论】(outline 里的 hold/fail),但其中可能有错——"
        "可能是上游程序算错或绑定错了。请【别盲信这些结论】,对照每个节点的原始事实自己复核;"
        "发现结论与数据矛盾,以数据为准来做诊断。并在输出里加一个 corrections 字段,列出你纠正的节点:"
        "[{\"node_id\":\"…\",\"given\":\"给定结论\",\"should_be\":\"你判定的正确结论\",\"依据\":\"…\"}]。")


def corrupt(doc: dict):
    ft = doc.get("fault_tree", {}); ol = ft.get("outline") or {}; nodes = ft.get("pruned_nodes") or {}
    pt = lambda n: nodes.get(n, {}).get("pass_through")
    lvl = lambda n: nodes.get(n, {}).get("level", 99)
    holds = sorted([n for n, s in ol.items() if s == "hold" and not pt(n)], key=lvl)            # 越靠根越狠
    fails = sorted([n for n, s in ol.items() if s == "fail" and not pt(n)], key=lambda n: -lvl(n))
    insf  = [n for n, s in ol.items() if s == "insufficient" and not pt(n)]
    recs = []

    def setpred(nid, v):
        for f in nodes.get(nid, {}).get("facts", []):
            if "predicate_satisfied" in f:
                f["predicate_satisfied"] = v

    if holds:                                  # 假阴:hold → fail
        n = holds[0]; ol[n] = "fail"; setpred(n, False)
        recs.append({"node_id": n, "given": "fail", "true": "hold", "kind": "假阴"})
    pos = (fails[0] if fails else (insf[0] if insf else None))
    if pos and pos not in {r["node_id"] for r in recs}:   # 假阳:fail/insuf → hold
        true_v = "fail" if pos in fails else "insufficient"
        ol[pos] = "hold"; setpred(pos, True)
        recs.append({"node_id": pos, "given": "hold", "true": true_v, "kind": "假阳"})

    doc["_mode"] = "corrupt"; doc["_note"] = NOTE
    return doc, recs


def _one(p: Path):
    doc, recs = corrupt(json.loads(p.read_text(encoding="utf-8")))
    p.with_name("rca_input_corrupt.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return recs


def main() -> int:
    ap = argparse.ArgumentParser(description="生成纠错卷(注入错误判定结论)")
    ap.add_argument("path", nargs="?"); ap.add_argument("--suite")
    a = ap.parse_args()
    if a.suite:
        tot = 0
        for p in sorted(Path(a.suite).glob("**/rca_input.json")):
            tot += len(_one(p))
        print(f"注入 {tot} 处错误结论 → 各 rca_input_corrupt.json")
    elif a.path:
        print(json.dumps(_one(Path(a.path)), ensure_ascii=False, indent=2))
    else:
        ap.error("给 <rca_input.json> 或 --suite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
