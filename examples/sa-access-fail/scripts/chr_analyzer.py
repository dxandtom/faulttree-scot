#!/usr/bin/env python3
"""[由 cs-scot-generate 为「SA场景接入失败」生成] chr 指标解析脚本(自包含)。

只查数据、绝不下判断:① 数据驱动地遍历真实字段做分布/数值统计;
② 把故障子树引用到的字段取值定向取出其计数。是否成立、根因 —— 全部交给 fault-tree-rca。

自包含:内联读取函数,不依赖任何本仓自定义模块。读 xlsx 需 openpyxl;csv 用标准库。
用法:python chr_analyzer.py "<chr数据.csv 或 .xlsx>" -o "<输出.json>"
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")

# 故障子树引用到的 (字段, 取值) —— 由本场景子树得出,只取计数、不算占比、不判成立。
_REFERENCED = [("DCErrType", "RrcReject"), ("DCErrType", "TransLink")]
# 样例标识优先用这些列(找不到就用行号)。
_ID_CANDIDATES = ("CallID", "CALLID", "CellId", "Time")


def _read_table(path: str):
    """读 xlsx/csv → (表头, 行dict列表);自动编码检测。自包含。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise RuntimeError("读取 xlsx 需要 openpyxl,请先 pip install openpyxl") from e
        wb = load_workbook(p, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [("" if h is None else str(h)).strip() for h in next(it, [])]
        rows = []
        for r in it:
            rows.append({header[i]: ("" if i >= len(r) or r[i] is None else str(r[i]))
                         for i in range(len(header))})
        wb.close()
        return header, rows
    raw = p.read_bytes()
    text = None
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "ignore")
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    return headers, [{(k or ""): ("" if v is None else str(v)) for k, v in row.items()} for row in reader]


def _is_number(s: str) -> bool:
    """判断字符串能否当数值。"""
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _row_id(row: dict, idx: int) -> str:
    """给一行取一个样例标识:优先 ID 类列,否则用行号。"""
    for c in _ID_CANDIDATES:
        v = (row.get(c) or "").strip()
        if v:
            return v
    return f"row#{idx}"


def _field_stats(header, rows) -> dict:
    """数据驱动地逐字段统计:数值字段给摘要,类别字段给 top-N 分布 + 样例。不写死字段。"""
    stats = {}
    for col in header:
        vals = [(r.get(col) or "").strip() for r in rows]
        nonempty = [(i, v) for i, (r, v) in enumerate(zip(rows, vals)) if v]
        if not nonempty:
            stats[col] = {"kind": "empty", "count": 0}
            continue
        if all(_is_number(v) for _, v in nonempty):
            nums = [float(v) for _, v in nonempty]
            stats[col] = {"kind": "numeric", "count": len(nums),
                          "min": min(nums), "max": max(nums),
                          "mean": round(sum(nums) / len(nums), 4)}
        else:
            cnt = Counter(v for _, v in nonempty)
            top = dict(cnt.most_common(10))
            samples = {}
            for val in top:
                samples[val] = [_row_id(rows[i], i) for i, v in nonempty if v == val][:3]
            stats[col] = {"kind": "categorical", "distinct": len(cnt),
                          "top": top, "samples": samples}
    return stats


def _referenced(header, rows, total) -> dict:
    """定向取出故障子树引用到的 (字段, 取值) 的计数。只摆事实,不算占比、不判成立。"""
    ref = {}
    for field, val in _REFERENCED:
        key = f"{field}={val}"
        if field not in header:
            ref[key] = {"found": False, "where": field, "note": f"数据中无字段 {field}"}
            continue
        hits = [(i, r) for i, r in enumerate(rows) if (r.get(field) or "").strip() == val]
        ref[key] = {"found": True, "where": field, "count": len(hits), "total": total,
                    "samples": [_row_id(r, i) for i, r in hits][:5]}
    return ref


def analyze(data_path: str) -> dict:
    """解析 chr 数据:产出字段分布事实 + 子树引用字段的取值计数(不下任何判断)。"""
    header, rows = _read_table(data_path)
    total = len(rows)
    return {
        "indicator": "chr",
        "summary": {"total_records": total, "fields": header},
        "field_stats": _field_stats(header, rows),
        "referenced": _referenced(header, rows, total),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="chr 指标解析(SA场景接入失败,只查数据不判断)")
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
