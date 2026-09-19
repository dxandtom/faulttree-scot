#!/usr/bin/env python3
"""classify.py - 分类层：按0→5顺序查错，命中即停

基于对账结果 + 执行状态 + 树结构检查结果，执行错误分类。

用法:
    python classify.py --run <run.json> --rca-input <rca_input.json> --rca <rca.json>
                       --tree-index <tree_index.json> --expected <expected.json>
                       [--binding-check <binding_check.json>] [--tree-check <tree_check.json>]
                       [--reconcile <reconcile.json>]
                       [--out <output.json>]

触发条件（与 SKILL.md 决策树一致）：
    只有对账存在 MISSED（漏判）或 FALSE_CONFIRM（假阳）时才逐类查错；全部命中直接返回"未分类"（PASS 路径），
    数据缺口由 verdict 层按 PASS_WITH_GAPS 处理，不在这里定错。
    （未提供 --reconcile 时无从判断，保守地照旧全查。）

错误分类（按序命中即停）:
    0: ENV_FAILURE - execution_status.*.ok == false 或 run.json不存在
    1: INPUT_GAP - missing_inputs非空 且 有期望根因 MISSED
    2: BINDING_GAP - 绑定层的所有问题：孤儿绑定（orphan：产出了事实但树上无此节点，
       典型原因是删节点没清绑定）；或 漏判的期望根因**自身**有判据但无绑定。
       祖先未绑定不算：执行规则（rca_rules.md）规定未绑定的上级节点默认放行、永不阻断叶子确证，
       解释不了漏判；全树其余未绑定节点也不定错——"清空绑定让节点默认放行"是合规的调优方式。
    3: KNOWLEDGE_GAP - tree_index不存在该节点
    4: CRITERIA_GAP - predicate vs expected_facts不相等
    5: VERDICT_GAP - 裁决≠期望（含阴性用例假阳）——纯裁决层问题，事实全对却归入了错误的结论档（确证/疑似）才属于它
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# 错误类别定义
ERROR_CLASSES = {
    0: "ENV_FAILURE",
    1: "INPUT_GAP",
    2: "BINDING_GAP",
    3: "KNOWLEDGE_GAP",
    4: "CRITERIA_GAP",
    5: "VERDICT_GAP"
}

# 错误类别对应的分类代号(只是分类结果的代号,与评估报告 meta.verdict 的评级无关)
CLASS_CODES = {
    "ENV_FAILURE": "run_invalid",
    "INPUT_GAP": "needs_data",
    "BINDING_GAP": "needs_binding",
    "KNOWLEDGE_GAP": "needs_knowledge",
    "CRITERIA_GAP": "needs_criteria_review",
    "VERDICT_GAP": "needs_adjudication"
}


def load_json(path: Path) -> Dict[str, Any]:
    """通用 JSON 加载"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)
        return {}


def extract_actual_facts(rca_input_data: Dict[str, Any]) -> Dict[str, bool]:
    """从 rca_input 提取各节点的 predicate_satisfied（多事实取"或"：任一成立即成立）。

    兼容两种形态：
    - 剪枝形态（真实场景常态）：fault_tree.pruned_nodes[<node_id>].facts[]，
      fact 条目可能是精简后的扁平结构（字段在顶层）或未精简的嵌套 {"fact": {...}}；
    - 退化形态（树很小、剪枝不划算时）：indicator_results.<源>.conditions[]。
    """
    facts: Dict[str, bool] = {}

    def _merge(nid: Optional[str], ps: Any) -> None:
        if nid and ps is not None:
            facts[nid] = facts.get(nid, False) or bool(ps)

    pruned = (rca_input_data.get("fault_tree") or {}).get("pruned_nodes") or {}
    for nid, node in pruned.items():
        for f in node.get("facts", []):
            inner = f.get("fact")
            ff = inner if isinstance(inner, dict) else f
            _merge(nid, ff.get("predicate_satisfied"))

    for source, result in (rca_input_data.get("indicator_results") or {}).items():
        for cond in result.get("conditions", []):
            _merge(cond.get("node_id"), cond.get("predicate_satisfied"))

    return facts


def check_env_failure(run_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """类0: ENV_FAILURE 检测

    条件: execution_status.*.ok == false 或 run.json不存在
    """
    if not run_data:
        return {
            "class": 0,
            "error_category": "ENV_FAILURE",
            "class_code": "run_invalid",
            "reason": "run.json 不存在或无法读取"
        }

    exec_status = run_data.get("execution_status", {})
    failed_steps = []

    for step, status in exec_status.items():
        if isinstance(status, dict):
            if status.get("ok") is False:
                failed_steps.append({
                    "step": step,
                    "error": status.get("error", "unknown")
                })

    if failed_steps:
        return {
            "class": 0,
            "error_category": "ENV_FAILURE",
            "class_code": "run_invalid",
            "reason": f"执行步骤失败: {[s['step'] for s in failed_steps]}",
            "failed_steps": failed_steps
        }

    return None


def _case_data_names(expected_data: Dict[str, Any]) -> Optional[set]:
    """扫描用例自带 data 目录下的文件/目录名(上限 5000 个),返回小写集合。
    expected 没记录 data_source 或目录不存在 → None(无法判断,调用方保守处理)。"""
    root = str(expected_data.get("data_source") or "").strip()
    if not root or not Path(root).is_dir():
        return None
    names = set()
    for i, p in enumerate(Path(root).rglob("*")):
        names.add(p.name.lower())
        if i > 5000:
            break
    return names


_SRC_SYN = {"cmd": ("cmd", "diag"), "diag": ("diag", "cmd")}
_TABLE_SUFFIXES = (".csv", ".xlsx", ".xls")


def _source_forms(k, names: set):
    """该数据源在文件名集合里以什么形态存在 → (有内容, 有表文件)。
    表文件=名字含源名且以 .csv/.xlsx/.xls 结尾;其余(目录名/txt/log)算原始采集文本(raw 形态)。"""
    keys = _SRC_SYN.get(str(k).lower(), (str(k).lower(),))
    hit = [n for n in names if any(kk in n for kk in keys)]
    return bool(hit), any(n.endswith(_TABLE_SUFFIXES) for n in hit)


def _absent_sources(missing: List[str], run_data: Dict[str, Any], names: Optional[set]) -> set:
    """缺源里"本就不可提供"的部分:①runner 标 collection_absent;②用例数据里本来就没有该类内容(names 已知时)。"""
    kind = run_data.get("missing_kind") or {}
    absent = {k for k in missing if kind.get(k) == "collection_absent"}
    if names is not None:
        absent |= {k for k in missing if k not in absent and not _source_forms(k, names)[0]}
    return absent


def _only_raw_form(supportable: List[str], names: set) -> bool:
    """可提供的缺源是否全部只以 raw 形态在场(目录/txt 可见,没有表文件)——是则属绑定形态不匹配。"""
    forms = [_source_forms(k, names) for k in supportable]
    return all(p for p, _t in forms) and not any(t for _p, t in forms)


def _binding_form_mismatch(supportable: List[str]) -> Dict[str, Any]:
    """类1 检测改判为类2 的结果对象:现绑定(表类)处理不了在场的 raw,修复在绑定层。"""
    return {
        "class": 2,
        "error_category": "BINDING_GAP",
        "class_code": CLASS_CODES["BINDING_GAP"],
        "reason": (f"绑定形态不匹配:数据源 {supportable} 的内容在用例数据里以 raw 形态"
                   "在场(无表文件),执行缺的是表——现绑定(表类)处理不了在场的 raw。"
                   "有该节点交付包→migrate 重迁(fault_check 脚本直接吃 raw);"
                   "无包→zip2csv 转表 + data_map.json 映射,或给 tester 出脚本需求单"),
        "binding_form_mismatch": True,
        "missing_inputs": supportable
    }


def check_input_gap(run_data: Dict[str, Any], missed_count: int,
                    expected_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """类1: INPUT_GAP 检测

    条件: missing_inputs非空 且 有期望根因 MISSED（缺数据但全部命中不算错，由 verdict 层
    按 PASS_WITH_GAPS 处理；阴性用例假阳与缺数据没有因果关系，也不在这里拦）
    **且缺的数据源是本可提供的**——三条机械规则,依据都是"用例数据就是判定所需的充分数据"(与节点交付方的契约):
    ① run.json 的 missing_kind 标为 collection_absent(runner 认定采集本身不含该类数据,不是漏传)→ 剔除;
    ② 用例自带数据里本来就没有该类内容 → 剔除(数据形态天然不含,缺它是绑定位置的问题——
      实测 22 个没有 LOG 采集的用例曾被归为 INPUT_GAP,开出"使用方补数据"这种无法执行的指令);
    ③ 缺的数据源在用例数据里**只有原始采集文本(raw 形态)**(目录/txt 可见,没有表文件)而执行仍报缺 → 不是
      没数据,是现有绑定(表类)处理不了已存在的原始文本——判为 **类2 绑定形态不匹配**,修复在绑定层
      (migrate 重迁换成 S 类脚本绑定,即直接读原始文本的 fault_check 脚本 / zip2csv 转成表 + data_map 映射),
      不是要数据(实测:mpls n33 老式 cmd 表绑定,原始目录里 CMD 存在,被归为类1 让人补数据,正确做法是用交付包重迁)。
      注意:**表文件存在**(如 logbuffer.csv)却没传给脚本的仍是类1——修复方法是传入表文件/data_map 映射,
      归 needs_data 没错。
    ①②剔完为空 → 类1 不成立,继续往下查绑定层。
    """
    missing = run_data.get("missing_inputs", [])
    if not (missing and missed_count > 0):
        return None
    names = _case_data_names(expected_data) if expected_data is not None else None
    absent = _absent_sources(missing, run_data, names)
    supportable = [k for k in missing if k not in absent]
    if not supportable:
        print(f"[classify] 缺源 {sorted(absent)} 为数据形态天然不含(用例数据即如此,期望仍要求命中)"
              "→ 不构成 INPUT_GAP,穿透继续查绑定层")
        return None
    if names is not None and _only_raw_form(supportable, names):
        print(f"[classify] 缺源 {supportable} 在用例数据里只有 raw 形态在场(无表文件)"
              "→ 绑定形态不匹配,按类2 处置(migrate 重迁/转表映射),不开补数据死指令")
        return _binding_form_mismatch(supportable)
    reason = f"缺失数据源: {supportable}，且有期望根因漏判"
    if absent:
        reason += f"(另有 {sorted(absent)} 为数据形态天然不含,不计入)"
    return {
        "class": 1,
        "error_category": "INPUT_GAP",
        "class_code": "needs_data",
        "reason": reason,
        "missing_inputs": supportable
    }


def check_binding_gap(
    binding_check: Dict[str, Any],
    missed_nodes: List[str],
    rca_data: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """类2: BINDING_GAP 检测（绑定层的所有问题）

    条件一（孤儿绑定）: rca.json 的 tree_issues.orphan_node_ids 非空——执行产出了某节点
    的事实但树上没有这个节点（典型原因：删节点没清绑定），属于绑定与树不一致，修复方法是清掉残留绑定。
    条件二（缺绑定）: 某个漏判的期望根因**自身**有判据但无绑定（静态查tree_index）。
    祖先未绑定不构成本类：执行规则（rca_rules.md）规定未绑定/数据不足的上级节点默认放行、
    永不阻断叶子确证，解释不了叶子漏判——按"路径上级"开工单只会引导人去给祖先补没有交付包依据的绑定。
    全树其余未绑定节点（含故意清空绑定、走"默认放行"方式的）也不定错。
    """
    # 孤儿绑定：绑定指向树上不存在的节点——确定的不一致，先于缺绑定报出
    orphan_nodes = ((rca_data or {}).get("tree_issues") or {}).get("orphan_node_ids", [])
    if orphan_nodes:
        return {
            "class": 2,
            "error_category": "BINDING_GAP",
            "class_code": "needs_binding",
            "reason": f"存在孤儿绑定: 产出了事实但树上没有这些节点 {orphan_nodes}"
                      f"（典型：删节点没清绑定）——清掉残留绑定",
            "orphan_nodes": orphan_nodes
        }

    if not binding_check or not missed_nodes:
        return None

    unbound = binding_check.get("unbound", [])
    if not unbound:
        return None

    unbound_by_id = {u.get("node_id"): u for u in unbound if u.get("node_id")}
    for mid in missed_nodes:
        if mid in unbound_by_id:
            hit = unbound_by_id[mid]
            return {
                "class": 2,
                "error_category": "BINDING_GAP",
                "class_code": "needs_binding",
                "node_id": mid,
                "node_name": hit.get("name", ""),
                "reason": f"节点{mid}有判据但无任何绑定（漏判期望根因的自身）",
                "evidence": ["tree_index.json#" + mid]
            }
    return None


def check_knowledge_gap(tree_check: Dict[str, Any], expected_roots: List[Dict]) -> Optional[Dict[str, Any]]:
    """类3: KNOWLEDGE_GAP 检测

    条件: tree_index不存在该节点
    """
    if not tree_check:
        return None

    missing = tree_check.get("missing", [])
    if missing:
        first = missing[0]
        return {
            "class": 3,
            "error_category": "KNOWLEDGE_GAP",
            "class_code": "needs_knowledge",
            "node_id": first.get("node_id"),
            "reason": f"节点{first.get('node_id')}在tree_index中不存在"
        }
    return None


def check_criteria_gap(
    rca_input_data: Dict[str, Any],
    expected_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """类4: CRITERIA_GAP 检测

    条件: predicate_satisfied vs expected_facts不相等
    （事实提取兼容剪枝/退化两种 rca_input 形态，见 extract_actual_facts）
    """
    expected_facts = expected_data.get("expected_facts", {})
    if not expected_facts:
        return None

    actual_facts = extract_actual_facts(rca_input_data)

    # 比对
    for node_id, expected_fact in expected_facts.items():
        expected_satisfied = expected_fact.get("predicate_satisfied")
        if expected_satisfied is None or node_id not in actual_facts:
            continue

        actual_satisfied = actual_facts[node_id]
        if bool(expected_satisfied) != actual_satisfied:
            return {
                "class": 4,
                "error_category": "CRITERIA_GAP",
                "class_code": "needs_criteria_review",
                "node_id": node_id,
                "expected_predicate": expected_satisfied,
                "actual_predicate": actual_satisfied,
                "reason": f"节点{node_id}的predicate_satisfied与期望不符"
            }

    return None


def check_verdict_gap(
    rca_data: Dict[str, Any],
    reconcile_data: Dict[str, Any],
    missed_nodes: List[str]
) -> Optional[Dict[str, Any]]:
    """类5: VERDICT_GAP 检测

    条件: 裁决≠期望（漏判/阴性用例假阳）。
    只查裁决层——事实全对却归入了错误的结论档才轮到它（孤儿绑定属于绑定与树不一致，归类2）。
    """
    reconciliation = reconcile_data.get("reconciliation", {}) if reconcile_data else {}

    # 阴性用例假阳
    if reconciliation.get("false_confirm", 0) > 0:
        details = reconcile_data.get("details", [])
        fc = [d for d in details if d.get("recon") == "FALSE_CONFIRM"]
        first = fc[0] if fc else {}
        return {
            "class": 5,
            "error_category": "VERDICT_GAP",
            "class_code": "needs_adjudication",
            "node_id": first.get("node_id"),
            "node_name": first.get("node_name", ""),
            "reason": f"阴性用例出现确证根因（假阳）: {first.get('node_id')}",
            "expected_in": "none",
            "actual_in": "rca_result"
        }

    # 检查漏判
    if reconciliation.get("missed", 0) > 0:
        details = reconcile_data.get("details", [])
        missed_details = [d for d in details if d.get("recon") == "MISSED"]
        if missed_details:
            first = missed_details[0]
            return {
                "class": 5,
                "error_category": "VERDICT_GAP",
                "class_code": "needs_adjudication",
                "node_id": first.get("node_id"),
                "node_name": first.get("node_name", ""),
                "reason": f"期望根因{first.get('node_id')}不在RCA结论中",
                "expected_in": "rca_result",
                "actual_in": "none"
            }

    return None


def classify(
    run_data: Dict[str, Any],
    rca_input_data: Dict[str, Any],
    rca_data: Dict[str, Any],
    tree_index_data: Dict[str, Any],
    expected_data: Dict[str, Any],
    binding_check: Optional[Dict[str, Any]] = None,
    tree_check: Optional[Dict[str, Any]] = None,
    reconcile_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """执行完整分类流程

    触发条件：对账无 MISSED 且无 FALSE_CONFIRM → 不查错直接返回未分类（PASS 路径）。
    否则按 0→5 顺序查错，命中即停。
    """
    not_classified = {
        "classified": False,
        "error_class": None,
        "error_category": None,
        "details": None
    }

    reconciliation = (reconcile_data or {}).get("reconciliation", {})
    missed_count = reconciliation.get("missed", 0)
    false_confirm = reconciliation.get("false_confirm", 0)

    # 触发条件：提供了对账结果且全部命中 → 不查错（数据缺口交给 verdict 层的 PASS_WITH_GAPS）
    if reconcile_data and missed_count == 0 and false_confirm == 0:
        return not_classified

    # 提取漏判节点列表（用于 BINDING_GAP 和 VERDICT_GAP 定位）
    missed_nodes = []
    if reconcile_data:
        details = reconcile_data.get("details", [])
        missed_nodes = [d.get("node_id") for d in details
                        if d.get("recon") == "MISSED" and d.get("node_id")]

    # 提取期望根因节点列表
    expected_roots = expected_data.get("expected_root_causes", [])

    # 按顺序检测各类错误
    checks = [
        ("ENV_FAILURE", lambda: check_env_failure(run_data)),
        ("INPUT_GAP", lambda: check_input_gap(run_data, missed_count, expected_data)),
        ("BINDING_GAP", lambda: check_binding_gap(binding_check, missed_nodes, rca_data)),
        ("KNOWLEDGE_GAP", lambda: check_knowledge_gap(tree_check, expected_roots)),
        ("CRITERIA_GAP", lambda: check_criteria_gap(rca_input_data, expected_data)),
        ("VERDICT_GAP", lambda: check_verdict_gap(rca_data, reconcile_data, missed_nodes))
    ]

    for class_num in range(6):
        name, check_fn = checks[class_num]
        result = check_fn()
        if result:
            # 检测函数可以自行报出更准确的类别(如类1 检测发现"原始文本存在但没提供表文件"时改报类2 绑定形态
            # 不匹配)——以它为准;没有自报时按检查顺序中的位置归类(现有各检测都自报本类,行为不变)
            cls = result.get("class", class_num)
            cat = result.get("error_category") or ERROR_CLASSES.get(cls, name)
            result["error_class"] = cls
            return {
                "classified": True,
                "error_class": cls,
                "error_category": cat,
                "details": result
            }

    # 全部通过
    return not_classified


def main() -> None:
    parser = argparse.ArgumentParser(description="分类层：按0→5顺序查错")
    parser.add_argument("--run", required=True, help="run.json 路径")
    parser.add_argument("--rca-input", required=True, help="rca_input.json 路径")
    parser.add_argument("--rca", required=True, help="rca.json 路径")
    parser.add_argument("--tree-index", required=True, help="tree_index.json 路径")
    parser.add_argument("--expected", required=True, help="expected.json 路径")
    parser.add_argument("--binding-check", help="binding_check.json 路径（可选）")
    parser.add_argument("--tree-check", help="tree_check.json 路径（可选）")
    parser.add_argument("--reconcile", help="reconcile.json 路径（可选）")
    parser.add_argument("--out", help="输出 JSON 路径（可选）")
    args = parser.parse_args()

    run_data = load_json(Path(args.run))
    rca_input_data = load_json(Path(args.rca_input))
    rca_data = load_json(Path(args.rca))
    tree_index_data = load_json(Path(args.tree_index))
    expected_data = load_json(Path(args.expected))
    binding_check = load_json(Path(args.binding_check)) if args.binding_check else {}
    tree_check = load_json(Path(args.tree_check)) if args.tree_check else {}
    reconcile_data = load_json(Path(args.reconcile)) if args.reconcile else {}

    result = classify(
        run_data, rca_input_data, rca_data, tree_index_data, expected_data,
        binding_check, tree_check, reconcile_data
    )

    if result["classified"]:
        print(f"[classify] 分类结果: 类{result['error_class']} {result['error_category']}")
        print(f"  分类代号: {result['details'].get('class_code', 'unknown')}")
        print(f"  reason: {result['details'].get('reason', '')}")
    else:
        print("[classify] 分类结果: 全部通过 (PASS)")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[output] 结果已写入: {out_path}")

    sys.exit(1 if result["classified"] else 0)


if __name__ == "__main__":
    main()
