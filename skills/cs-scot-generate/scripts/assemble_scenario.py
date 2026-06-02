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
    """拷贝单个文件,缺失则告警跳过(不阻断)。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        print(f"  [warn] 缺少源文件,跳过: {src}", file=sys.stderr)
        return
    shutil.copy2(src, dst)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="归集场景诊断产物")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--work", required=True, help="中间产物目录(含 tree/、scripts/、codegen_prompts/)")
    ap.add_argument("--indicators", required=True, help="逗号分隔,如 kpi,chr")
    ap.add_argument("--out", required=True, help="输出场景产物目录")
    args = ap.parse_args()

    scenario = args.scenario
    indicators = [s.strip() for s in args.indicators.split(",") if s.strip()]
    work, out = Path(args.work), Path(args.out)
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
