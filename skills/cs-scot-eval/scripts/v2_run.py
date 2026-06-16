#!/usr/bin/env python3
"""v2 端到端编排 —— 用**真实** generate/execute 技能跑手工题库,产出可评测的中间件。零重写 gap。

与 v1(inject_on_tree:复用真实内核但绕过 generate/execute 技能壳)不同,v2 真的把
suite_v2(由 v2_handcrafted.py 手工编排)喂给**真脚本**:
  1. 真 excel_to_tree   :fault_tree.csv → 产物/tree/<根>.md + .tree_index.json(重新分配 id n1,n2…)
  2. 真 assemble_scenario:→ 产物/plan.json + scripts/<源>_analyzer.py(骨架)+ condition_kernels.py + manifest
  3. code→新 id 映射     :gold_tree.json 按 code 索引 → 映射到重解析出的 nN(join key)
  4. 填【金标准绑定】到各 <源>_analyzer.py 的 CONDITIONS(node_id=新 id)——RCA 路隔离绑定误差
  5. 真 runner 逐场景跑  :--data <源>=<场景数据> → result/rca_input.json,搬到 scenarios/<场景>/rca_input.json
  6. 出绑定题(suite 级):binding_question.json(待绑判据 + 各源列概览)——绑定阶段单独评测

之后:模型逐场景读 rca_input.json 写 rca.json(RCA 题);模型读 binding_question 写 binding.json(绑定题);
再 score_v2.py 同时给【绑定】(模型 CONDITIONS vs 金标准)和【RCA】(模型 rca vs truth)打分。

用法: python v2_run.py            (先 v2_handcrafted.py 备好 suite_v2)
       python v2_run.py --keep-result   (调试:保留 产物/result 不清理)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent / "suite_v2"
GEN = HERE.parent.parent / "cs-scot-generate" / "scripts"
EXE = HERE.parent.parent / "cs-scot-execute" / "scripts"
ROOT_NAME = "SA接入失败"        # 与 v2_handcrafted.TREE 的根一致(L2 场景节点)
ROOT_LEVEL = 2


def _run(cmd: list[str]) -> None:
    p = subprocess.run([sys.executable, *cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"子步骤失败: {' '.join(cmd)}\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    return p.stdout


def code_to_id(tree_index: dict) -> dict:
    return {n["code"]: nid for nid, n in tree_index["nodes"].items() if n.get("code")}


def fill_analyzer(scaffold_text: str, conds: list) -> str:
    """把金标准 CONDITIONS 注入真实骨架的 CONDITIONS 列表(只替换该赋值块,其余逻辑原样保留)。"""
    lines = scaffold_text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("CONDITIONS = ["))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "]")
    body = json.dumps(conds, ensure_ascii=False, indent=4)
    return "\n".join(lines[:start] + ["CONDITIONS = " + body] + lines[end + 1:]) + "\n"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="v2 端到端:真 excel_to_tree+assemble+runner 跑手工题库")
    ap.add_argument("--suite", default=str(SUITE))
    ap.add_argument("--keep-result", action="store_true")
    args = ap.parse_args()
    suite = Path(args.suite)
    csv_path = suite / "fault_tree.csv"
    gold = json.loads((suite / "gold_tree.json").read_text(encoding="utf-8"))
    if not csv_path.exists():
        print("缺 suite_v2/fault_tree.csv —— 先跑 v2_handcrafted.py", file=sys.stderr)
        return 2
    prod = suite / "产物"
    if prod.exists():
        shutil.rmtree(prod)
    indicators = sorted({v["source"] for v in gold["by_code"].values()})

    # 1) 真 excel_to_tree → md + tree_index(重解析、重分配 id)
    md = prod / "tree" / f"{ROOT_NAME}.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    _run([str(GEN / "excel_to_tree.py"), str(csv_path), "--start-node", ROOT_NAME,
          "--start-level", str(ROOT_LEVEL), "-o", str(md)])
    tidx = json.loads(md.with_suffix(".tree_index.json").read_text(encoding="utf-8"))
    c2i = code_to_id(tidx)
    print(f"1) excel_to_tree:{len(tidx['nodes'])} 节点;code→id 映射 {len(c2i)} 条")

    # 2) 真 assemble_scenario → plan + analyzer 骨架 + kernels + manifest
    _run([str(GEN / "assemble_scenario.py"), "--scenario", ROOT_NAME,
          "--indicators", ",".join(indicators), "--out", str(prod)])
    print(f"2) assemble_scenario:plan.json + {len(indicators)} 个 analyzer 骨架({indicators})")

    # 3+4) code→新 id,把金标准绑定填进各 <源>_analyzer.py(RCA 路用金标准,隔离绑定误差)
    by_src: dict[str, list] = {ind: [] for ind in indicators}
    gold_for_score = []           # 供 score_v2 自测/对照(node_id 已映射到新 id)
    for code, g in gold["by_code"].items():
        nid = c2i.get(code)
        if not nid:
            raise RuntimeError(f"金标准 code {code} 在重解析树里找不到对应 id")
        b = dict(g["binding"]); b["node_id"] = nid; b["id"] = f"{nid}_b"; b["node"] = g["name"]
        by_src[g["source"]].append(b)
        gold_for_score.append(b)
    for ind in indicators:
        sc = (prod / "scripts" / f"{ind}_analyzer.py")
        sc.write_text(fill_analyzer(sc.read_text(encoding="utf-8"), by_src[ind]), encoding="utf-8")
    (prod / "_gold_binding_byid.json").write_text(
        json.dumps(gold_for_score, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"4) 金标准绑定填入 analyzer:{ {k: len(v) for k, v in by_src.items()} }")

    # 5) 真 runner 逐场景跑 → rca_input.json(用金标准绑定算事实、剪枝)
    scn_dir = suite / "scenarios"
    scns = sorted(d for d in scn_dir.iterdir() if d.is_dir())
    for d in scns:
        data_args = []
        for ind in indicators:
            f = d / "data" / f"{ind}.csv"
            if f.exists():
                data_args += ["--data", f"{ind}={f.resolve()}"]
        _run([str(EXE / "runner.py"), "--plan", str(prod / "plan.json"), "--base", str(prod), *data_args])
        src = prod / "result" / "rca_input.json"
        shutil.move(str(src), str(d / "rca_input.json"))
    print(f"5) runner 逐场景执行:{len(scns)} 个场景 → 各自 rca_input.json")

    # 6) 绑定题(suite 级):待绑判据(新 id + 大白话条件 + 源)+ 各源列概览(并集)
    overview: dict[str, dict] = {}
    for d in scns:
        for f in (d / "data").glob("*.csv"):
            src = f.stem
            ov = overview.setdefault(src, {})
            try:
                import csv as _csv
                rows = list(_csv.DictReader(f.open(encoding="utf-8")))
            except Exception:
                rows = []
            for r in rows:
                for k, v in r.items():
                    s = ov.setdefault(k, [])
                    if v not in s and len(s) < 4 and v not in ("", None):
                        s.append(v)
    to_bind = [{"node_id": c2i[code], "node": g["name"], "condition_text": g["binding"]["condition_text"],
                "source": g["source"]} for code, g in gold["by_code"].items()]
    bq = {"root": tidx["nodes"][tidx["root"]]["name"], "tree_index": "产物/tree/" + md.with_suffix('.tree_index.json').name,
          "to_bind": to_bind,
          "data_overview": {s: {"columns": [{"name": c, "samples": v} for c, v in cols.items()]}
                            for s, cols in overview.items()}}
    (suite / "binding_question.json").write_text(json.dumps(bq, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"6) 绑定题:binding_question.json({len(to_bind)} 条待绑判据,{len(overview)} 个数据源列概览)")

    if not args.keep_result:
        shutil.rmtree(prod / "result", ignore_errors=True)
    print(f"\n→ {suite}")
    print("   RCA 题:scenarios/<场景>/rca_input.json(真 runner 用金标准绑定产出)→ 模型写 rca.json")
    print("   绑定题:binding_question.json + 产物/tree/*.md → 模型写 binding.json(CONDITIONS)")
    print("   打分:python score_v2.py            (--use-reference 自测打分链路应 100%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
