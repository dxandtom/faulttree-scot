#!/usr/bin/env python3
"""把 rca_input.json 变成【hard 模式】输入:删掉内核替模型算好的"答案标签",只留原始证据,
逼模型【自己判每个节点 hold/fail/insufficient】再导航 —— 这才是真 RCA(能失败、能区分强弱)。

删掉:
  · fault_tree.outline            —— 每节点 hold/fail/insufficient/pass 的【现成结论】(easy 模式最大的剧透);
  · 每条 fact 的 predicate_satisfied —— 内核对该节点条件的【字面判定】(成立与否的直接答案);
  · 每条 fact 的 ratio / top        —— C 类占比的【算好的比值/分布】(留 matched/total 让模型自己算)。
保留:condition(判据,含阈值)、class、列名、value / matched / total / matched_sessions / op / threshold、
     status(ok / insufficient_data,用来区分"有数据可判"还是"数据不足")、树结构(parent/level/is_leaf/pass_through)。

用法:
  python harden_rca.py <rca_input.json>                 # 单文件 → 同目录 rca_input_hard.json
  python harden_rca.py --suite <suite目录>               # 批量:该目录下所有 */rca_input.json → */rca_input_hard.json
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

STRIP_FACT = ("predicate_satisfied", "ratio", "top")


def harden(doc: dict) -> dict:
    ft = doc.get("fault_tree", {})
    ft.pop("outline", None)                                   # 删现成结论
    for nd in (ft.get("pruned_nodes") or {}).values():
        for f in (nd.get("facts") or []):
            for k in STRIP_FACT:
                f.pop(k, None)
    for f in (doc.get("unmapped_facts") or []):
        for k in STRIP_FACT:
            f.pop(k, None)
    doc["_mode"] = "hard"
    doc["_note"] = ("hard 模式:无 outline、无 predicate_satisfied。请逐节点按 condition + 原始事实"
                    "自己判 hold/fail/insufficient(A:value 比 op/threshold;C:matched/total 占比比阈值;"
                    "G:matched>0;H:matched_sessions>0;无数据/status=insufficient_data → 数据不足),再四档导航。")
    return doc


def _one(p: Path) -> Path:
    out = p.with_name("rca_input_hard.json")
    out.write_text(json.dumps(harden(json.loads(p.read_text(encoding="utf-8"))),
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 hard 模式 RCA 输入(删答案标签,留原始证据)")
    ap.add_argument("path", nargs="?", help="单个 rca_input.json")
    ap.add_argument("--suite", help="批量:遍历该目录下 */rca_input.json")
    args = ap.parse_args()
    n = 0
    if args.suite:
        for p in sorted(Path(args.suite).glob("**/rca_input.json")):
            _one(p); n += 1
    elif args.path:
        _one(Path(args.path)); n = 1
    else:
        ap.error("给 <rca_input.json> 或 --suite <目录>")
    print(f"hardened {n} 个 → rca_input_hard.json(无 outline / predicate_satisfied / ratio)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
