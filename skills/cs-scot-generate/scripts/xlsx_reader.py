#!/usr/bin/env python3
"""xlsx 最小读取器(zipfile + xml.etree,**零三方依赖**,不需要 openpyxl)。

本仓所有要**读** xlsx 的地方(概览 analyzer / 内核 condition_kernels / 建树 excel_to_tree(_v2) /
调优表导入 import_tree_excel)都从这里读——**同一份数据在各环节必须读出同样的值**,否则
"概览看到的列值"和"逐条件判定用的值"会悄悄不一致,RCA 结论跟着脏,这类 bug 极难查。
(写 xlsx——调优表导出/增删节点——仍走 openpyxl:写是生成/迁移期的开发动作,读是诊断现场的运行动作,
只有读必须零依赖。)

xlsx 就是个 zip:
  xl/workbook.xml            页签名 + 活动页(靠 xl/_rels/workbook.xml.rels 映射到真实文件)
  xl/worksheets/sheetN.xml   单元格(**空单元格会被整个跳过**,必须靠 r="B3" 定位列)
  xl/sharedStrings.xml       文本抽出来按下标引用(t="s");富文本会拆成多个 <r><t>
  xl/styles.xml              日期不是日期,是数字;靠数字格式(numFmt)才认得出来

对外只有 6 个函数:
  is_xlsx(p) / list_sheets(p) / sheet_briefs(p) / read_xlsx(p, sheet=None) /
  read_rows(p, sheet=None) / iter_sheets(p)
read_rows 是"位置形态"(表头行+数据行都是 list,不建 dict):**重复表头不吞**,给要校验
"表头有两个 L3 列"这类问题的调用方(import_tree_excel)用;其余场景用 dict 形态即可。

不支持旧版 .xls(二进制 BIFF,不是 zip;openpyxl 其实也不支持)→ 抛 ValueError,请另存为 .xlsx。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
# 内置的日期/时间数字格式号(含中日韩那几套):命中就把序列号还原成日期串。
_DATE_FMT_IDS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))
_EXCEL_EPOCH = datetime(1899, 12, 30)  # 1900 历,且要抵消 Excel 那个 1900-02-29 的历史 bug

EXCEL_EXT = (".xlsx", ".xlsm", ".xls")


def is_xlsx(path) -> bool:
    return Path(path).suffix.lower() in EXCEL_EXT


# ---------- 单元格 ----------
def _text(el) -> str:
    """<si>/<is> 的文本:直接 <t>,或富文本多个 <r><t>;<rPh>(注音)不算。
    单元格局部加粗/变色 = 富文本,漏了 <r> 会**丢字**。"""
    if el is None:
        return ""
    parts = []
    for child in el:
        if child.tag == _NS + "t":
            parts.append(child.text or "")
        elif child.tag == _NS + "r":
            for run in child:
                if run.tag == _NS + "t":
                    parts.append(run.text or "")
    return "".join(parts)


def _col_index(ref: str) -> int:
    """单元格引用 'BC12' → 列下标 54(0-based)。**必须靠它定位**:xlsx 跳过空单元格,
    按出现顺序 enumerate 会让空值后面的列整体错位。"""
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def _serial_to_text(raw: str) -> Optional[str]:
    """Excel 日期序列号 → 'YYYY-MM-DD [HH:MM:SS]';不像日期就返回 None。"""
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if f < 1 or f > 2958466:  # 1900-01-01 ~ 9999-12-31 之外的当普通数值
        return None
    dt = _EXCEL_EPOCH + timedelta(seconds=round(f * 86400))  # 按秒取整,否则 08:30:00 会掉成 08:29:59
    return dt.strftime("%Y-%m-%d %H:%M:%S" if (dt.hour or dt.minute or dt.second) else "%Y-%m-%d")


def _num_text(raw: str) -> str:
    """数值文本归一:xlsx 里 99.1 常存成 '99.09999999999999',openpyxl 转 float 再 str 会得到 '99.1'。
    这里对齐:整数原样返回(大整数 ID/编码不过 float、不丢精度),小数走最短往返表示。"""
    s = str(raw).strip()
    if re.fullmatch(r"-?\d+", s):
        return s
    try:
        return repr(float(s))
    except ValueError:
        return raw


def _cell(c, shared, date_styles) -> str:
    """一个 <c> → 字符串值。t='s' 共享字符串 / inlineStr 内联 / b 布尔 / e 错误 / 其余数值或公式缓存。
    公式单元格取 <v>(缓存值),等价于 openpyxl 的 data_only=True。"""
    t = c.get("t")
    if t == "inlineStr":
        return _text(c.find(_NS + "is"))
    v = c.find(_NS + "v")
    if v is None or v.text is None:
        return _text(c.find(_NS + "is"))  # 少数没标 t 的内联串
    raw = v.text
    if t == "s":
        try:
            i = int(raw)
        except ValueError:
            return raw
        return shared[i] if 0 <= i < len(shared) else raw
    if t == "b":
        return "TRUE" if raw.strip() in ("1", "true", "TRUE") else "FALSE"
    if t in ("e", "str"):  # #N/A 等错误值、公式返回的字符串
        return raw
    try:
        s = int(c.get("s") or -1)
    except ValueError:
        s = -1
    if s in date_styles:
        return _serial_to_text(raw) or raw
    return _num_text(raw)


# ---------- 工作簿 ----------
def _open(path) -> zipfile.ZipFile:
    p = Path(path)
    if p.suffix.lower() == ".xls":
        raise ValueError(f"不支持旧版 .xls 二进制格式:{p.name};请另存为 .xlsx 后再跑。")
    try:
        return zipfile.ZipFile(p)
    except zipfile.BadZipFile as e:
        raise ValueError(f"{p.name} 不是有效的 xlsx(zip)文件") from e


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except (KeyError, ET.ParseError):
        return []
    return [_text(si) for si in root.findall(_NS + "si")]


def _date_styles(zf: zipfile.ZipFile) -> set:
    """style 下标集合:这些样式的数字格式是日期/时间。没有它,时间列会变成 45292 这种序列号,
    被数值统计当普通数值,量纲全乱。"""
    try:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return set()
    custom = set()
    for nf in root.iter(_NS + "numFmt"):
        code = re.sub(r'\[[^\]]*\]|"[^"]*"', "", nf.get("formatCode") or "")  # 去掉 [红色]、"年" 之类
        if any(c in code for c in "ymdhs"):
            try:
                custom.add(int(nf.get("numFmtId")))
            except (TypeError, ValueError):
                pass
    out = set()
    xfs = root.find(_NS + "cellXfs")
    if xfs is None:
        return out
    for i, xf in enumerate(xfs.findall(_NS + "xf")):
        try:
            fid = int(xf.get("numFmtId") or 0)
        except ValueError:
            continue
        if fid in _DATE_FMT_IDS or fid in custom:
            out.add(i)
    return out


def _sheets(zf: zipfile.ZipFile) -> Tuple[List[Tuple[str, str]], int]:
    """[(页签名, zip 内路径)...](保持工作簿顺序)+ 活动页签下标。"""
    root = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = {}
    try:
        for r in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")):
            rels[r.get("Id")] = r.get("Target") or ""
    except (KeyError, ET.ParseError):
        pass
    names = set(zf.namelist())
    entries: List[Tuple[str, str]] = []
    for sh in root.iter(_NS + "sheet"):
        target = rels.get(sh.get(_NS_REL + "id"), "")
        path = target.lstrip("/").replace("../", "")
        if not path.startswith("xl/"):
            path = "xl/" + path
        if path not in names:  # rels 缺失/异常时按顺序推断
            path = f"xl/worksheets/sheet{len(entries) + 1}.xml"
        entries.append((sh.get("name") or f"Sheet{len(entries) + 1}", path))
    active = 0
    view = root.find(f"{_NS}bookViews/{_NS}workbookView")
    if view is not None:
        try:
            active = int(view.get("activeTab") or 0)
        except ValueError:
            active = 0
    if not 0 <= active < len(entries):
        active = 0
    return entries, active


def _iter_rows(zf: zipfile.ZipFile, sheet_path: str, shared, date_styles) -> Iterator[List[str]]:
    """流式产出每行(list[str]),按 r 属性把空单元格补回原位。iterparse 逐行 clear,不整张进内存。"""
    with zf.open(sheet_path) as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag != _NS + "row":
                continue
            cells, width = {}, 0
            for i, c in enumerate(el.findall(_NS + "c")):
                ref = c.get("r") or ""
                idx = _col_index(ref) if (ref and ref[0].isalpha()) else i
                cells[idx] = _cell(c, shared, date_styles)
                width = max(width, idx + 1)
            yield [cells.get(i, "") for i in range(width)]
            el.clear()


def _header_of(row: List[str]) -> List[str]:
    header = [str(h).strip() for h in row]
    while header and header[-1] == "":  # 砍掉尾部空列,别造出 "" 这种列名
        header.pop()
    return header


def _rows_as_dicts(header: List[str], it: Iterator[List[str]]) -> List[Dict[str, str]]:
    return [{header[i]: (r[i] if i < len(r) else "") for i in range(len(header))} for r in it]


def _sheet_path(entries, active, sheet: Optional[str]) -> str:
    by_name = dict(entries)
    if sheet is not None:
        if sheet not in by_name:
            raise ValueError(f"页签「{sheet}」不存在;可用页签:{[n for n, _ in entries]}")
        return by_name[sheet]
    return entries[active][1]


# ---------- 对外 ----------
def list_sheets(path) -> List[str]:
    """页签名列表(工作簿顺序)。"""
    with _open(path) as zf:
        entries, _ = _sheets(zf)
    return [n for n, _ in entries]


def sheet_briefs(path) -> List[Dict]:
    """每个页签只取 表头 + 行数(不建 dict、不整张进内存):[{name, total_records, columns}]。"""
    out = []
    with _open(path) as zf:
        entries, _ = _sheets(zf)
        shared, styles = _shared_strings(zf), _date_styles(zf)
        for name, sheet_path in entries:
            it = _iter_rows(zf, sheet_path, shared, styles)
            header = _header_of(next(it, []))
            n = sum(1 for _ in it)
            out.append({"name": name, "total_records": n, "columns": header})
    return out


def read_xlsx(path, sheet: Optional[str] = None) -> Tuple[List[str], List[Dict[str, str]]]:
    """→ (header, rows[dict])。sheet 给页签名则取该页签(不存在 → ValueError),否则取活动页。"""
    with _open(path) as zf:
        entries, active = _sheets(zf)
        if not entries:
            return [], []
        it = _iter_rows(zf, _sheet_path(entries, active, sheet), _shared_strings(zf), _date_styles(zf))
        header = _header_of(next(it, []))
        return header, _rows_as_dicts(header, it)


def read_rows(path, sheet: Optional[str] = None) -> List[List[str]]:
    """位置形态:全部行(含表头行)按 list[str] 原样给,值不 strip、重复表头不吞、不建 dict——
    调优表导入的表头校验(重复列/错位列要用易懂的话点名)必须拿到原始位置信息,dict 会把重复列悄悄合并。"""
    with _open(path) as zf:
        entries, active = _sheets(zf)
        if not entries:
            return []
        it = _iter_rows(zf, _sheet_path(entries, active, sheet), _shared_strings(zf), _date_styles(zf))
        return list(it)


def iter_sheets(path) -> List[Tuple[str, List[str], List[Dict[str, str]]]]:
    """全部页签 → [(页签名, 表头, 行dict列表)]。多页签故障树(v2)用。"""
    out = []
    with _open(path) as zf:
        entries, _ = _sheets(zf)
        shared, styles = _shared_strings(zf), _date_styles(zf)
        for name, sheet_path in entries:
            it = _iter_rows(zf, sheet_path, shared, styles)
            header = _header_of(next(it, []))
            out.append((name, header, _rows_as_dicts(header, it)))
    return out
