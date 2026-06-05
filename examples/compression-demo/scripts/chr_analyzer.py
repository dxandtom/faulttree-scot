#!/usr/bin/env python3
"""压缩示例:填好的 chr 逐条件脚本(带 node_id)。演示 D 组合 + 阈值计数。
   注:真实产物里 condition_kernels.py 与本脚本同目录;本仓库示例从 generate 技能导入,免重复。"""
import argparse
import json
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here if (_here / "condition_kernels.py").exists()
                       else _here.parents[2] / "skills" / "cs-scot-generate" / "scripts"))
from condition_kernels import load_table, evaluate_binding  # noqa: E402

INDICATOR = "chr"

CONDITIONS = [
    {"id": "chr_干扰", "node": "干扰导致", "node_id": "n3",
     "condition_text": "CHR DCErrType 为 RNL_Unspecified 且 RelSinr < 0", "class": "D", "combine": "AND",
     "mapping_reason": "需同时满足:RNL_Unspecified(C 字段匹配)+ RelSinr<0(A 阈值计数)",
     "sub": [{"class": "C", "column": "ErrType", "equals": ["RNL_Unspecified"], "match": "exact"},
             {"class": "A", "column": "RelSinr", "op": "<", "threshold": 0, "scope": "count_rows"}]},
    {"id": "chr_弱覆盖", "node": "弱覆盖", "node_id": "n4",
     "condition_text": "CHR RelRsrp < -120", "class": "A",
     "column": "RelRsrp", "op": "<", "threshold": -120, "scope": "count_rows",
     "mapping_reason": "弱覆盖:RelRsrp<-120 的行数"},
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
            for sub in (c.get("sub") or []):
                _table(sub.get("source"))
            fact = evaluate_binding(c, sources, c.get("source", INDICATOR))
        meta = {k: c[k] for k in ("id", "node", "node_id", "condition_text", "class", "mapping_reason") if k in c}
        conds.append({**meta, "fact": fact})
    result = {"indicator": args.indicator, "source": INDICATOR,
              "summary": {"total": len(conds)}, "conditions": conds}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[chr_analyzer] {len(conds)} 条件 -> {args.output}")


if __name__ == "__main__":
    main()
