#!/usr/bin/env python3
"""reconcile.py - 对账层：期望根因 vs 实际RCA结果

逐期望根因节点与 RCA 结果进行比对，输出对账结果。

用法:
    python reconcile.py --expected <expected.json> --rca <rca.json>
                       [--out <output.json>]

对账结果类型:
    - HIT_CONFIRMED: 期望根因在 rca_result 中
    - HIT_DEGRADED: 期望根因只在 probable_causes 中
    - MISSED: 期望根因不在任何档
    - FALSE_CONFIRM: 阴性用例却有 rca_result
    - TRUE_NEGATIVE: 阴性用例且 rca_result 为空
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# 对账结果类型
class ReconResult:
    HIT_CONFIRMED = "HIT_CONFIRMED"       # 确证命中
    HIT_DEGRADED = "HIT_DEGRADED"         # 降级命中（只在 probable_causes）
    MISSED = "MISSED"                     # 漏判
    FALSE_CONFIRM = "FALSE_CONFIRM"       # 假阳（阴性用例有结果）
    TRUE_NEGATIVE = "TRUE_NEGATIVE"       # 真阴（阴性用例无结果）
    NOT_IN_RCA = "NOT_IN_RCA"             # 不在 RCA 输出中


def load_json(path: Path) -> Dict[str, Any]:
    """通用 JSON 加载"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[error] 无法读取 {path}: {e}", file=sys.stderr)
        sys.exit(1)


def extract_rca_node_ids(rca_result: List[Dict[str, Any]]) -> set:
    """从 RCA 结果中提取所有节点 ID"""
    node_ids = set()
    for item in rca_result:
        for evidence in item.get("evidence_path", []):
            nid = evidence.get("node_id")
            if nid:
                node_ids.add(nid)
        # 也检查 fault_tree_path
        for path_node in item.get("Fault_tree_path", []):
            if path_node:
                node_ids.add(path_node)
    return node_ids


def reconcile_single(
    expected_root: Dict[str, Any],
    rca_result_ids: set,
    probable_cause_ids: set,
    evidence_source: Optional[str] = None
) -> Dict[str, Any]:
    """对单个期望根因进行对账

    返回（与 SKILL.md evaluation_report.json reconciliation.details 对齐）:
        {
            "node_id": str,
            "node_name": str,
            "expected_result": "confirmed",  # expected_root_causes 都是期望确证的
            "actual_result": "confirmed" | "probable" | "none",
            "recon": ReconResult类型,
            "evidence_source": str | None
        }
    """
    node_id = expected_root.get("node_id")
    node_name = expected_root.get("node_name", "")

    in_rca = node_id in rca_result_ids
    in_probable = node_id in probable_cause_ids

    if in_rca:
        recon = ReconResult.HIT_CONFIRMED
        actual_result = "confirmed"
    elif in_probable:
        recon = ReconResult.HIT_DEGRADED
        actual_result = "probable"
    else:
        recon = ReconResult.MISSED
        actual_result = "none"

    return {
        "node_id": node_id,
        "node_name": node_name,
        "expected_result": "confirmed",  # expected_root_causes 里的都是期望确证的
        "actual_result": actual_result,
        "recon": recon,
        "evidence_source": evidence_source
    }


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
                "hit_degraded": int,
                "missed": int,
                "false_confirm": int,
                "true_negative": int
            },
            "details": [{node_id, node_name, expected_result, actual_result, recon, evidence_source}, ...]
        }
    """
    expected = load_json(expected_path)
    rca = load_json(rca_path)

    case_id = expected.get("case_id", "unknown")
    scenario = expected.get("scenario", "")
    negative = expected.get("negative", False)
    expected_roots = expected.get("expected_root_causes", [])
    expected_facts = expected.get("expected_facts", {})  # 用于提取 evidence_source

    rca_result = rca.get("rca_result", [])
    probable = rca.get("probable_causes", [])

    rca_result_ids = extract_rca_node_ids(rca_result)
    probable_ids = extract_rca_node_ids(probable)

    details = []
    hit_confirmed = 0
    hit_degraded = 0
    missed = 0
    false_confirm = 0
    true_negative = 0

    # 阳性用例对账
    if not negative:
        for root in expected_roots:
            node_id = root.get("node_id")
            # 从 expected_facts 中提取 evidence_source
            fact = expected_facts.get(node_id, {})
            evidence_source = fact.get("evidence_source")
            detail = reconcile_single(root, rca_result_ids, probable_ids, evidence_source)
            details.append(detail)

            if detail["recon"] == ReconResult.HIT_CONFIRMED:
                hit_confirmed += 1
            elif detail["recon"] == ReconResult.HIT_DEGRADED:
                hit_degraded += 1
            elif detail["recon"] == ReconResult.MISSED:
                missed += 1
    # 阴性用例对账
    else:
        if rca_result:
            # 阴性用例却有 rca_result = 假阳
            false_confirm = len(rca_result)
            for item in rca_result:
                for evidence in item.get("evidence_path", []):
                    nid = evidence.get("node_id")
                    if nid:
                        details.append({
                            "node_id": nid,
                            "node_name": item.get("Root_cause", ""),
                            "expected_result": "none",
                            "actual_result": "confirmed",
                            "recon": ReconResult.FALSE_CONFIRM,
                            "evidence_source": None
                        })
        else:
            # 阴性用例无 rca_result = 真阴
            true_negative = 1
            details.append({
                "node_id": None,
                "node_name": scenario,
                "expected_result": "none",
                "actual_result": "none",
                "recon": ReconResult.TRUE_NEGATIVE,
                "evidence_source": None
            })

    return {
        "case_id": case_id,
        "scenario": scenario,
        "negative": negative,
        "reconciliation": {
            "total_expected": len(expected_roots),
            "hit_confirmed": hit_confirmed,
            "hit_degraded": hit_degraded,
            "missed": missed,
            "false_confirm": false_confirm,
            "true_negative": true_negative
        },
        "details": details
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

    recon = result["reconciliation"]
    print(f"[reconcile] 对账完成: case_id={result['case_id']}")
    print(f"  期望根因数: {recon['total_expected']}")
    print(f"  确证命中: {recon['hit_confirmed']}")
    print(f"  降级命中: {recon['hit_degraded']}")
    print(f"  漏判: {recon['missed']}")
    print(f"  假阳: {recon['false_confirm']}")
    print(f"  真阴: {recon['true_negative']}")

    if recon["missed"] > 0:
        print(f"\n[warn] 漏判节点:")
        for d in result["details"]:
            if d["result"] == ReconResult.MISSED:
                print(f"  - {d['node_id']}: {d['node_name']}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[output] 结果已写入: {out_path}")

    # 对账有漏判或假阳则返回 1
    has_issues = recon["missed"] > 0 or recon["false_confirm"] > 0
    sys.exit(1 if has_issues else 0)


if __name__ == "__main__":
    main()
