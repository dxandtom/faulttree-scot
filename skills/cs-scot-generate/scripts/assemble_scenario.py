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
    """生成一份符合 SCoT plan schema 的直线计划(含逐条件绑定层)。

    流程:各指标 analyzer(script,产概览事实)→ bind_conditions(agent,看树+概览产 conditions.json)
         → eval_conditions(condition_eval,按绑定+原始数据算 condition_facts)→ fault-tree-rca(agent,裁决)。
    runner 可重入:跑到 agent 步骤会备好输入并停下,产出后重跑自动续上。
    """
    nodes = []
    for i, ind in enumerate(indicators):
        nxt = indicators[i + 1] if i + 1 < len(indicators) else "bind_conditions"
        nodes.append({
            "id": ind,
            "kind": "script",
            "name": f"{ind}_analyzer",
            "purpose": f"解析 {ind} 指标(概览事实:列清单 + 数值摘要/十分位 + nulls)",
            "inputs": {"data": f"$input.{ind}"},
            "outputs": {"result": f"$memory.{ind}_result"},
            "transition": {"next_on_success": nxt},
        })
    nodes.append({
        "id": "bind_conditions",
        "kind": "agent_skill",
        "name": "bind-conditions",
        "purpose": "看故障树 + 各源概览,逐条件产出 A–J 绑定 conditions.json(选内核/对哪列/变换/路由/阈值;对不上标数据不足)",
        "produces": "conditions.json",
        "guide": "cs-scot-execute 技能的 references/condition_binding_schema.md",
        "inputs": {"fault_tree_md": "$memory.fault_tree_md",
                   **{ind: f"$memory.{ind}_result" for ind in indicators}},
        "outputs": {"conditions": "$memory._conditions_authored"},
        "transition": {"next_on_success": "eval_conditions"},
    })
    nodes.append({
        "id": "eval_conditions",
        "kind": "condition_eval",
        "purpose": "按绑定 + 各源原始数据,算出每条件事实 condition_facts.json(只产事实,自带 validate 自检)",
        "inputs": {"conditions": "conditions.json",
                   "data": {ind: f"$input.{ind}" for ind in indicators}},
        "outputs": {"result": "$memory.condition_facts"},
        "transition": {"next_on_success": "rca"},
    })
    nodes.append({
        "id": "rca",
        "kind": "agent_skill",
        "name": "fault-tree-rca",
        "produces": "result/rca.json",
        "purpose": "综合根因分析(消费 condition_facts:字面命中只是输入,最终成立/不成立/数据不足由你裁决)",
        "inputs": {"fault_tree_md": "$memory.fault_tree_md",
                   "condition_facts": "$memory.condition_facts",
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
        "entry": indicators[0] if indicators else "bind_conditions",
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

    for sub in ("scripts", "tree"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    copy_into(work / "tree" / f"{scenario}.md", out / "tree" / f"{scenario}.md")
    # 不再每场景生成代码:把同一个通用解析引擎 analyzer.py 按指标命名复制进去。
    # 引擎自动判别数据形态(tabular/catalog),并按文件名 <ind>_analyzer 自报指标标签。
    engine = Path(__file__).parent / "analyzer.py"
    if not engine.exists():
        print(f"找不到通用解析引擎: {engine}", file=sys.stderr)
        sys.exit(2)
    for ind in indicators:
        copy_into(engine, out / "scripts" / f"{ind}_analyzer.py")

    plan = build_plan(scenario, indicators, tree_rel)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已归集场景产物: {out}")
    print(f"  plan.json(SCoT v4.0)+ scripts/*_analyzer.py(通用引擎副本)+ tree/{scenario}.md")
    run_hint = " ".join(f"--data {ind}=<文件>" for ind in indicators)
    print(f"跑它: python <cs-scot-execute>/scripts/runner.py --plan {out}/plan.json {run_hint}")


if __name__ == "__main__":
    main()
