#!/usr/bin/env python3
"""reconcile.py - 对账层：期望根因 vs 实际RCA结果

逐期望根因节点与 RCA 结果进行比对，输出对账结果。

用法:
    python reconcile.py --expected <expected.json> --rca <rca.json>
                       [--out <output.json>]

对账结果类型:
    - HIT_CONFIRMED: 期望根因就是某条确证根因的候选自身
    - HIT_ANCESTOR: 期望根因是某条确证根因的路径祖先（诊断落在期望子树内、比期望更深）
                    —— 算通过，但单独计数并给出"用例期望粒度偏粗"的质量提示
    - HIT_DEGRADED: 期望根因在 probable_causes 中（候选自身或其路径祖先）
    - MISSED: 期望根因不在任何档
    - FALSE_CONFIRM: 阴性用例却有 rca_result
    - TRUE_NEGATIVE: 阴性用例且 rca_result 为空

候选自身与路径祖先的区分：rca.json 的确证/疑似条目顶层带 node_id（候选自身），
evidence_path 逐节点带 node_id 且最后一个是候选自身、其余是路径上级。
Fault_tree_path 是节点名字列表（不是 id），仅当元素形如 nX 时才作为祖先兜底。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# 对账结果类型
class ReconResult:
    HIT_CONFIRMED = "HIT_CONFIRMED"       # 精确命中（期望=确证候选自身）
    HIT_ANCESTOR = "HIT_ANCESTOR"         # 祖先命中（期望是确证候选的路径上级）
    HIT_DEGRADED = "HIT_DEGRADED"         # 降级命中（只在 probable_causes）
    MISSED = "MISSED"                     # 漏判
    FALSE_CONFIRM = "FALSE_CONFIRM"       # 假阳（阴性用例有结果）
    TRUE_NEGATIVE = "TRUE_NEGATIVE"       # 真阴（阴性用例无结果）


_NODE_ID_RE = re.compile(r"^[nv]\d+$")


def load_json(path: Path) -> Dict[str, Any]:
    """通用 JSON 加载"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[error] 无法读取 {path}: {e}", file=sys.stderr)
        sys.exit(1)


def entry_node_ids(item: Dict[str, Any]) -> Tuple[Optional[str], Set[str]]:
    """从一条确证/疑似条目里提取 (候选自身 node_id, 路径祖先 node_id 集合)。

    优先级：顶层 node_id 字段（执行链 finalize 的输出直接带）→ evidence_path 最后一个节点。
    祖先 = evidence_path 除最后一个之外的节点；Fault_tree_path 仅当元素形如 nX 时兜底并入。
    """
    ev = item.get("evidence_path") or []
    ev_ids = [e.get("node_id") for e in ev if isinstance(e, dict) and e.get("node_id")]
    self_id = item.get("node_id") or (ev_ids[-1] if ev_ids else None)
    ancestors: Set[str] = set(ev_ids[:-1]) if ev_ids else set()
    for p in item.get("Fault_tree_path") or []:
        if isinstance(p, str) and _NODE_ID_RE.match(p) and p != self_id:
            ancestors.add(p)
    ancestors.discard(self_id)
    return self_id, ancestors


def index_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把条目列表整理成 [{self_id, ancestors, name}] 便于逐期望比对。"""
    out = []
    for item in entries:
        self_id, ancestors = entry_node_ids(item)
        out.append({"self_id": self_id, "ancestors": ancestors,
                    "name": item.get("Root_cause", "")})
    return out


def reconcile_single(
    expected_root: Dict[str, Any],
    confirmed_index: List[Dict[str, Any]],
    probable_index: List[Dict[str, Any]],
    evidence_source: Optional[str] = None
) -> Dict[str, Any]:
    """对单个期望根因进行对账

    返回（与 evaluation_report.json reconciliation.details 对齐）:
        {
            "node_id": str,
            "node_name": str,
            "expected_result": "confirmed",  # expected_root_causes 都是期望确证的
            "actual_result": "confirmed" | "confirmed_descendant" | "probable" | "none",
            "recon": ReconResult类型,
            "actual_node_id": str | None,   # 祖先命中时=实际确证的更深节点
            "hint": str | None,             # 祖先命中时的用例质量提示
            "evidence_source": str | None
        }
    """
    node_id = expected_root.get("node_id")
    node_name = expected_root.get("node_name", "")

    detail = {
        "node_id": node_id,
        "node_name": node_name,
        "expected_result": "confirmed",
        "actual_result": "none",
        "recon": ReconResult.MISSED,
        "actual_node_id": None,
        "hint": None,
        "evidence_source": evidence_source
    }

    # 1) 精确命中：期望就是某条确证候选自身
    for e in confirmed_index:
        if node_id == e["self_id"]:
            detail.update(recon=ReconResult.HIT_CONFIRMED, actual_result="confirmed",
                          actual_node_id=node_id)
            return detail

    # 2) 祖先命中：期望是某条确证候选的路径上级（诊断更深、落在期望子树内）
    for e in confirmed_index:
        if node_id in e["ancestors"]:
            detail.update(
                recon=ReconResult.HIT_ANCESTOR, actual_result="confirmed_descendant",
                actual_node_id=e["self_id"],
                hint=(f"期望根因 {node_id}「{node_name}」是实际确证 {e['self_id']}"
                      f"「{e['name']}」的路径上级——期望粒度比实际粗,建议把用例期望精化到 "
                      f"{e['self_id']}(粗粒度期望无法区分同一子树内的兄弟叶子)"))
            return detail

    # 3) 降级命中：期望在疑似档（候选自身或其路径祖先都算——疑似本身已是降级档）
    for e in probable_index:
        if node_id == e["self_id"] or node_id in e["ancestors"]:
            detail.update(recon=ReconResult.HIT_DEGRADED, actual_result="probable",
                          actual_node_id=e["self_id"])
            return detail

    return detail  # MISSED


def reconcile(
    expected_path: Path,
    rca_path: Path
) -> Dict[str, Any]:
    """执行完整对账

    返回:
        {
            "case_id": str,
            "scenario": str,
            "negative": bool,
            "reconciliation": {
                "total_expected": int,
                "hit_confirmed": int,
                "hit_ancestor": int,
                "hit_degraded": int,
                "missed": int,
                "false_confirm": int,
                "true_negative": int
            },
            "details": [{node_id, node_name, expected_result, actual_result, recon, ...}, ...],
            "case_quality": [str, ...]   # 祖先命中的"期望粒度偏粗"提示（可为空）
        }
    """
    expected = load_json(expected_path)
    rca = load_json(rca_path)

    case_id = expected.get("case_id", "unknown")
    scenario = expected.get("scenario", "")
    negative = expected.get("negative", False)
    expected_roots = expected.get("expected_root_causes", [])
    expected_facts = expected.get("expected_facts", {})  # 用于提取 evidence_source

    confirmed_index = index_entries(rca.get("rca_result", []))
    probable_index = index_entries(rca.get("probable_causes", []))

    details = []
    case_quality = []
    counts = {"hit_confirmed": 0, "hit_ancestor": 0, "hit_degraded": 0,
              "missed": 0, "false_confirm": 0, "true_negative": 0}

    # 阳性用例对账
    if not negative:
        for root in expected_roots:
            node_id = root.get("node_id")
            # 从 expected_facts 中提取 evidence_source
            fact = expected_facts.get(node_id, {})
            evidence_source = fact.get("evidence_source")
            detail = reconcile_single(root, confirmed_index, probable_index, evidence_source)
            details.append(detail)

            if detail["recon"] == ReconResult.HIT_CONFIRMED:
                counts["hit_confirmed"] += 1
            elif detail["recon"] == ReconResult.HIT_ANCESTOR:
                counts["hit_ancestor"] += 1
                if detail.get("hint"):
                    case_quality.append(detail["hint"])
            elif detail["recon"] == ReconResult.HIT_DEGRADED:
                counts["hit_degraded"] += 1
            elif detail["recon"] == ReconResult.MISSED:
                counts["missed"] += 1
    # 阴性用例对账
    else:
        if rca.get("rca_result"):
            # 阴性用例却有 rca_result = 假阳
            counts["false_confirm"] = len(rca["rca_result"])
            for e in confirmed_index:
                details.append({
                    "node_id": e["self_id"],
                    "node_name": e["name"],
                    "expected_result": "none",
                    "actual_result": "confirmed",
                    "recon": ReconResult.FALSE_CONFIRM,
                    "actual_node_id": e["self_id"],
                    "hint": None,
                    "evidence_source": None
                })
        else:
            # 阴性用例无 rca_result = 真阴
            counts["true_negative"] = 1
            details.append({
                "node_id": None,
                "node_name": scenario,
                "expected_result": "none",
                "actual_result": "none",
                "recon": ReconResult.TRUE_NEGATIVE,
                "actual_node_id": None,
                "hint": None,
                "evidence_source": None
            })

    return {
        "case_id": case_id,
        "scenario": scenario,
        "negative": negative,
        "reconciliation": {"total_expected": len(expected_roots), **counts},
        "details": details,
        "case_quality": case_quality
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="对账层：期望根因 vs 实际RCA结果")
    parser.add_argument("--expected", required=True, help="expected.json 路径")
    parser.add_argument("--rca", required=True, help="rca.json 路径")
    parser.add_argument("--out", help="输出 JSON 路径（可选）")
    args = parser.parse_args()

    expected_path = Path(args.expected)
    rca_path = Path(args.rca)

    if not expected_path.exists():
        print(f"[error] expected.json 不存在: {expected_path}", file=sys.stderr)
        sys.exit(1)
    if not rca_path.exists():
        print(f"[error] rca.json 不存在: {rca_path}", file=sys.stderr)
        sys.exit(1)

    result = reconcile(expected_path, rca_path)

    # 结果先落盘再打印——打印环节出任何问题都不能丢对账结果
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[output] 结果已写入: {out_path}")

    recon = result["reconciliation"]
    print(f"[reconcile] 对账完成: case_id={result['case_id']}")
    print(f"  期望根因数: {recon['total_expected']}")
    print(f"  精确命中: {recon['hit_confirmed']}")
    print(f"  祖先命中: {recon['hit_ancestor']}（算通过,期望粒度偏粗,见 case_quality 提示）")
    print(f"  降级命中: {recon['hit_degraded']}")
    print(f"  漏判: {recon['missed']}")
    print(f"  假阳: {recon['false_confirm']}")
    print(f"  真阴: {recon['true_negative']}")

    if recon["missed"] > 0:
        print(f"\n[warn] 漏判节点:")
        for d in result["details"]:
            if d["recon"] == ReconResult.MISSED:
                print(f"  - {d['node_id']}: {d['node_name']}")

    for hint in result.get("case_quality", []):
        print(f"\n[case-quality] {hint}")

    # 对账有漏判或假阳则返回 1
    has_issues = recon["missed"] > 0 or recon["false_confirm"] > 0
    sys.exit(1 if has_issues else 0)


if __name__ == "__main__":
    main()
