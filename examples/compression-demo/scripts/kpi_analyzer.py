#!/usr/bin/env python3
"""压缩示例:填好的 kpi 逐条件脚本(带 node_id)。演示"差值缺原始计数 → 数据不足"(中间节点卡住)。"""
import argparse
import json
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here if (_here / "condition_kernels.py").exists()
                       else _here.parents[2] / "skills" / "cs-scot-generate" / "scripts"))
from condition_kernels import load_table, evaluate_binding  # noqa: E402

INDICATOR = "kpi"

CONDITIONS = [
    {"id": "kpi_qos", "node": "QoS建立失败", "node_id": "n2",
     "condition_text": "N.QosFlow.Est.Att - N.QosFlow.Est.Succ > 0", "class": "B",
     "minuend": ["N.QosFlow.Est.Att"], "subtrahend": ["N.QosFlow.Est.Succ"], "op": ">", "threshold": 0,
     "mapping_reason": "需原始计数 Att/Succ;kpi 只有成功率、无原始计数 → 数据不足"},
]
CUSTOM = {}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--indicator", default=INDICATOR)
    args = ap.parse_args()
    sources = {}

    def _table(name):
        key = name or INDICATOR
        if key not in sources:
            try:
                sources[key] = load_table(args.data, sheet=(None if key == INDICATOR else key))
            except Exception:
                sources[key] = ([], [])
        return sources[key]

    conds = []
    for c in CONDITIONS:
        cls = str(c.get("class", "")).upper()
        if c.get("decision") in ("insufficient_data", "manual", "not_applicable") or cls == "J":
            fact = evaluate_binding(c, {}, c.get("source", INDICATOR))
        elif c.get("mode") == "custom" and c.get("id") in CUSTOM:
            fact = CUSTOM[c["id"]](_table(c.get("source")))
        else:
            _table(c.get("source"))
            fact = evaluate_binding(c, sources, c.get("source", INDICATOR))
        meta = {k: c[k] for k in ("id", "node", "node_id", "condition_text", "class", "mapping_reason") if k in c}
        conds.append({**meta, "fact": fact})
    result = {"indicator": args.indicator, "source": INDICATOR,
              "summary": {"total": len(conds)}, "conditions": conds}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[kpi_analyzer] {len(conds)} 条件 -> {args.output}")


if __name__ == "__main__":
    main()
