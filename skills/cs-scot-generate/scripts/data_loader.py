#!/usr/bin/env python3
"""通用数据加载器:统一支持 .xlsx 与 .csv,自动检测编码。

各指标解析脚本(kpi_analyzer.py / chr_analyzer.py / 未来新增指标)都复用本模块,
这样"读文件"这件事只实现一次,且 xlsx 与 csv 都能吃。
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Dict, List, Tuple, Union

# 候选编码,按顺序尝试(覆盖现网常见的 UTF-8 / 带 BOM / GBK)
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")

PathLike = Union[str, Path]


def _read_text(path: Path) -> str:
    """按候选编码依次尝试读取文本文件,返回解码后的字符串。"""
    raw = path.read_bytes()
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 兜底:忽略非法字节,保证不崩
    return raw.decode("utf-8", errors="ignore")


def load_table(path: PathLike) -> Tuple[List[str], List[Dict[str, str]]]:
    """加载一个表格文件,返回 (表头列表, 行字典列表)。

    - .csv:自动检测编码后用 csv.DictReader 解析;
    - .xlsx / .xlsm / .xls:用 openpyxl 读第一个工作表,首行为表头。
    所有单元格统一转成字符串,类型转换交给下游解析脚本。
    """
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return _load_excel(path)
    return _load_csv(path)


def _load_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """读取 csv 文件。"""
    text = _read_text(path)
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    rows: List[Dict[str, str]] = []
    for r in reader:
        rows.append({(k or ""): ("" if v is None else str(v)) for k, v in r.items()})
    return headers, rows


def _load_excel(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """读取 xlsx 文件(需要 openpyxl)。"""
    try:
        from openpyxl import load_workbook
    except ImportError as e:  # 缺依赖时给出清晰提示
        raise RuntimeError("读取 xlsx 需要 openpyxl,请先 pip install openpyxl") from e

    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        wb.close()
        return [], []
    headers = [("" if h is None else str(h)).strip() for h in header_row]
    rows: List[Dict[str, str]] = []
    for r in rows_iter:
        row: Dict[str, str] = {}
        for i, h in enumerate(headers):
            val = r[i] if i < len(r) else None
            row[h] = "" if val is None else str(val)
        rows.append(row)
    wb.close()
    return headers, rows


def load_json_column(path: PathLike, column: str = "KpiTreeJson") -> List[dict]:
    """加载"每行是一段 JSON 字符串"的文件(如 kpiTree),返回解析后的 dict 列表。

    JSON 内部通常含逗号/引号,不适合按 CSV 拆列,因此对文本文件**按整行解析**:
    跳过表头行,其余每行 json.loads;若行被 CSV 外层引号包裹则先剥掉。
    xlsx 则取每行第一个非空单元格当作 JSON。非法行跳过(适配人工导出的偶发脏行)。
    """
    path = Path(path)
    out: List[dict] = []

    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        _, rows = load_table(path)
        for row in rows:
            raw = (row.get(column) or next((v for v in row.values() if v), "") or "").strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                continue
        return out

    text = _read_text(path)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # 跳过表头:首行等于列名,或首行不是以 { 开头(说明是表头而非 JSON)
    start = 0
    if lines and (lines[0].startswith(column) or not lines[0].lstrip().startswith("{")):
        start = 1
    for ln in lines[start:]:
        s = ln.strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':  # 去掉 CSV 外层引号
            s = s[1:-1].replace('""', '"')
        try:
            out.append(json.loads(s))
        except (json.JSONDecodeError, ValueError):
            continue
    return out
