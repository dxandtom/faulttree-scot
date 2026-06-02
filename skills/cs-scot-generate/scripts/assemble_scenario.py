#!/usr/bin/env python3
"""把 cs-scot-generate 的中间产物归集成一个场景的诊断产物(资产,不是 skill)。

产物 = plan.json(真正的 SCoT 计划)+ scripts/<指标>_analyzer.py(自包含解析脚本)
       + tree/<场景>.md + codegen_prompts/(留痕)。由 cs-scot-execute 来跑。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import List

# 与 excel_to_tree.py 保持一致的统一产物目录约定:<anchor>/scot产物/<场景名>
DEFAULT_OUT_CONTAINER = "scot产物"


def scenario_out_dir(anchor_dir: Path, scenario: str) -> Path:
    """统一的场景产物目录:<anchor_dir>/scot产物/<场景名>。"""
    return Path(anchor_dir) / DEFAULT_OUT_CONTAINER / scenario


def build_plan(scenario: str, indicators: List[str], tree_rel: str) -> dict:
    """生成一份符合 SCoT plan schema 的直线计划。

    - 每个指标 → 一个 kind=script 节点(跑解析脚本,产出存入 $memory.<指标>_result);
    - 末尾 → 一个 kind=agent_skill 节点(fault-tree-rca,综合根因);
    - 节点用 transition.next_on_success 串成直线,引用用 $input/$memory/$output。
    """
    nodes = []
    for i, ind in enumerate(indicators):
        nxt = indicators[i + 1] if i + 1 < len(indicators) else "rca"
        nodes.append({
            "id": ind,
            "kind": "script",
            "name": f"{ind}_analyzer",
            "purpose": f"解析 {ind} 指标",
            "inputs": {"data": f"$input.{ind}"},
            "outputs": {"result": f"$memory.{ind}_result"},
            "transition": {"next_on_success": nxt},
        })
    nodes.append({
        "id": "rca",
        "kind": "agent_skill",
        "name": "fault-tree-rca",
        "purpose": "综合根因分析",
        "inputs": {"fault_tree_md": "$memory.fault_tree_md",
                   **{ind: f"$memory.{ind}_result" for ind in indicators}},
        "outputs": {"result": "$output.final_answer"},
        "transition": {"end": True},
    })
    return {
        "version": "4.0",
        "plan_id": f"plan_{scenario}",
        "meta": {
            "goal": f"诊断「{scenario}」的根因",
            "success_criteria": ["输出所有成立的根因路径与逐条判断依据"],
        },
        "entry": indicators[0] if indicators else "rca",
        "memory_init": {"fault_tree_md": tree_rel},
        "nodes": nodes,
    }


def copy_into(src: Path, dst: Path) -> None:
    """拷贝单个文件,缺失则告警跳过(不阻断);源与目标同一文件则免拷(work==out 的常态)。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        print(f"  [warn] 缺少源文件,跳过: {src}", file=sys.stderr)
        return
    if src.resolve() == dst.resolve():
        return  # 已在目标位置,无需拷贝
    shutil.copy2(src, dst)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="归集场景诊断产物")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--indicators", required=True, help="逗号分隔,如 kpi,chr")
    ap.add_argument("--out", help="场景产物目录(中间产物也在这里;默认 = <out-base 或 xlsx 所在目录>/scot产物/<场景名>)")
    ap.add_argument("--out-base", help="产物根目录;与 --out 二选一,默认配合 --xlsx 推导")
    ap.add_argument("--xlsx", help="故障树 xlsx 路径;省略 --out 时用它所在目录作为产物根")
    ap.add_argument("--work", help="中间产物目录;默认与 --out 相同(work 与 out 合并为同一目录)")
    args = ap.parse_args()

    scenario = args.scenario
    indicators = [s.strip() for s in args.indicators.split(",") if s.strip()]

    # 统一产物目录:--out 优先;否则按 <out-base 或 xlsx 所在目录>/scot产物/<场景> 推导(与步骤1一致)。
    if args.out:
        out = Path(args.out)
    else:
        if args.out_base:
            anchor: Path = Path(args.out_base).expanduser()
        elif args.xlsx:
            anchor = Path(args.xlsx).expanduser().resolve().parent
        else:
            print("需要 --out,或用 --xlsx/--out-base 让我推导默认产物目录", file=sys.stderr)
            sys.exit(2)
        out = scenario_out_dir(anchor, scenario)
    work = Path(args.work) if args.work else out  # 默认 work==out,产物只此一处
    tree_rel = f"tree/{scenario}.md"

    for sub in ("scripts", "tree", "codegen_prompts"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    copy_into(work / "tree" / f"{scenario}.md", out / "tree" / f"{scenario}.md")
    for ind in indicators:
        copy_into(work / "scripts" / f"{ind}_analyzer.py", out / "scripts" / f"{ind}_analyzer.py")
        copy_into(work / "codegen_prompts" / f"{ind}_codegen_prompt.md",
                  out / "codegen_prompts" / f"{ind}_codegen_prompt.md")

    plan = build_plan(scenario, indicators, tree_rel)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已归集场景产物: {out}")
    print(f"  plan.json(SCoT v4.0)+ scripts/*_analyzer.py + tree/{scenario}.md + codegen_prompts/")
    run_hint = " ".join(f"--data {ind}=<文件>" for ind in indicators)
    print(f"跑它: python <cs-scot-execute>/scripts/runner.py --plan {out}/plan.json {run_hint}")


if __name__ == "__main__":
    main()
