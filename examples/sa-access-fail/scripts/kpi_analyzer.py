#!/usr/bin/env python3
"""[由 cs-scot-generate 为「SA场景接入失败」生成] kpi 指标解析脚本(自包含)。

只查数据、绝不下判断:① 逐行剥引号 json.loads、遍历每行所有键做数据驱动统计;
② 把故障子树引用到的计数器定向取出其原始取值(只取值,不做减法、不比阈值)。
是否成立、根因 —— 全部交给 fault-tree-rca。

自包含:内联读取函数,不依赖任何本仓自定义模块。读 xlsx 需 openpyxl;csv 用标准库。
用法:python kpi_analyzer.py "<kpi数据.csv 或 .xlsx>" -o "<输出.json>"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")

# 故障子树引用到的计数器 —— 由本场景子树得出,只取原始值,不做任何运算/比较。
_REFERENCED = ["N.RRC.SetupAtt.Sum", "N.RRC.SetupSucc.Sum",
               "N.RRC.SetupFail.NoRes", "N.NG.Setup.Fail.NoRsp"]


def _read_json_lines(path: str, column: str = "KpiTreeJson") -> list:
    """读"每行一段 JSON"的 kpiTree(csv/xlsx)→ dict 列表;非法行跳过。自包含。"""
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
        out = []
        for r in it:
            cells = [("" if v is None else str(v)) for v in r]
            raw = ""
            if column in header:
                raw = cells[header.index(column)] if header.index(column) < len(cells) else ""
            if not raw:
                raw = next((c for c in cells if c.strip()), "")
            raw = raw.strip()
            if raw:
                try:
                    out.append(json.loads(raw))
                except (json.JSONDecodeError, ValueError):
                    pass
        wb.close()
        return out
    raw_bytes = p.read_bytes()
    text = None
    for enc in _ENCODINGS:
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw_bytes.decode("utf-8", "ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    start = 1 if lines and (lines[0].startswith(column) or not lines[0].lstrip().startswith("{")) else 0
    out = []
    for ln in lines[start:]:
        s = ln.strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            s = s[1:-1].replace('""', '"')
        try:
            out.append(json.loads(s))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def _clean(name: str) -> str:
    """清洗计数器名:截断 HTML 污染(如末尾 <span、实体 &gt; 等)。属于忠实取数,不是判断。"""
    return re.split(r"[<&]", name)[0].strip()


def _is_number(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _num(v):
    """把值转 int/float;非数返回原字符串。"""
    f = float(v)
    return int(f) if f.is_integer() else f


def _field_stats(rows) -> dict:
    """遍历每行所有键做数据驱动统计:数值键给摘要,其余给 top-N 分布。不写死字段。"""
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    stats = {}
    for k in keys:
        vals = [str(r[k]) for r in rows if k in r and r[k] not in (None, "")]
        if not vals:
            stats[k] = {"kind": "empty", "count": 0}
        elif all(_is_number(v) for v in vals):
            nums = [float(v) for v in vals]
            stats[k] = {"kind": "numeric", "count": len(nums),
                        "min": min(nums), "max": max(nums),
                        "mean": round(sum(nums) / len(nums), 4)}
        else:
            cnt = Counter(vals)
            stats[k] = {"kind": "categorical", "distinct": len(cnt),
                        "top": dict(cnt.most_common(10))}
    return stats


def _referenced(rows) -> dict:
    """定向取出故障子树引用到的计数器原始值。只取值,不做减法/比阈值/判成立。"""
    # 建 清洗名 → (原始值, 原始名) 映射;记录被清洗过的名,作为取数事实。
    name_to = {}
    for r in rows:
        raw_name = str(r.get("name", ""))
        clean = _clean(raw_name)
        if clean:
            name_to[clean] = (r.get("value"), raw_name)
    ref = {}
    for counter in _REFERENCED:
        if counter not in name_to:
            ref[counter] = {"found": False, "where": "kpiTree.name",
                            "note": "数据中无此计数器(按缺失处理,是否影响判断由 RCA 决定)"}
            continue
        raw_value, raw_name = name_to[counter]
        entry = {"found": True, "where": "kpiTree(name=该计数器 的 value)",
                 "value": _num(raw_value) if _is_number(raw_value) else raw_value}
        if raw_name != counter:
            entry["raw_name"] = raw_name  # 原始名带污染,已清洗 —— 摆出事实供核对
        ref[counter] = entry
    return ref


def analyze(data_path: str) -> dict:
    """解析 kpi(kpiTree)数据:产出字段统计事实 + 子树引用计数器的取值(不下任何判断)。"""
    rows = _read_json_lines(data_path)
    return {
        "indicator": "kpi",
        "summary": {"total_nodes": len(rows)},
        "field_stats": _field_stats(rows),
        "referenced": _referenced(rows),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="kpi 指标解析(SA场景接入失败,只查数据不判断)")
    ap.add_argument("data", help="kpi 数据文件(xlsx/csv)")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    result = analyze(args.data)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已输出: {out}")


if __name__ == "__main__":
    main()
