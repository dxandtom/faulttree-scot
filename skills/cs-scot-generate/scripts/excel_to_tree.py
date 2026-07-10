#!/usr/bin/env python3
"""故障树 Excel → 层级 Markdown 子树(自包含,无本仓自定义依赖)。

输入 Excel 的约定(沿用现网故障树导出格式):
- 层级列:L1(Chinese)、L2(Chinese) … Ln(Chinese),每列是该层的节点名;
- 判断条件列:Judgment Condition(Chinese),挂在每行最深的那个节点上;
- 值为"不涉及"的判断条件会被跳过。

输出 Markdown:用 #/##/### 表示层级,标题下面紧跟该节点的判断条件正文。

用法(当命令行跑,不要 import):
    python excel_to_tree.py "<故障树.xlsx>" --start-node "<场景名>"
不带 -o 时,会把子树写到统一产物目录 <故障树所在目录>/scot产物/<场景名>/tree/<场景名>.md,
并在末尾打印「本场景产物目录」——后续所有步骤都用它,保证产物每次落在同一处、不再到处乱存。
也可用 -o 显式指定 md 路径、或 --out-base 改产物根目录。

要点:
- 背景业务知识:场景节点通常在 **L2**,所以 `--start-level` 默认 2,`--start-node` 在该层选具体场景;
  万一不在 L2,会自动在其它层找到该场景名并以它为根截取(兜底)。
- `--exclude-pattern` 默认排除噪声节点 `萃取新增节点-.*`;传 `--exclude-pattern ""` 可保留全部。
读 xlsx 需要 openpyxl(pip install openpyxl);csv 用标准库即可。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LEVEL_RE = re.compile(r"^L(\d+)\(Chinese\)$")
_CODE_RE = re.compile(r"^L(\d+) Code$")
_JUDGMENT_HEADER = "Judgment Condition(Chinese)"
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")
# 现网导出里常见的"自动萃取"噪声节点,默认排除(与同事产出 69KB 子树的做法一致)
DEFAULT_EXCLUDE = r"萃取新增节点-.*"
# 背景业务知识:场景节点通常位于第 2 层(L2)
DEFAULT_START_LEVEL = 2
# 默认产物容器目录名:产物目录 = <out-base 或 xlsx 所在目录>/<容器>/<场景名>
DEFAULT_OUT_CONTAINER = "scot产物"


def scenario_out_dir(anchor_dir: Path, scenario: str) -> Path:
    """统一的场景产物目录:<anchor_dir>/scot产物/<场景名>。

    anchor_dir 默认取故障树 xlsx 所在目录(见 main),保证每次都落在固定、可预期的地方。
    """
    return Path(anchor_dir) / DEFAULT_OUT_CONTAINER / scenario


def _read_table(path: str) -> Tuple[List[str], List[Dict[str, str]]]:
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
        rows: List[Dict[str, str]] = []
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


def find_level_columns(headers: List[str]) -> List[Tuple[str, int]]:
    """从表头里找出所有 Ln(Chinese) 层级列,按层级升序返回 (列名, 层级)。"""
    cols: List[Tuple[str, int]] = []
    for h in headers:
        m = _LEVEL_RE.match((h or "").strip())
        if m:
            cols.append((h, int(m.group(1))))
    cols.sort(key=lambda x: x[1])
    return cols


def find_judgment_column(headers: List[str]) -> Optional[str]:
    """找到判断条件列名。"""
    for h in headers:
        if (h or "").strip() == _JUDGMENT_HEADER:
            return h
    return None


def find_code_columns(headers: List[str]) -> Dict[int, str]:
    """从表头找出 "Ln Code" 稳定编码列,返回 {层级: 列名}。"""
    out: Dict[int, str] = {}
    for h in headers:
        m = _CODE_RE.match((h or "").strip())
        if m:
            out[int(m.group(1))] = h
    return out


# HTML 标签白名单(只删这些已知标签,避免把条件里的 < > 比较运算符当成标签吃掉)
_HTML_TAG = re.compile(
    r"</?(?:span|div|p|a|br|hr|img|h[1-6]|table|thead|tbody|tr|td|th|ul|ol|li|"
    r"b|i|u|s|em|strong|font|sub|sup|blockquote|pre|code|section|article|center)\b[^>]*>",
    re.IGNORECASE)
_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")          # markdown 图片
_HTML_ENTITIES = (("&nbsp;", " "), ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
                  ("&ldquo;", "“"), ("&rdquo;", "”"), ("&mdash;", "—"),
                  ("&ndash;", "–"), ("&hellip;", "…"), ("&copy;", "©"),
                  ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))
# pass-through 判据标记:这些不是"真条件",节点应判 pass_through(无条件进入),不当条件
_PASSTHROUGH_JUDGMENTS = {"不涉及", "无条件进入", "无条件", "无判断", "无判断条件"}


def _clean_text(s: Optional[str]) -> str:
    """清掉 HTML 标签/图片/转义实体;**保留 < > 比较运算符**,便于阅读/判断。"""
    s = s or ""
    s = _MD_IMG.sub("", s)            # 先去 markdown 图片
    s = _HTML_TAG.sub("", s)          # 去 HTML 标签(白名单,先于实体解码,避免误吃运算符)
    for k, v in _HTML_ENTITIES:       # 再解码实体(&gt;→> 等;此时已无真标签)
        s = s.replace(k, v)
    s = re.sub(r"[ \t ]+", " ", s)        # 多空格/不换行空格 → 单空格(不动换行)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)     # 去每行首尾空白
    s = re.sub(r"\n{3,}", "\n\n", s)           # 3+ 连续空行 → 1 个空行
    return s.strip()


def _is_skip_judgment(jv: Optional[str]) -> bool:
    """该判断单元格是否应跳过(不当真条件):空、或 pass-through 标记(不涉及/无条件进入…)。"""
    t = _clean_text(jv).rstrip("。.;；、, ")
    return (not t) or t in _PASSTHROUGH_JUDGMENTS


def _split_judgment(cell: Optional[str]) -> Tuple[Optional[str], Dict[str, str]]:
    """把判断条件单元格拆成 (方法正文, 数据源dict)。数据源用于路由提示。"""
    text = _clean_text(cell)
    if not text:
        return None, {}
    src: Dict[str, str] = {}
    for m in re.finditer(r"数据源[（(]([^）)]+)[）)]\s*[:：]\s*([^\n]+)", text):
        src[m.group(1).strip()] = m.group(2).strip()
    mm = re.search(r"判断方法\s*[:：]?\s*", text)
    if mm:
        method = text[mm.end():].strip()
    else:  # 没有"判断方法"标记:去掉数据源行,剩下的当方法
        method = re.sub(r"数据源[（(][^）)]+[）)]\s*[:：][^\n]*\n?", "", text).strip()
    return (method or None), src


def _infer_combine(method: Optional[str]) -> Optional[str]:
    """从方法正文粗略推断本节点多条判据的组合关系;拿不准给 unknown,交模型判。"""
    if not method:
        return None
    if re.search(r"(满足.{0,6}(一个|其一|任一|其中一)|任选其一|至少一)", method):
        return "OR"
    if re.search(r"(同时满足|需同时|均满足|且|&)", method):
        return "AND"
    return "unknown"


def build_tree(rows: List[Dict[str, str]], level_cols: List[Tuple[str, int]],
               start_level: Optional[int] = None, start_node: Optional[str] = None,
               judgment_col: Optional[str] = None,
               exclude_pattern: Optional[str] = None) -> Dict[str, Any]:
    """把扁平的行数据按层级列拼成嵌套树;判断条件挂到每行最深的节点上。

    路径截取规则(给了 start_node 时):
    - 优先在 start_level 层(默认 L2,背景业务知识)匹配场景名;
    - 若该层不是,则在该行任意层找到 start_node 并以它为根(兜底);
    - 都没有则跳过该行。
    """
    tree: Dict[str, Any] = {}
    exclude_re = re.compile(exclude_pattern) if exclude_pattern else None
    col_names = [c for c, _ in level_cols]

    for row in rows:
        full = [(row.get(c) or "").strip() for c in col_names]
        full = [v for v in full if v]
        if not full:
            continue
        if exclude_re and any(exclude_re.search(v) for v in full):
            continue
        judgment = None
        if judgment_col:
            jv = (row.get(judgment_col) or "").strip()
            if not _is_skip_judgment(jv):
                judgment = _clean_text(jv)          # md 也清洗(去 HTML/图片、保留运算符)

        if start_node:
            sl = start_level or 1
            if len(full) >= sl and full[sl - 1] == start_node:
                path = full[sl - 1:]                    # 优先:场景名在 L2
            elif start_node in full:
                path = full[full.index(start_node):]    # 兜底:在别的层找到也认
            else:
                continue
        elif start_level and start_level > 1:
            path = full[start_level - 1:]
        else:
            path = full
        if not path:
            continue

        cur = tree
        for i, node in enumerate(path):
            cur.setdefault(node, {})
            if i == len(path) - 1 and judgment:
                cur[node].setdefault("_judgment_", [])
                if judgment not in cur[node]["_judgment_"]:
                    cur[node]["_judgment_"].append(judgment)
            cur = cur[node]
    return tree


def build_index(rows: List[Dict[str, str]], level_cols: List[Tuple[str, int]],
                code_cols: Dict[int, str], start_level: Optional[int], start_node: Optional[str],
                judgment_col: Optional[str], exclude_pattern: Optional[str]) -> Dict[str, Any]:
    """与 build_tree 同样的截取规则,但产出**扁平权威 index**(给压缩/RCA 用):
    每个节点 = 稳定 id(L-Code 链,缺则用名字链)+ name/parent/level/condition/combine/data_source/pass_through。
    """
    exclude_re = re.compile(exclude_pattern) if exclude_pattern else None
    nodes: Dict[str, Any] = {}
    root_id: Optional[str] = None
    short_map: Dict[str, str] = {}  # code-chain → 短稳定 id(n1,n2,…),同一节点跨行一致

    def _short(chain: str) -> str:
        if chain not in short_map:
            short_map[chain] = "n" + str(len(short_map) + 1)
        return short_map[chain]
    for row in rows:
        triples: List[Tuple[int, str, str]] = []  # (层级, 名字, 编码)
        for col, lvl in level_cols:
            nm = (row.get(col) or "").strip()
            if not nm:
                continue
            cd = (row.get(code_cols.get(lvl, "")) or "").strip()
            triples.append((lvl, nm, cd))
        if not triples:
            continue
        names = [t[1] for t in triples]
        if exclude_re and any(exclude_re.search(n) for n in names):
            continue
        judgment = None
        if judgment_col:
            jv = (row.get(judgment_col) or "").strip()
            if not _is_skip_judgment(jv):
                judgment = jv                       # 原文交给 _split_judgment(内部 _clean_text)
        # 截取(镜像 build_tree)
        if start_node:
            sl = start_level or 1
            if len(triples) >= sl and triples[sl - 1][1] == start_node:
                path = triples[sl - 1:]
            elif start_node in names:
                path = triples[names.index(start_node):]
            else:
                continue
        elif start_level and start_level > 1:
            path = triples[start_level - 1:]
        else:
            path = triples
        if not path:
            continue
        parent_id, id_parts = None, []
        for j, (lvl, nm, cd) in enumerate(path):
            id_parts.append(cd or nm)            # 优先编码,缺则名字
            chain = "/".join(id_parts)
            nid = _short(chain)                  # 短稳定 id(n1,n2,…)= 节点 key
            node = nodes.setdefault(nid, {"name": nm, "parent": parent_id, "level": lvl,
                                          "code": cd or None, "children": [], "condition": None,
                                          "combine": None, "data_source": {}, "pass_through": True})
            if parent_id and nid not in nodes[parent_id]["children"]:
                nodes[parent_id]["children"].append(nid)
            if j == 0:
                root_id = nid
            if j == len(path) - 1 and judgment:
                method, src = _split_judgment(judgment)
                if method:
                    node["condition"] = (node["condition"] + "\n" + method) if node["condition"] else method
                    node["combine"] = _infer_combine(node["condition"])
                    node["pass_through"] = False
                if src:
                    node["data_source"].update(src)
            parent_id = nid
    return {"scenario": start_node, "root": root_id, "nodes": nodes}


def tree_to_markdown(tree: Dict[str, Any], lines: Optional[List[str]] = None,
                     level: int = 1) -> List[str]:
    """把嵌套树渲染成 #/##/### 层级 Markdown,判断条件紧跟在标题下。"""
    if lines is None:
        lines = []
    items = [(k, v) for k, v in tree.items() if k]
    for i, (name, data) in enumerate(items):
        if i > 0 and level == 1:
            lines.append("")
        lines.append("#" * level + " " + name)
        if isinstance(data, dict) and "_judgment_" in data:
            for j in data["_judgment_"]:
                lines.append(j)
        children = {k: v for k, v in data.items() if k != "_judgment_"} if isinstance(data, dict) else {}
        if children:
            tree_to_markdown(children, lines, level + 1)
    return lines


def excel_to_tree_markdown(excel_path: str, output_path: Optional[str] = None,
                           start_level: Optional[int] = None, start_node: Optional[str] = None,
                           exclude_pattern: Optional[str] = None) -> str:
    """主流程:读 Excel → 建树 → 渲染 md,可选写出文件,返回 md 文本。"""
    headers, rows = _read_table(excel_path)
    level_cols = find_level_columns(headers)
    if not level_cols:
        raise ValueError("未找到 Ln(Chinese) 格式的层级列,请检查 Excel 表头")
    judgment_col = find_judgment_column(headers)
    code_cols = find_code_columns(headers)
    tree = build_tree(rows, level_cols, start_level, start_node, judgment_col, exclude_pattern)
    lines = tree_to_markdown(tree)
    if not lines:
        raise ValueError(f"未构建出任何节点。请确认场景名是否准确(--start-node「{start_node}」是否在故障树里)")
    md = "\n".join(lines) + "\n"
    # 同时产权威 index(给压缩/RCA;Code 链当稳定 id),写在 md 旁边
    index = build_index(rows, level_cols, code_cols, start_level, start_node, judgment_col, exclude_pattern)
    if output_path:
        outp = Path(output_path)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(md, encoding="utf-8")
        outp.with_suffix(".tree_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return md


def main() -> None:
    try:  # Windows 控制台中文路径不乱码
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="故障树 Excel → 层级 Markdown 子树")
    ap.add_argument("excel_path", help="故障树 Excel 路径(.xlsx)")
    ap.add_argument("-o", "--output", help="显式指定输出 md 路径(覆盖默认产物目录)")
    ap.add_argument("--out-base", help="产物根目录(默认=故障树 xlsx 所在目录);产物目录 = <out-base>/scot产物/<场景名>")
    ap.add_argument("--start-node", help="场景名;在 start-level 层选定该场景,截取以它为根的子树(强烈建议传)")
    ap.add_argument("--start-level", type=int, default=DEFAULT_START_LEVEL,
                    help=f"场景所在层级,默认 {DEFAULT_START_LEVEL}(背景业务知识:场景通常在 L2)")
    ap.add_argument("--exclude-pattern", default=DEFAULT_EXCLUDE,
                    help=f"正则;任一层级值命中则整行排除。默认 {DEFAULT_EXCLUDE!r};传 \"\" 可保留全部")
    args = ap.parse_args()
    if not args.start_node:
        print("[warn] 未传 --start-node,将导出整棵故障树(可能很大)。建议传场景名只截子树。",
              file=sys.stderr)

    # 解析输出位置:-o 优先;否则按 <out-base 或 xlsx 所在目录>/scot产物/<场景> 自动定位(确定性)。
    out_dir: Optional[Path] = None
    output_path = args.output
    if not output_path and args.start_node:
        anchor = (Path(args.out_base).expanduser() if args.out_base
                  else Path(args.excel_path).expanduser().resolve().parent)
        out_dir = scenario_out_dir(anchor, args.start_node)
        output_path = str(out_dir / "tree" / f"{args.start_node}.md")

    try:
        md = excel_to_tree_markdown(args.excel_path, output_path, args.start_level,
                                    args.start_node, args.exclude_pattern)
    except Exception as e:  # noqa: BLE001 — CLI 边界统一兜底
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    if output_path:
        print(f"已输出子树: {output_path}")
        print(f"已输出权威 index(压缩/RCA 用): {Path(output_path).with_suffix('.tree_index.json')}")
        try:                                             # 与 v2 同款:亮出判据数,0 判据当场喊停
            _idx = json.loads(Path(output_path).with_suffix(".tree_index.json").read_text(encoding="utf-8"))
            _n = sum(1 for nd in (_idx.get("nodes") or {}).values() if nd.get("condition"))
            print(f"节点数 {len(_idx.get('nodes') or {})},带判据节点 {_n}")
            if _n == 0:
                print("⚠⚠ 带判据节点为 0:这棵子树没有任何判断条件,多半是判据列没被识别"
                      "(本脚本只认表头「Judgment Condition(Chinese)」)——**停下检查 Excel 表头,"
                      "别往下绑定**;0 判据的树绑出来的只能是瞎猜。", file=sys.stderr)
        except Exception:  # noqa: BLE001 — 计数只是提示,不阻断
            pass
        if out_dir is not None:
            print(f"本场景产物目录(后续步骤 2-4 都写到这里): {out_dir}")
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
