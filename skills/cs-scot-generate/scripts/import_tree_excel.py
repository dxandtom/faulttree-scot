#!/usr/bin/env python3
"""调优表(<短名>.tuning.xlsx)→ 机械编译回场景产物:tree_index / tree md / solutions.json /
各 <指标>_analyzer.py 的 CONDITIONS / plan.json,全程零模型参与、确定性、可重复。

设计要点(每条都是防"人改坏"的):
- **node_id 只认不改**:已有 id 原样保留;新增行留空,按 max+1 自动分配(历史 id 不复用——
  连已删节点的 id 也不复用,防外部引用错位);改已有 id / id 重复 = 硬拦。
- **先全量校验、后写盘**:任何一处错(JSON 坏/id 重复/上级对不上/类别不认识)都收集全、
  按 Excel 行号用人话报出来,一个文件都不写——绝不留半成品产物。
- **新增数据源列**(绑定:<新源>)自动落地:克隆现有 analyzer 换 INDICATOR、写 CONDITIONS、
  plan.json 增加该指标步骤;某源绑定清空则从 plan 摘除(脚本保留)。
- **变更摘要**:导入成功后打印 节点增/删/改名、判据改动、各源绑定增删改——每次导入可评审。

用法:import_tree_excel.py --out <场景产物目录(或打包技能的 assets/)>
本脚本自包含(不依赖 generate 技能的其它模块),会随产物一起分发。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

_LVL_RE = re.compile(r"^L(\d+)(?:\(Chinese\))?$")
_NID_RE = re.compile(r"^n\d+$")
_CLASSES = set("ABCDEFGHIJ")
_DECISIONS = {"insufficient_data", "manual", "not_applicable"}
_CURLY = {"“": '"', "”": '"', "‘": "'", "’": "'"}


# ---------- 与生成侧同一套的机械件(内嵌副本,保持自包含) ----------
def _infer_combine(method):
    if not method:
        return None
    if re.search(r"(满足.{0,6}(一个|其一|任一|其中一)|任选其一|至少一)", method):
        return "OR"
    if re.search(r"(同时满足|需同时|均满足|且|&)", method):
        return "AND"
    return "unknown"


def _index_to_markdown(index):
    nodes, lines = index["nodes"], []

    def rec(nid, depth):
        nd = nodes[nid]
        lines.append("#" * depth + " " + nd["name"])
        if nd.get("condition"):
            lines.extend(nd["condition"].split("\n"))
        for tag, v in (nd.get("meta") or {}).items():
            lines.append(f"（{tag}）{v}")
        for ch in nd["children"]:
            rec(ch, depth + 1)

    if index.get("root"):
        rec(index["root"], 1)
    return "\n".join(lines) + "\n"


def _write_conditions(analyzer: Path, entries):
    """ast 定位 CONDITIONS 赋值块整体替换 + 写后语法自检(与 merge_conditions 同款)。"""
    src = analyzer.read_text(encoding="utf-8")
    tree = ast.parse(src)
    span = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in node.targets):
            span = (node.lineno, node.end_lineno)
            break
    if span is None:
        return f"{analyzer.name}: 找不到 CONDITIONS 赋值块"
    block = "CONDITIONS = [\n" + "".join(f"    {e!r},\n" for e in entries) + "]"
    lines = src.splitlines(keepends=True)
    new_src = "".join(lines[:span[0] - 1]) + block + "\n" + "".join(lines[span[1]:])
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        return f"{analyzer.name}: 写入后语法自检失败:{e}"
    analyzer.write_text(new_src, encoding="utf-8")
    return None


def _read_conditions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in node.targets):
            return ast.literal_eval(node.value)
    return []


def _parse_cell_json(raw, where, errors, warns):
    """绑定格 JSON 解析:中文引号先修一轮再试(业务同事最常见的手误),修好算过但提醒。"""
    txt = str(raw).strip()
    if not txt:
        return None
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        fixed = txt
        for k, v in _CURLY.items():
            fixed = fixed.replace(k, v)
        try:
            val = json.loads(fixed)
            warns.append(f"{where}: 用了中文引号(“ ” ‘ ’),本次已自动纠正——下次请用英文引号 \" '")
            return val
        except json.JSONDecodeError as e:
            errors.append(f"{where}: 绑定格式不对(JSON 解析失败:{e.msg},第{e.lineno}行第{e.colno}列)。"
                          "常见原因:引号用了中文引号、少了逗号或括号。照着 tuning-guide.md 的模板改。")
            return None


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="调优表 → 机械编译回场景产物")
    ap.add_argument("--out", required=True, help="场景产物目录(或打包技能的 assets/ 目录)")
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve()

    xls = sorted((out / "tree").glob("*.tuning.xlsx"))
    tis = sorted((out / "tree").glob("*.tree_index.json"))
    if len(xls) != 1 or len(tis) != 1:
        raise SystemExit(f"tree/ 下应各有一份调优表与 tree_index(现有 {len(xls)} / {len(tis)} 份)")
    old_index = json.loads(tis[0].read_text(encoding="utf-8"))
    stem = tis[0].name[: -len(".tree_index.json")]

    from openpyxl import load_workbook
    ws = load_workbook(xls[0], data_only=True).active
    rows = [[("" if c is None else str(c)).strip() for c in r] for r in ws.iter_rows(values_only=True)]
    if not rows:
        raise SystemExit("调优表是空的")
    header, body = rows[0], rows[1:]

    # ---- 解析表头 ----
    lvl_col, tag_col, src_col = {}, {}, {}
    id_col = sol_zh = sol_en = meta_col = None
    for i, h in enumerate(header):
        m = _LVL_RE.match(h)
        if h == "node_id":
            id_col = i
        elif m:
            lvl_col[int(m.group(1))] = i
        elif h.startswith("判据:") or h.startswith("判据:"):
            tag_col[h.split(":", 1)[-1].split(":", 1)[-1]] = i
        elif h.startswith("绑定:") or h.startswith("绑定:"):
            src_col[h.split(":", 1)[-1].split(":", 1)[-1]] = i
        elif h == "Recommend Solution(Chinese)":
            sol_zh = i
        elif h == "Recommend Solution(English)":
            sol_en = i
        elif h == "meta(勿改)":
            meta_col = i
    if id_col is None or not lvl_col:
        raise SystemExit("表头不合格:缺 node_id 列或层级列(L2(Chinese) 这种)——别删表头行/表头列")

    levels = sorted(lvl_col)
    errors, warns = [], []
    nodes, order, solutions = {}, [], {}
    binds = {s: [] for s in src_col}                      # 源 → [(row_idx, nid占位, cell对象列表)]
    stack = {}                                            # 层级 → (nid, name)
    seen_ids, blank_rows = set(), []

    for ri, row in enumerate(body):
        rowno = ri + 2                                    # Excel 行号(1-based + 表头)
        def cell(i):
            return row[i] if (i is not None and i < len(row)) else ""
        filled = [(l, cell(ci)) for l, ci in sorted(lvl_col.items()) if cell(ci)]
        if not filled:
            continue
        lvl, name = filled[-1]
        # 上级各层必须照抄且与上文一致(防插错位置/上级改名漏改)
        parent = None
        if filled[:-1]:
            anc_ok = True
            for al, aname in filled[:-1]:
                st = stack.get(al)
                if not st or st[1] != aname:
                    errors.append(f"第{rowno}行「{name}」: 上级「{aname}」(L{al})和上文对不上——"
                                  "新行要插在它上级的块里面,并把上级各层的名字照抄")
                    anc_ok = False
                    break
            if anc_ok:
                parent = stack[filled[-2][0]][0]
        nid = cell(id_col)
        if nid:
            if not _NID_RE.match(nid):
                errors.append(f"第{rowno}行「{name}」: node_id「{nid}」格式不对(应为 n+数字,新增行请留空)")
            elif nid in seen_ids:
                errors.append(f"第{rowno}行「{name}」: node_id「{nid}」重复了——新增行 node_id 要留空,别复制别的行的")
            seen_ids.add(nid)
        else:
            blank_rows.append(ri)
        key = nid or f"__new__{ri}"
        cond_parts = [(t, cell(ci)) for t, ci in tag_col.items() if cell(ci)]
        condition = "\n".join(f"[{t}] {v}" for t, v in cond_parts) or None
        meta = {}
        if cell(meta_col):
            meta = _parse_cell_json(cell(meta_col), f"第{rowno}行「{name}」meta 列", errors, warns) or {}
        nodes[key] = {"name": name, "parent": parent, "level": lvl, "code": None,
                      "children": [], "condition": condition,
                      "combine": _infer_combine(condition),
                      "data_source": {t: v for t, v in cond_parts},
                      "pass_through": not condition, "sheets": ["tuning"], "meta": meta}
        order.append(key)
        if parent:
            nodes[parent]["children"].append(key)
        stack = {l: v for l, v in stack.items() if l < lvl}
        stack[lvl] = (key, name)
        if cell(sol_zh) or cell(sol_en):
            solutions[key] = {k: v for k, v in (("zh", cell(sol_zh)), ("en", cell(sol_en))) if v}
        for s, ci in src_col.items():
            val = _parse_cell_json(cell(ci), f"第{rowno}行「{name}」绑定:{s}", errors, warns)
            if val is None:
                continue
            items = val if isinstance(val, list) else [val]
            for b in items:
                if not isinstance(b, dict):
                    errors.append(f"第{rowno}行「{name}」绑定:{s}: 每条绑定要是 {{...}} 对象")
                    continue
                cls, dec = str(b.get("class", "")).upper(), b.get("decision")
                if dec is not None and dec not in _DECISIONS:
                    errors.append(f"第{rowno}行「{name}」绑定:{s}: decision「{dec}」不认识"
                                  f"(只认 {'/'.join(sorted(_DECISIONS))})")
                elif dec is None and cls not in _CLASSES:
                    errors.append(f"第{rowno}行「{name}」绑定:{s}: class「{b.get('class')}」不认识"
                                  "(A–J 之一;查不了就写 decision,模板见 tuning-guide.md)")
            binds[s].append((ri, key, items))

    roots = [k for k in order if nodes[k]["parent"] is None]
    if len(roots) != 1:
        errors.append(f"树应恰有一个根,现有 {len(roots)} 个顶层节点:{[nodes[k]['name'] for k in roots]}"
                      "——检查是不是有行的上级各层忘了照抄")
    if errors:
        print(f"不合格:发现 {len(errors)} 处问题,**没有改动任何文件**。逐条修完重跑:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(2)

    # ---- 分配新 id(历史 id 不复用:取 表内∪旧index 的最大号+1)----
    used = {int(k[1:]) for k in seen_ids if _NID_RE.match(k)}
    used |= {int(k[1:]) for k in (old_index.get("nodes") or {}) if _NID_RE.match(k)}
    nxt = max(used, default=0) + 1
    rename = {}
    for ri in blank_rows:
        key = f"__new__{ri}"
        rename[key] = f"n{nxt}"
        nxt += 1
    def rid(k):
        return rename.get(k, k)
    new_nodes = {}
    for k in order:
        nd = dict(nodes[k])
        nd["parent"] = rid(nd["parent"]) if nd["parent"] else None
        nd["children"] = [rid(c) for c in nd["children"]]
        new_nodes[rid(k)] = nd
    index = {"scenario": old_index.get("scenario"), "root": rid(roots[0]), "nodes": new_nodes}
    if old_index.get("slug"):
        index["slug"] = old_index["slug"]
    solutions = {rid(k): v for k, v in solutions.items()}

    # ---- 变更摘要(对旧产物)----
    old_nodes = old_index.get("nodes") or {}
    added = [rid(k) for k in order if rid(k) not in old_nodes]
    removed = [k for k in old_nodes if k not in new_nodes]
    renamed = [k for k in new_nodes if k in old_nodes and old_nodes[k].get("name") != new_nodes[k]["name"]]
    cond_chg = [k for k in new_nodes if k in old_nodes and (old_nodes[k].get("condition") or None) != new_nodes[k]["condition"]]

    # ---- 写盘(校验全过才走到这)----
    tis[0].write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "tree" / f"{stem}.md").write_text(_index_to_markdown(index), encoding="utf-8")
    if solutions:
        (out / "tree" / "solutions.json").write_text(
            json.dumps(solutions, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        (out / "tree" / "solutions.json").unlink(missing_ok=True)

    existing = sorted((out / "scripts").glob("*_analyzer.py"))
    if not existing:
        raise SystemExit("产物 scripts/ 下没有任何 analyzer,无法落绑定(产物不完整)")
    summary_src = []
    for s, entries in binds.items():
        full = []
        for _ri, key, items in entries:
            nid = rid(key)
            nd = new_nodes[nid]
            for b in items:
                full.append({"id": f"{s}-{nid}", "node": nd["name"], "node_id": nid,
                             "condition_text": nd["condition"] or nd["name"], "source": s, **b})
        ap_path = out / "scripts" / f"{s}_analyzer.py"
        if not ap_path.exists():                          # 新增数据源:克隆现有 analyzer,换 INDICATOR
            tpl = existing[0].read_text(encoding="utf-8")
            tpl = re.sub(r'(?m)^INDICATOR = .*$', f'INDICATOR = "{s}"', tpl, count=1)
            ap_path.write_text(tpl, encoding="utf-8")
        before = len(_read_conditions(ap_path))
        err = _write_conditions(ap_path, full)
        if err:
            raise SystemExit(f"写绑定失败:{err}")
        summary_src.append(f"{s}: {before}→{len(full)} 条")

    # ---- plan / manifest:指标 = 有绑定的源(表列顺序);清空的源摘除 ----
    indicators = [s for s in src_col if any(it for _r, _k, it in binds[s])]
    plan_p = out / "plan.json"
    if plan_p.exists() and indicators:
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        pnodes = []
        for i, ind in enumerate(indicators):
            nxt_id = indicators[i + 1] if i + 1 < len(indicators) else "rca"
            pnodes.append({"id": ind, "kind": "script", "name": f"{ind}_analyzer",
                           "purpose": f"读 {ind} 数据,逐条件统计出事实(条件已在生成脚本里按 A–J 模板算好)",
                           "inputs": {"data": f"$input.{ind}"},
                           "outputs": {"result": f"$memory.{ind}_result"},
                           "transition": {"next_on_success": nxt_id}})
        rca = next((n for n in plan.get("nodes", []) if n.get("id") == "rca"), None) or {
            "id": "rca", "kind": "agent_skill", "name": "fault-tree-rca",
            "purpose": "综合根因分析", "outputs": {"result": "$output.final_answer"},
            "transition": {"end": True}}
        rca["inputs"] = {"fault_tree_md": "$memory.fault_tree_md",
                         **{ind: f"$memory.{ind}_result" for ind in indicators}}
        plan["nodes"] = pnodes + [rca]
        plan["entry"] = indicators[0]
        plan_p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    mf_p = out / "manifest.json"
    if mf_p.exists():
        mf = json.loads(mf_p.read_text(encoding="utf-8"))
        mf["indicators"] = indicators
        mf_p.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")

    for w in warns:
        print(f"⚠ {w}")
    def _nm(k, pool):
        return f"{k}「{(pool.get(k) or {}).get('name', '')}」"

    print(f"导入完成:节点 {len(new_nodes)} 个。变更摘要:")
    print(f"  新增节点 {len(added)}: {', '.join(_nm(k, new_nodes) for k in added) or '无'}")
    print(f"  删除节点 {len(removed)}: {', '.join(_nm(k, old_nodes) for k in removed) or '无'}")
    print(f"  改名 {len(renamed)}: "
          + (", ".join(f"{k}: {old_nodes[k].get('name')}→{new_nodes[k]['name']}" for k in renamed) or "无"))
    print(f"  判据改动 {len(cond_chg)}: {', '.join(_nm(k, new_nodes) for k in cond_chg) or '无'}")
    print(f"  绑定: {'; '.join(summary_src) or '无'}")
    print("下一步:重新打包/上传前,建议用真实数据跑一遍 runner 验证。")


if __name__ == "__main__":
    main()
