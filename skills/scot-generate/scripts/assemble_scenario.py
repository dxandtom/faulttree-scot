#!/usr/bin/env python3
"""把 scot-generate 的中间产物打包成一个自包含的场景 skill。

会做:建目录、拷子树/各指标脚本/codegen 提示词、拷 data_loader/runner/rca_rules、
生成 plan.json、由模板渲染场景 SKILL.md。产出目录可直接作为平台 skill 上传。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import List

HERE = Path(__file__).resolve()
GEN_SKILL = HERE.parents[1]      # skills/scot-generate
SKILLS_ROOT = HERE.parents[2]    # skills/

DEFAULT_DATA_LOADER = GEN_SKILL / "scripts" / "data_loader.py"
DEFAULT_RUNNER = SKILLS_ROOT / "scot-execute" / "scripts" / "runner.py"
DEFAULT_RCA_RULES = SKILLS_ROOT / "fault-tree-rca" / "references" / "rca_rules.md"
DEFAULT_TEMPLATE = GEN_SKILL / "references" / "scenario_template" / "SKILL.md.tmpl"


def slugify(name: str) -> str:
    """场景名 → kebab-case 的 skill name(中文等非 ASCII 会被去掉,空则兜底)。"""
    s = re.sub(r"[\s_]+", "-", name.strip().lower())
    s = re.sub(r"[^a-z0-9-]+", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "scenario"


def build_plan(scenario: str, indicators: List[str], tree_rel: str) -> dict:
    """根据指标列表生成直线 SCoT 计划(每指标一个 script 步骤 + 末尾一个 rca 步骤)。"""
    steps = []
    for ind in indicators:
        steps.append({
            "id": ind, "kind": "script",
            "run": f"scripts/{ind}_analyzer.py",
            "data": f"$data.{ind}",
            "out": f"result/{ind}.json",
            "desc": f"解析 {ind} 指标",
        })
    steps.append({
        "id": "rca", "kind": "rca",
        "tree": tree_rel,
        "inputs": [f"result/{ind}.json" for ind in indicators],
        "rules": "references/rca_rules.md",
        "out": "result/rca.json",
        "desc": "综合根因分析",
    })
    return {"version": "1.0", "scenario": scenario, "fault_tree": tree_rel,
            "indicators": indicators, "steps": steps}


def render_skill_md(template_text: str, scenario: str, slug: str, indicators: List[str]) -> str:
    """渲染场景 SKILL.md(简单占位符替换)。"""
    data_args = " ".join(f"--data {ind}=<{ind}数据文件>" for ind in indicators)
    return (template_text
            .replace("{{SCENARIO}}", scenario)
            .replace("{{SCENARIO_SLUG}}", slug)
            .replace("{{INDICATORS}}", "、".join(indicators))
            .replace("{{DATA_ARGS}}", data_args))


def copy_into(src: Path, dst: Path) -> None:
    """拷贝单个文件,缺失则告警跳过(不阻断打包)。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        print(f"  [warn] 缺少源文件,跳过: {src}", file=sys.stderr)
        return
    shutil.copy2(src, dst)


def main() -> None:
    ap = argparse.ArgumentParser(description="打包自包含场景 skill")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--work", required=True, help="中间产物目录(含 tree/、scripts/、codegen_prompts/)")
    ap.add_argument("--indicators", required=True, help="逗号分隔,如 kpi,chr")
    ap.add_argument("--out", required=True, help="输出场景 skill 目录")
    ap.add_argument("--slug", help="显式指定 skill 名(kebab-case);默认从场景名推断,中文名建议显式指定")
    ap.add_argument("--data-loader", default=str(DEFAULT_DATA_LOADER))
    ap.add_argument("--runner", default=str(DEFAULT_RUNNER))
    ap.add_argument("--rca-rules", default=str(DEFAULT_RCA_RULES))
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    args = ap.parse_args()

    scenario = args.scenario
    indicators = [s.strip() for s in args.indicators.split(",") if s.strip()]
    work, out = Path(args.work), Path(args.out)
    slug = args.slug or slugify(scenario)
    tree_rel = f"tree/{scenario}.md"

    for sub in ("scripts", "tree", "references", "codegen_prompts", "result"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    # 子树
    copy_into(work / "tree" / f"{scenario}.md", out / "tree" / f"{scenario}.md")
    # 各指标脚本 + 生成的 codegen 提示词
    for ind in indicators:
        copy_into(work / "scripts" / f"{ind}_analyzer.py", out / "scripts" / f"{ind}_analyzer.py")
        copy_into(work / "codegen_prompts" / f"{ind}_codegen_prompt.md",
                  out / "codegen_prompts" / f"{ind}_codegen_prompt.md")
    # 运行所需(拷入,保证 docker 内自包含)
    copy_into(Path(args.data_loader), out / "scripts" / "data_loader.py")
    copy_into(Path(args.runner), out / "scripts" / "runner.py")
    copy_into(Path(args.rca_rules), out / "references" / "rca_rules.md")
    # plan.json(SCoT)
    plan = build_plan(scenario, indicators, tree_rel)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    # 场景 SKILL.md
    tmpl = Path(args.template)
    if tmpl.exists():
        md = render_skill_md(tmpl.read_text(encoding="utf-8"), scenario, slug, indicators)
        (out / "SKILL.md").write_text(md, encoding="utf-8")
    else:
        print(f"  [warn] 模板缺失: {tmpl}", file=sys.stderr)

    print(f"已组装自包含场景 skill: {out}  (skill name = {slug})")


if __name__ == "__main__":
    main()
