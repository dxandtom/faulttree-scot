#!/usr/bin/env python3
"""逐条件执行器:读 LLM 产出的"绑定"(conditions.json)+ 各数据源 → 算出每条条件的**事实**。

每条件按 `class`(A–J)派到 condition_kernels 里预制的统计内核;内核只产事实
(算出的量 + provenance + predicate_satisfied 的字面比较),不下"成立/不成立"的最终判定——那是 RCA 的活。

绑定(conditions.json)形如:
{ "scenario":"…",
  "conditions":[
    { "id":"第22行", "node":"NSA加扇区失败", "condition_text":"<逐字树条件>",
      "class":"A", "source":"kpi", "mapping_reason":"为何这么对/为何数据不足",
      ...该类内核所需参数(column/op/threshold/transforms/group_by/sub…)… } ] }

用法:
    eval_conditions.py --conditions conditions.json --data kpi=k.csv --data chr=c.csv -o condition_facts.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from condition_kernels import load_table, evaluate_binding  # noqa: E402

_META = ("id", "node", "condition_text", "class", "source", "mapping_reason")


def run(conditions_path: str, data_map: dict) -> dict:
    doc = json.loads(Path(conditions_path).read_text(encoding="utf-8"))
    conds = doc.get("conditions", doc) if isinstance(doc, dict) else doc
    sources = {name: load_table(path) for name, path in data_map.items()}
    src_info = {name: {"columns": h, "rows": len(r)} for name, (h, r) in sources.items()}

    out_conds = []
    for c in conds:
        fact = evaluate_binding(c, sources, c.get("source"))
        entry = {k: c.get(k) for k in _META if c.get(k) is not None}
        entry["fact"] = fact
        out_conds.append(entry)

    n = len(out_conds)
    ok = sum(1 for c in out_conds if c["fact"].get("status") == "ok")
    insuf = sum(1 for c in out_conds if c["fact"].get("status") == "insufficient_data")
    manual = sum(1 for c in out_conds if c["fact"].get("status") == "manual")
    return {"scenario": (doc.get("scenario") if isinstance(doc, dict) else None),
            "sources": src_info,
            "summary": {"total": n, "ok": ok, "insufficient_data": insuf, "manual": manual},
            "conditions": out_conds}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="逐条件执行器(绑定 + 数据 → 事实)")
    ap.add_argument("--conditions", required=True, help="LLM 产出的绑定 conditions.json")
    ap.add_argument("--data", action="append", default=[], help="数据源,如 --data kpi=k.csv(可多次)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    data_map = {}
    for item in args.data:
        if "=" not in item:
            print(f"--data 格式应为 name=path: {item}", file=sys.stderr)
            sys.exit(2)
        k, v = item.split("=", 1)
        data_map[k.strip()] = v.strip()

    result = run(args.conditions, data_map)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    s = result["summary"]
    print(f"[eval_conditions] {s['total']} 条:ok={s['ok']} 数据不足={s['insufficient_data']} "
          f"人工={s['manual']} → {out}")


if __name__ == "__main__":
    main()
