#!/usr/bin/env python3
"""[由 scot-generate 为「SA场景接入失败」生成] chr 指标解析脚本。

读取 chr 记录(表格),统计 DCErrType 分布与占比,评估本场景子树涉及的 chr 判断条件。
确定性统计在这里完成,RCA 直接采用 conditions 的命中结果。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_loader import load_table  # noqa: E402


def analyze(data_path: str) -> dict:
    """解析 chr 数据,统计 DCErrType 分布并评估本场景子树涉及的 chr 判断条件。"""
    _, rows = load_table(data_path)
    total = len(rows)
    dist = Counter((r.get("DCErrType") or "").strip()
                   for r in rows if (r.get("DCErrType") or "").strip())

    def ratio(t: str) -> float:
        return (dist.get(t, 0) / total * 100.0) if total else 0.0

    conditions = [
        {"expr": "DCErrType 中 RrcReject 占比 > 30%",
         "values": {"RrcReject_count": dist.get("RrcReject", 0), "total": total,
                    "ratio_percent": round(ratio("RrcReject"), 2)},
         "hit": ratio("RrcReject") > 30},
        {"expr": "DCErrType 中 TransLink 占比 > 20%",
         "values": {"TransLink_count": dist.get("TransLink", 0), "total": total,
                    "ratio_percent": round(ratio("TransLink"), 2)},
         "hit": ratio("TransLink") > 20},
    ]
    return {"summary": {"record_count": total},
            "dcerrtype_distribution": dict(dist),
            "conditions": conditions}


def main() -> None:
    ap = argparse.ArgumentParser(description="chr 指标解析(SA场景接入失败)")
    ap.add_argument("data", help="chr 数据文件(xlsx/csv)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    result = analyze(args.data)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已输出: {out}")


if __name__ == "__main__":
    main()
