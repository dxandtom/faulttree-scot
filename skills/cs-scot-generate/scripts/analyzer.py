#!/usr/bin/env python3
"""通用解析引擎(指标无关)——只查数据、摆事实,绝不下判断。

所有真实数据都是**带表头的表**(CHR、kpiData 皆然),逐字段数据驱动统计:
- 数值列(>=90% 可转数)→ count/min/max/mean + **十分位 p0..p100** + nulls;
- 类别列 → top-N 计数 + distinct + 每个 top 值几个样例标识 + nulls。

绝不输出 hit / judgment / conditions:是否成立、根因、以及"故障树的量 ↔ 数据列"的映射,
全部由 fault-tree-rca 拿着故障树去做(且映射要按**语义**、显式可审,见 rca_rules.md)。
本引擎只产**事实**。

自包含:不 import 任何本仓自定义模块。读 xlsx 需 openpyxl;csv 用标准库。
用法:python analyzer.py <数据文件> -o <输出.json> [--indicator <标签>] [--fault-tree <故障树.md>]
给了 --fault-tree(或脚本旁存在唯一的 ../tree/*.md)就尝试**只保留故障树提到的列**以压缩输出;
但树常用英文/code 名、数据列常为中文,纯子串命中过稀时**保留全部列**,把字段↔列映射交给 RCA 语义完成。
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
# 空值/缺测标记:含 kpiData 常见的 NIL(大小写不敏感)。NIL 视为缺测,绝不当一个类别值。
_NULLISH = {"", "na", "n/a", "null", "none", "nil", "-"}
# 用作样例标识的列(挑第一个存在的):优先调用/小区/网元一类的标识列。
_ID_CANDIDATES = ("CallID", "CALLID", "Call_Id", "id", "ID", "Index",
                  "cellName", "CellName", "Object", "ObjectName", "neName", "NE")


# ---------- 读取 ----------
def _decode(raw: bytes) -> str:
    """字节流按候选编码解码,全失败则忽略错误兜底。"""
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _sheets(path: str):
    """xlsx 页签名列表;csv → []。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=True, data_only=True)
        names = list(wb.sheetnames)
        wb.close()
        return names
    return []


def _load(path: str, sheet=None):
    """读数据 → (header, rows)。xlsx 多页签:sheet 给页签名选表,不给则活动页。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise RuntimeError("读取 xlsx 需要 openpyxl,请先 pip install openpyxl") from e
        wb = load_workbook(p, read_only=True, data_only=True)
        ws = wb[sheet] if (sheet is not None and sheet in wb.sheetnames) else wb.active
        it = ws.iter_rows(values_only=True)
        header = [("" if h is None else str(h)).strip() for h in next(it, [])]
        rows = [{header[i]: (("" if r[i] is None else str(r[i])) if i < len(r) else "")
                 for i in range(len(header))} for r in it]
        wb.close()
        return header, rows

    text = _decode(p.read_bytes())
    reader = csv.DictReader(io.StringIO(text))
    header = list(reader.fieldnames or [])
    rows = [{(k or ""): ("" if v is None else str(v)) for k, v in row.items()} for row in reader]
    return header, rows


# ---------- 通用统计 ----------
def _to_num(v):
    """尽量转数值(去掉末尾 %);空值/缺测/失败返回 None。"""
    s = str(v).strip().rstrip("%")
    if s.lower() in _NULLISH:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _nonempty(values):
    return [v for v in values if str(v).strip().lower() not in _NULLISH]


def _pct(sorted_nums, q):
    """sorted_nums 的第 q 百分位(线性插值);空列表返回 None。"""
    n = len(sorted_nums)
    if n == 0:
        return None
    if n == 1:
        return sorted_nums[0]
    pos = (n - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_nums[lo] * (1 - frac) + sorted_nums[hi] * frac


def _stat_values(values, samples_for=None):
    """对一列做统计:数值(>=90% 可转数)给摘要 + 十分位,否则类别给 top-N。按值判别,不看名字。
    nulls = 该列空值/缺测(含 NIL)的条数——让"缺测"显式可见,RCA 才不会把它当"正常/不成立"。"""
    total = len(values)
    ne = _nonempty(values)
    nulls = total - len(ne)
    if not ne:
        return {"kind": "empty", "count": 0, "nulls": nulls}
    nums = [_to_num(v) for v in ne]
    ok = [n for n in nums if n is not None]
    if len(ok) >= max(1, int(0.9 * len(ne))):  # 绝大多数能转数 → 数值列
        s = sorted(ok)
        return {"kind": "numeric", "count": len(ok), "nulls": nulls,
                "min": round(s[0], 4), "max": round(s[-1], 4),
                "mean": round(sum(ok) / len(ok), 4),
                # 十分位:树里 "RSRP < -120"、"成功率 < 85%" 这类阈值条件,RCA 可据此估"多少在阈值一侧"。
                "deciles": {f"p{q}": round(_pct(s, q), 4) for q in range(0, 101, 10)}}
    cnt = Counter(str(v).strip() for v in ne)
    top = dict(cnt.most_common(20))
    out = {"kind": "categorical", "count": len(ne), "nulls": nulls,
           "distinct": len(cnt), "top": top}
    if samples_for is not None:  # 给每个 top 值附几个样例标识
        out["samples"] = {val: samples_for(val)[:3] for val in list(top)[:8]}
    return out


# ---------- 表格统计 ----------
def _analyze_tabular(header, rows, indicator) -> dict:
    """逐列统计;不写死字段,不投影故障树(投影/映射交给 RCA)。"""
    id_col = next((c for c in _ID_CANDIDATES if c in header), None)

    def make_sampler(col):
        def sampler(val):
            return [str(rows[i].get(id_col) if id_col else f"row#{i}")
                    for i, r in enumerate(rows) if str(r.get(col, "")).strip() == val]
        return sampler

    field_stats = {}
    for col in header:
        vals = [r.get(col, "") for r in rows]
        field_stats[col] = _stat_values(vals, samples_for=make_sampler(col))
    return {"indicator": indicator, "kind": "tabular",
            "summary": {"total_records": len(rows), "columns": header,
                        "id_column": id_col},
            "field_stats": field_stats}


# ---------- 故障树投影(可选:只保留树提到的列,压缩输出) ----------
def _auto_tree(script_path):
    """产物布局里 scripts/ 与 tree/ 是兄弟;脚本旁若恰有唯一一棵树 md,自动用它投影。"""
    d = Path(script_path).resolve().parent.parent / "tree"
    if d.is_dir():
        mds = sorted(d.glob("*.md"))
        if len(mds) == 1:
            return str(mds[0])
    return None


def _project(result: dict, tree_text: str) -> dict:
    """尝试只保留故障树文本**子串命中**的列(树写 `DCErrType`、CHR 列也叫 `DCErrType` 时有效,大幅压缩)。
    但树常用英文/code 名(`SUSRLastAvgRsrp`),数据列常为中文(`上行RSSI均值`)——子串会几乎全落空。
    命中过稀(<3 列或 <10%)时**保留全部列**,字段↔列映射交给 RCA 按语义显式完成,避免把数据投空、害 RCA 瞎判。"""
    def mentioned(name) -> bool:
        s = str(name or "")
        return len(s) >= 3 and s in tree_text

    fs = result.get("field_stats", {})
    ref = {k: v for k, v in fs.items() if mentioned(k)}
    if len(ref) < max(3, int(0.1 * len(fs))):  # 命中过稀 → 跨命名,保留全部
        result["projection"] = {
            "by": "fault_tree", "mode": "kept_all_fallback",
            "matched_fields": len(ref), "total_fields": len(fs),
            "note": "子串命中过少(疑似树用英文/code 名、数据列为中文等跨命名);"
                    "已保留全部列,字段↔列映射由 RCA 按语义显式完成(须记录 mapping + 理由,找不到就数据不足)。"}
    else:
        result["field_stats"] = ref
        result["projection"] = {"by": "fault_tree", "mode": "substring",
                                "kept_fields": len(ref), "total_fields": len(fs)}
    return result


# ---------- 入口 ----------
def _default_indicator(script_path, data_path, override):
    if override:
        return override
    stem = Path(script_path).stem
    if stem.endswith("_analyzer"):
        return stem[: -len("_analyzer")] or Path(data_path).stem
    if stem != "analyzer":
        return stem
    return Path(data_path).stem


def analyze(data_path: str, indicator: str, sheet=None) -> dict:
    header, rows = _load(data_path, sheet)
    return _analyze_tabular(header, rows, indicator)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="通用解析引擎(只查数据、摆事实,不下判断)")
    ap.add_argument("data", help="数据文件(csv/xlsx)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--indicator", help="指标标签(默认从脚本名 <ind>_analyzer 或数据文件名推断)")
    ap.add_argument("--fault-tree", help="故障树 md;给了就尝试只保留树提到的列(命中过稀则保留全部);"
                                         "默认自动探测脚本旁唯一的 ../tree/*.md")
    ap.add_argument("--sheet", help="xlsx 指定页签名(多页签数据用);不给且多页签 → 只列各页签的列(轻量)")
    args = ap.parse_args()
    indicator = _default_indicator(__file__, args.data, args.indicator)
    sheets = _sheets(args.data)
    if len(sheets) > 1 and not args.sheet:
        # 多页签且未指定:轻量列出每个页签的列 + 行数(不跑全量统计,免输出爆炸);
        # 模型据此发现页签,再 --sheet <名> 看某页签详情,并在条件里用 source=<页签名> 指过去。
        briefs = [{"name": s, "total_records": len(_load(args.data, s)[1]),
                   "columns": _load(args.data, s)[0]} for s in sheets]
        result = {"indicator": indicator, "kind": "multisheet_overview", "sheets": briefs,
                  "note": "多页签:此为各页签列清单;某页签详情用 --sheet <名>;条件里 source=<页签名> 指过去。"}
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[analyzer] indicator={indicator} 多页签({len(sheets)}):{[b['name'] for b in briefs]} → {out}")
        return
    result = analyze(args.data, indicator, args.sheet)
    tree_path = args.fault_tree or _auto_tree(__file__)
    if tree_path and Path(tree_path).exists():
        result = _project(result, _decode(Path(tree_path).read_bytes()))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    proj = result.get("projection")
    extra = f" 投影按故障树:{proj.get('mode')}" if proj else ""
    print(f"[analyzer] indicator={indicator} kind={result.get('kind')}{extra} → {out}")


if __name__ == "__main__":
    main()
