#!/usr/bin/env python3
"""示例:generate 时模型为「kpi」源填好的逐条件分析脚本(读表 → 逐条件统计 → 输出事实)。

演示"折中":多数条件直接套预制模板(evaluate_binding);个别模板不够用的(这里是"同列极差>阈值"),
模型才写 custom 函数。execute 把它当普通脚本跑,直接吐该源逐条件事实。

注:真实产物里 condition_kernels.py 会被 assemble 拷到本脚本同目录;本示例为免重复,
从仓库的 cs-scot-generate/scripts 导入模板库。
"""
import argparse
import json
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
# 真实产物里 condition_kernels.py 与本脚本同目录;本仓库示例则回退到 generate 技能的 scripts。
if (_here / "condition_kernels.py").exists():
    sys.path.insert(0, str(_here))
else:
    sys.path.insert(0, str(_here.parents[2] / "skills" / "cs-scot-generate" / "scripts"))
from condition_kernels import load_table, evaluate_binding, resolve_column  # noqa: E402

INDICATOR = "kpi"


def _custom_干扰波动(table):
    """模板没有"同列极差(max-min)"这种算法 → 模型自定义:UL interference Avg 的极差 > 10dB 视为抬升。"""
    header, rows = table
    col, how = resolve_column(header, "UL interference Avg")
    if not col:
        return {"class": "A", "status": "insufficient_data", "resolved_column": None,
                "reason": "未找到 UL interference Avg 列"}
    vals = []
    for r in rows:
        try:
            vals.append(float(str(r.get(col, "")).strip()))
        except ValueError:
            pass
    if not vals:
        return {"class": "A", "status": "insufficient_data", "reason": "列无有效数值"}
    rng = max(vals) - min(vals)
    return {"class": "A", "status": "ok", "resolved_column": col, "resolve": how,
            "metric": "range(max-min)", "value": round(rng, 4), "op": ">", "threshold": 10,
            "predicate_satisfied": rng > 10, "note": "模板无 range 算法 → 自定义同列极差比阈值"}


# 本场景路由到「kpi」源的条件:前 3 条套模板,最后 1 条用 custom。
CONDITIONS = [
    {"id": "第22行-合取1", "node": "NSA加扇区成功率低",
     "condition_text": "NSA_SgNB addition Success Rate < 0.99", "class": "A",
     "column": "NSA_SgNB addition Success Rate", "op": "<", "threshold": 0.99,
     "scope": "aggregate", "agg": "mean",
     "mapping_reason": "列 Common:NSA_SgNB addition Success Rate,剥前缀后语义一致;聚合取均值"},
    {"id": "QoSFlow建立成功率", "node": "QoSFlow建立成功率低",
     "condition_text": "SA_QosFlow Setup Success Rate < 85", "class": "A",
     "column": "SA_QosFlow Setup Success Rate(22B)", "op": "<", "threshold": 85,
     "scope": "count_rows",
     "mapping_reason": "百分数(0–100),阈值85;按行计满足数,NIL 行计 nulls、不当不成立"},
    {"id": "QoSFlow掉线率", "node": "QoSFlow掉线率高",
     "condition_text": "SA QoSFlow Drop Rate > 0.5", "class": "A",
     "column": "SA QoSFlow Drop Rate", "op": ">", "threshold": 0.5,
     "scope": "aggregate", "agg": "mean", "mapping_reason": "掉线率均值与阈值比"},
    {"id": "上行干扰抬升", "node": "上行干扰抬升", "mode": "custom",
     "condition_text": "上行干扰均值波动(极差)> 10dB", "class": "A",
     "mapping_reason": "模板无极差算法,自定义:同列 max-min 比阈值"},
]

CUSTOM = {"上行干扰抬升": _custom_干扰波动}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=f"{INDICATOR} 逐条件分析(读表→统计)")
    ap.add_argument("data")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--indicator", default=INDICATOR)
    args = ap.parse_args()
    # 多页签 xlsx:条件用 source=<页签名> 指到某页签;不指=活动页(键 INDICATOR)。
    sources = {INDICATOR: load_table(args.data)}

    def _table(name):
        if name and name not in sources:
            sources[name] = load_table(args.data, sheet=name)
        return sources.get(name or INDICATOR, sources[INDICATOR])

    conds = []
    for c in CONDITIONS:
        _table(c.get("source"))
        for sub in (c.get("sub") or []):
            _table(sub.get("source"))
        if c.get("mode") == "custom" and c.get("id") in CUSTOM:
            fact = CUSTOM[c["id"]](_table(c.get("source")))
        else:
            fact = evaluate_binding(c, sources, c.get("source", INDICATOR))
        meta = {k: c[k] for k in ("id", "node", "condition_text", "class", "mapping_reason") if k in c}
        conds.append({**meta, "fact": fact})
    ok = sum(1 for x in conds if x["fact"].get("status") == "ok")
    insuf = sum(1 for x in conds if x["fact"].get("status") == "insufficient_data")
    manual = sum(1 for x in conds if x["fact"].get("status") == "manual")
    result = {"indicator": args.indicator, "source": INDICATOR,
              "summary": {"total": len(conds), "ok": ok, "insufficient_data": insuf, "manual": manual},
              "conditions": conds}
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{args.indicator}_analyzer] {len(conds)} 条件 (ok={ok} insuf={insuf} manual={manual}) -> {out}")


if __name__ == "__main__":
    main()
