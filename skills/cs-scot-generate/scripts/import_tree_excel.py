#!/usr/bin/env python3
"""调优表(<短名>.tuning.xlsx)→ 机械编译回场景产物:tree_index / tree md / solutions.json /
各 <指标>_analyzer.py 的 CONDITIONS / plan.json,全程零模型参与、确定性、可重复。

设计要点(每条都是防"人改坏"的,多条来自隔离评审的实测发现):
- **node_id 只认不改**:已有 id 原样保留;新增行留空,按 max+1 自动分配(历史 id 不复用);
  改已有 id、复制 id、**手编一个树里从没有过的 id** = 硬拦。
- **先全量校验、全量备料,最后统一写盘**:任何错(JSON 坏/id 问题/上级不符/该有的列被删)都
  收集全、按 Excel 行号用人话报出来,一个文件都不写;写盘阶段不再有任何可失败的计算。
- **页签按表头找**(含 node_id 的那页),不吃"活动页"的亏(业务同事复制页签留底是常态);
  表头的全角冒号/空格自动归一,重复表头、非法源名硬拦。
- **判据以「判据:<标签>」列或「判据:原文」列为准**(原文列=老格式 v1 树的逐字判据,优先);
- 绑定 JSON 里保留的 id/source/condition_text 覆盖默认值(自定义 id/页签路由/逐字子句不丢);
  code 从旧 tree_index 按 node_id 继承(老产物 code 兼容不丢)。
- **新增数据源列**(绑定:<新源>)自动克隆现有 analyzer 换 INDICATOR 并接进 plan;
  绑定全清空时 plan 同步退化为只剩 rca(与 assemble 空指标行为一致)。
- **变更摘要**:节点增/删/改名/移动、判据/解决方案改动、各源绑定条数——每次导入可评审。

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
_SRC_RE = re.compile(r"^[A-Za-z0-9_]+$")
_CLASSES = set("ABCDEFGHIJ") | {"P"}   # A-J 含封存字母(老产物兼容);P(成对)是现役,漏收过(实测被拦)
_DECISIONS = {"insufficient_data", "manual", "not_applicable"}
_CURLY = {"“": '"', "”": '"', "‘": "'", "’": "'"}
RAW_TAG = "原文"


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


def _replace_conditions(src: str, entries, fname: str):
    """在 analyzer 源码文本里定位 CONDITIONS 赋值块整体替换,返回 (新文本, 错误串)。只备料不写盘。"""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return None, f"{fname}: analyzer 解析失败:{e}"
    span = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in node.targets):
            span = (node.lineno, node.end_lineno)
            break
    if span is None:
        return None, f"{fname}: 找不到 CONDITIONS 赋值块"
    block = "CONDITIONS = [\n" + "".join(f"    {e!r},\n" for e in entries) + "]"
    lines = src.splitlines(keepends=True)
    new_src = "".join(lines[:span[0] - 1]) + block + "\n" + "".join(lines[span[1]:])
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        return None, f"{fname}: 替换后语法自检失败:{e}"
    return new_src, None


def _read_conditions_text(src: str):
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in node.targets):
            return ast.literal_eval(node.value)
    return []


def _read_condition_node_ids(path):
    """从某 analyzer(如 cross_analyzer.py)静态抽 CONDITIONS 里的 node_id 集(联动核对用)。"""
    entries = _read_conditions_text(path.read_text(encoding="utf-8"))
    return [str(e.get("node_id")) for e in entries if isinstance(e, dict) and e.get("node_id")]


def _no_const(name):
    raise ValueError(f"数字「{name}」不合法(NaN/Infinity 不能用,写具体数字)")


def _parse_cell_json(raw, where, errors, warns):
    """绑定格 JSON 解析:中文引号先修一轮再试(业务同事最常见的手误),修好算过但提醒。"""
    txt = str(raw).strip()
    if not txt:
        return None
    try:
        return json.loads(txt, parse_constant=_no_const)
    except ValueError as e:
        fixed = txt
        for k, v in _CURLY.items():
            fixed = fixed.replace(k, v)
        if fixed != txt:
            try:
                val = json.loads(fixed, parse_constant=_no_const)
                warns.append(f"{where}: 用了中文引号(“ ” ‘ ’),本次已自动纠正——下次请用英文引号 \" '")
                return val
            except ValueError:
                pass
        msg = getattr(e, "msg", str(e))
        errors.append(f"{where}: 绑定格式不对({msg})。常见原因:引号用了中文引号、少了逗号或括号。"
                      "照着 tuning-guide.md 的模板改。")
        return None


def _pick_sheet(wb):
    """按表头找「node_id + 层级列」的那一页——绝不吃'活动页'的亏(复制页签留底是常态)。"""
    hits = []
    for ws in wb.worksheets:
        head = [("" if c is None else str(c)).strip() for c in next(ws.iter_rows(values_only=True), [])]
        if "node_id" in head and any(_LVL_RE.match(h) for h in head):
            hits.append(ws)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit("没有任何页签的表头含 node_id + 层级列(L2(Chinese) 这种)——"
                         "别删表头行;若把表复制到了新页签,保留一页正式的即可")
    raise SystemExit(f"有 {len(hits)} 个页签都像正式调优表({[w.title for w in hits]})——"
                     "只保留一页,备份页请删掉或去掉它的表头行")


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
    old_nodes = old_index.get("nodes") or {}
    stem = tis[0].name[: -len(".tree_index.json")]
    old_sol_p = out / "tree" / "solutions.json"
    old_solutions = json.loads(old_sol_p.read_text(encoding="utf-8")) if old_sol_p.exists() else {}
    existing_aps = sorted(p for p in (out / "scripts").glob("*_analyzer.py")
                          if p.name != "cross_analyzer.py")   # cross 骨架形态不同,不能当克隆模板
    if not existing_aps:
        raise SystemExit("产物 scripts/ 下没有任何 analyzer,无法落绑定(产物不完整)")

    from openpyxl import load_workbook
    ws = _pick_sheet(load_workbook(xls[0], data_only=True))
    rows = [[("" if c is None else str(c)).strip() for c in r] for r in ws.iter_rows(values_only=True)]
    header, body = rows[0], rows[1:]

    # ---- 表头解析(全角冒号/空格归一;重复列、非法源名硬拦)----
    errors, warns = [], []
    lvl_col, tag_col, src_col = {}, {}, {}
    id_col = sol_zh = sol_en = meta_col = raw_col = None
    for i, h in enumerate(header):
        hn = h.replace("：", ":").strip()
        m = _LVL_RE.match(hn)
        if hn == "node_id":
            id_col = i
        elif m:
            l = int(m.group(1))
            if l in lvl_col:
                errors.append(f"表头有两个 L{l} 层级列——删掉多余的一列")
            lvl_col[l] = i
        elif hn.startswith("判据:"):
            t = hn.split(":", 1)[1].strip()
            if t == RAW_TAG:
                raw_col = i
            elif t in tag_col:
                errors.append(f"表头有两个「判据:{t}」列——删掉多余的一列")
            else:
                tag_col[t] = i
        elif hn.startswith("绑定:"):
            s = hn.split(":", 1)[1].strip()
            if not _SRC_RE.match(s):
                errors.append(f"表头「{h}」的源名「{s}」不合法(只能用英文字母/数字/下划线,别带空格)")
            elif s in src_col:
                errors.append(f"表头有两个「绑定:{s}」列——删掉多余的一列")
            else:
                src_col[s] = i
        elif hn == "Recommend Solution(Chinese)":
            sol_zh = i
        elif hn == "Recommend Solution(English)":
            sol_en = i
        elif hn == "meta(勿改)":
            meta_col = i
        elif hn and ("判据" in hn or "绑定" in hn):
            errors.append(f"表头「{h}」看着像判据/绑定列但格式不对——应写成「判据:<标签>」或「绑定:<源名>」(半角冒号)")
    if id_col is None or not lvl_col:
        raise SystemExit("表头不合格:缺 node_id 列或层级列(L2(Chinese) 这种)——别删表头行/表头列")

    # ---- 防"删列即清数据"(评审 M2):旧产物有的东西,表里必须还有对应列 ----
    old_tags = []
    for n in old_nodes.values():
        for t in (n.get("data_source") or {}):
            if t not in old_tags:
                old_tags.append(t)
    miss_tags = [t for t in old_tags if t not in tag_col]
    if miss_tags and raw_col is None:
        errors.append(f"旧产物的判据标签 {miss_tags} 在表头找不到对应「判据:<标签>」列——删了会把这些判据整列清空;"
                      "误删就把列加回来")
    if old_solutions and sol_zh is None and sol_en is None:
        errors.append("旧产物有 solutions.json,但表头没有 Recommend Solution 列——删了会清空全部解决方案;误删就加回来")
    if any((n.get("meta") or {}) for n in old_nodes.values()) and meta_col is None:
        errors.append("旧产物节点带 meta,但表头没有 meta(勿改) 列——误删就加回来")

    # ---- 逐行解析 ----
    nodes, order, solutions = {}, [], {}
    binds = {s: [] for s in src_col}
    stack, seen_ids, blank_rows = {}, set(), []
    for ri, row in enumerate(body):
        rowno = ri + 2

        def cell(i, _row=row):
            return _row[i] if (i is not None and i < len(_row)) else ""
        filled = [(l, cell(ci)) for l, ci in sorted(lvl_col.items()) if cell(ci)]
        if not filled:
            continue
        lvl, name = filled[-1]
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
                errors.append(f"第{rowno}行「{name}」: node_id「{nid}」格式不对(应为 n+数字;新增行请留空)")
            elif nid in seen_ids:
                errors.append(f"第{rowno}行「{name}」: node_id「{nid}」重复了——新增行 node_id 要留空,别复制别的行的")
            elif nid not in old_nodes:                    # 评审 M1:手编新 id = 静默断外部引用,拦
                errors.append(f"第{rowno}行「{name}」: node_id「{nid}」是新造的(树里从来没有过)——"
                              "新增节点请把这格留空,系统会自动编号")
            seen_ids.add(nid)
        else:
            blank_rows.append(ri)
        key = nid or f"__new__{ri}"
        raw_cond = cell(raw_col)
        cond_parts = [(t, cell(ci)) for t, ci in tag_col.items() if cell(ci)]
        if raw_cond and cond_parts:
            errors.append(f"第{rowno}行「{name}」:「判据:原文」和「判据:<标签>」列都填了——只能用一种"
                          "(原文列是老格式树专用的逐字判据)")
        if raw_cond:                                      # v1 老树:逐字保真,不加任何标签前缀
            condition, data_source = raw_cond, {}
        else:
            if nid in old_nodes:                          # 评审 M6:同节点多标签按旧序,防往返抖动
                old_order = list((old_nodes[nid].get("data_source") or {}).keys())
                cond_parts.sort(key=lambda tv: (old_order.index(tv[0]) if tv[0] in old_order else 10**6))
            condition = "\n".join(f"[{t}] {v}" for t, v in cond_parts) or None
            data_source = {t: v for t, v in cond_parts}
        meta = {}
        if cell(meta_col):
            meta = _parse_cell_json(cell(meta_col), f"第{rowno}行「{name}」meta 列", errors, warns) or {}
        nodes[key] = {"name": name, "parent": parent, "level": lvl,
                      "code": (old_nodes.get(nid) or {}).get("code") if nid else None,
                      "children": [], "condition": condition,
                      "combine": _infer_combine(condition), "data_source": data_source,
                      "pass_through": (not condition) or lvl <= 2 or parent is None,
                      "sheets": ["tuning"], "meta": meta}   # 高层不判断规则:L1/L2 与场景根(parent 为空)的
                                                            # 判据只供人读,不阻断、不参与绑定(与 v2 一致)
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
                                  "(A/C/D/G/J/P 之一;查不了就写 decision,模板见 tuning-guide.md)")
                elif s == "cross" and dec is None and str(b.get("source") or "") in ("", "cross"):
                    errors.append(f"第{rowno}行「{name}」绑定:cross: 每条跨源绑定必须带"
                                  " source=最终查询落点的**真实源名**(如 config;没有叫 cross 的数据源)"
                                  "——写法见 binding-handbook.md 第三节")
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
    used |= {int(k[1:]) for k in old_nodes if _NID_RE.match(k)}
    nxt = max(used, default=0) + 1
    rename = {}
    for ri in blank_rows:
        rename[f"__new__{ri}"] = f"n{nxt}"
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

    # ---- 备料:各源 CONDITIONS 与 analyzer 文本(全部成功才进入写盘)----
    # cross 不是数据指标(不进 plan 的指标链、不参与克隆),但它的绑定照常从「绑定:cross」列写回
    indicators = [s for s in src_col if s != "cross" and any(it for _r, _k, it in binds[s])]
    writes, summary_src = [], []
    for s in src_col:
        full = []
        for _ri, key, items in binds[s]:
            nid = rid(key)
            nd = new_nodes[nid]
            for b in items:
                entry = {"id": f"{s}-{nid}", "node": nd["name"], "node_id": nid,
                         "condition_text": nd["condition"] or nd["name"], "source": s, **b}
                full.append(entry)
        ap_path = out / "scripts" / f"{s}_analyzer.py"
        if ap_path.exists():
            src_text = ap_path.read_text(encoding="utf-8")
            before = len(_read_conditions_text(src_text))
        elif s == "cross":                                # cross 骨架形态特殊,不能克隆普通 analyzer 充数
            raise SystemExit("不合格(未改动任何文件):表有「绑定:cross」列,但产物没有"
                             " scripts/cross_analyzer.py 骨架——先跑 assemble_scenario 补齐再导入")
        else:                                             # 新增数据源:克隆现有 analyzer 换 INDICATOR
            src_text = existing_aps[0].read_text(encoding="utf-8")
            src_text = re.sub(r'(?m)^INDICATOR = .*$', f'INDICATOR = "{s}"', src_text, count=1)
            before = 0
        new_text, err = _replace_conditions(src_text, full, ap_path.name)
        if err:
            raise SystemExit(f"不合格(未改动任何文件):{err}")
        writes.append((ap_path, new_text))
        summary_src.append(f"{s}: {before}→{len(full)} 条")

    # plan / manifest:指标 = 有绑定的源(表列顺序);全部清空也要重写(评审 M3,防 plan 与 manifest 撕裂)
    plan_p, mf_p = out / "plan.json", out / "manifest.json"
    plan_text = mf_text = None
    if plan_p.exists():
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        # cross 现也走调优表(「绑定:cross」列);表里没这列时既有绑定原样不动。无论哪种,
        # 产物里有 cross_analyzer.py 就必须保留其 plan 节点——否则跨源绑定被静默甩出执行链。
        has_cross = (out / "scripts" / "cross_analyzer.py").exists()
        last_next = "cross" if has_cross else "rca"
        pnodes = []
        for i, ind in enumerate(indicators):
            nxt_id = indicators[i + 1] if i + 1 < len(indicators) else last_next
            pnodes.append({"id": ind, "kind": "script", "name": f"{ind}_analyzer",
                           "purpose": f"读 {ind} 数据,逐条件统计出事实(条件已在生成脚本里按类别模板算好)",
                           "inputs": {"data": f"$input.{ind}"},
                           "outputs": {"result": f"$memory.{ind}_result"},
                           "transition": {"next_on_success": nxt_id}})
        if has_cross:
            pnodes.append({"id": "cross", "kind": "script", "name": "cross_analyzer",
                           "purpose": "跨源判据统计(一条判据动用多个数据源:传参链/两侧比较);CONDITIONS 为空时秒过",
                           "inputs": {"data": "$input.*"},
                           "outputs": {"result": "$memory.cross_result"},
                           "transition": {"next_on_success": "rca"}})
        rca = next((n for n in plan.get("nodes", []) if n.get("id") == "rca"), None) or {
            "id": "rca", "kind": "agent_skill", "name": "fault-tree-rca",
            "purpose": "综合根因分析", "outputs": {"result": "$output.final_answer"},
            "transition": {"end": True}}
        rca["inputs"] = {"fault_tree_md": "$memory.fault_tree_md",
                         **{ind: f"$memory.{ind}_result" for ind in indicators},
                         **({"cross": "$memory.cross_result"} if has_cross else {})}
        plan["nodes"] = pnodes + [rca]
        plan["entry"] = indicators[0] if indicators else ("cross" if has_cross else "rca")
        plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
    if mf_p.exists():
        mf = json.loads(mf_p.read_text(encoding="utf-8"))
        mf["indicators"] = indicators
        mf_text = json.dumps(mf, ensure_ascii=False, indent=2)

    # ---- 变更摘要(含移动/解决方案,评审 H6/M2)----
    added = [k for k in new_nodes if k not in old_nodes]
    removed = [k for k in old_nodes if k not in new_nodes]
    renamed = [k for k in new_nodes if k in old_nodes and old_nodes[k].get("name") != new_nodes[k]["name"]]
    moved = [k for k in new_nodes if k in old_nodes and old_nodes[k].get("parent") != new_nodes[k]["parent"]]
    cond_chg = [k for k in new_nodes if k in old_nodes
                and (old_nodes[k].get("condition") or None) != new_nodes[k]["condition"]]
    sol_chg = [k for k in set(solutions) | set(old_solutions)
               if (solutions.get(k) or {}) != (old_solutions.get(k) or {})]

    # ---- 统一写盘(此后无任何可失败的计算)----
    tis[0].write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "tree" / f"{stem}.md").write_text(_index_to_markdown(index), encoding="utf-8")
    if solutions:
        old_sol_p.write_text(json.dumps(solutions, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        old_sol_p.unlink(missing_ok=True)
    for p, text in writes:
        p.write_text(text, encoding="utf-8")
    if plan_text:
        plan_p.write_text(plan_text, encoding="utf-8")
    if mf_text:
        mf_p.write_text(mf_text, encoding="utf-8")

    for w in warns:
        print(f"⚠ {w}")

    def _nm(k, pool):
        return f"{k}「{(pool.get(k) or {}).get('name', '')}」"

    print(f"导入完成:节点 {len(new_nodes)} 个。变更摘要:")
    print(f"  新增节点 {len(added)}: {', '.join(_nm(k, new_nodes) for k in added) or '无'}")
    print(f"  删除节点 {len(removed)}: {', '.join(_nm(k, old_nodes) for k in removed) or '无'}")
    print(f"  改名 {len(renamed)}: "
          + (", ".join(f"{k}: {old_nodes[k].get('name')}→{new_nodes[k]['name']}" for k in renamed) or "无"))
    print(f"  移动(换了上级){len(moved)}: "
          + (", ".join(f"{_nm(k, new_nodes)}: {old_nodes[k].get('parent')}→{new_nodes[k]['parent']}"
                       for k in moved) or "无"))
    print(f"  判据改动 {len(cond_chg)}: {', '.join(_nm(k, new_nodes) for k in cond_chg) or '无'}")
    print(f"  解决方案改动 {len(sol_chg)}: {', '.join(sorted(sol_chg)) or '无'}")
    print(f"  绑定: {'; '.join(summary_src) or '无'}")
    # ---- 跨源绑定联动核对:仅当调优表**没有**「绑定:cross」列时才需要——那时 cross_analyzer 是表外
    # 维护的,树在这里改了它不会跟上(新判据+旧传参链=静默漂移),必须点名;表里有 cross 列时
    # 绑定刚按表重写,天然一致,不提醒。----
    cross_ap = out / "scripts" / "cross_analyzer.py"
    if cross_ap.exists() and "cross" not in src_col:
        try:
            cross_ids = set(_read_condition_node_ids(cross_ap))
        except Exception:  # noqa: BLE001
            cross_ids = set()
        hit_chg = sorted(cross_ids & set(cond_chg))
        hit_rm = sorted(cross_ids & set(removed))
        if hit_chg or hit_rm:
            print("⚠ 以下节点另有**跨源绑定**(scripts/cross_analyzer.py,工程侧维护,不随调优表更新):")
            if hit_chg:
                print(f"  判据已改但 cross 绑定未动 {len(hit_chg)}: {', '.join(_nm(k, new_nodes) for k in hit_chg)}"
                      "——不复核会按旧逻辑算新判据(静默漂移),请知会工程同事核对/更新对应绑定")
            if hit_rm:
                print(f"  节点已删除但 cross 仍认领 {len(hit_rm)}: {', '.join(_nm(k, old_nodes) for k in hit_rm)}"
                      "——运行时会变孤儿事实,请让工程同事删掉对应 cross 绑定")
    print("下一步:重新打包/上传前,建议用真实数据跑一遍 runner 验证。")


if __name__ == "__main__":
    main()
