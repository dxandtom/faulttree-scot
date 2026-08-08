#!/usr/bin/env python3
"""tree_index.json → 辩论引擎期望的故障树 Excel(vendor 格式)。

为什么从 tree_index 生成、而不是直接喂原始 Excel:
- tree_index 是本仓的**归一化拓扑**——老格式(excel_to_tree)/新格式(v2 多页签)都会产它,
  从它出发,辩论引擎对上游 Excel 格式零感知;
- **`Ln Code` 列填我们的 node_id(nN)**:引擎输出的路径因此天然携带全链主键,
  结果适配回 rca.json 时能挂回树、能被 validate_rca 消费。

生成的列(按 vendor 文档的必填集):`L1 Code / L1 / L1(Chinese)` … + `Judgment Condition(Chinese)`。
层级**重定基**:树根记 L1(我们的 tree_index 根常在 L2 起,引擎按 L1 起读)。

条件的挂法:每个叶子一行(条件=叶子的);**带条件的中间节点另出一行**(路径止于该节点,
条件挂它)。每个保留节点的自带行是**硬约束**(没有它,引擎会把子孙行判据涂抹到上层),
并见 references/engine_ops.md 升级核对项 ②。

用法:tree_to_vendor_xlsx.py <tree_index.json> -o <out.xlsx>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _depth_map(nodes: dict, root: str) -> dict:
    """{nid: 重定基深度(根=1)};按 parent 链算,断链的节点跳过。"""
    depth = {root: 1}
    changed = True
    while changed:                       # 层层扩散,避免递归深度问题
        changed = False
        for nid, n in nodes.items():
            p = n.get("parent")
            if nid not in depth and p in depth:
                depth[nid] = depth[p] + 1
                changed = True
    return depth


def _path(nodes: dict, nid: str) -> list:
    out, cur, seen = [], nid, set()
    while cur and cur in nodes and cur not in seen:
        seen.add(cur); out.append(cur); cur = nodes[cur].get("parent")
    return list(reversed(out))


_ANON_NAME = re.compile(r"^L\d+节点_\d+$")


def enrich_conditions(index: dict, rca_input: dict | None = None,
                      binding_question: dict | None = None) -> dict:
    """把判据文本(和真名)合回树(只填空缺/匿名占位,不覆盖真实内容)。

    为什么需要:合成评测树这类产物的节点 condition 可能**全为 null**——判据文本
    住在 rca_input(pruned_nodes[].condition / indicator_results[].condition_text)和
    binding_question(to_bind[].condition_text)里。不合回去,xlsx 全空判据,引擎对每个节点
    白给满分、排名无意义。名字同理:评测树是匿名名(L3节点_001)而 rca_input 存真名,
    不合回去 question 的排除/数据不足名单与引擎树完全失联、rca.json 也输出匿名名。
    生产树(excel_to_tree 产)自带判据与真名,本函数是无害空操作。"""
    nodes = index.get("nodes") or {}
    fill, name_fill = {}, {}
    for b in (binding_question or {}).get("to_bind") or []:
        nid, ct = b.get("node_id"), str(b.get("condition_text") or "").strip()
        if nid and ct:
            fill.setdefault(nid, ct)
    if rca_input:
        pn = ((rca_input.get("fault_tree") or {}).get("pruned_nodes")) or {}
        for nid, n in pn.items():
            ct = str((n or {}).get("condition") or "").strip()
            if ct:
                fill.setdefault(nid, ct)
            nm = str((n or {}).get("name") or "").strip()
            if nm:
                name_fill.setdefault(nid, nm)
        for res in (rca_input.get("indicator_results") or {}).values():
            for c in (res.get("conditions") or []) if isinstance(res, dict) else []:
                nid, ct = c.get("node_id"), str(c.get("condition_text") or "").strip()
                if nid and ct:
                    fill.setdefault(nid, ct)
                nm = str(c.get("node") or "").strip()
                if nid and nm:
                    name_fill.setdefault(nid, nm)
    if not fill and not name_fill:
        return index
    out_nodes, changed, renamed = {}, 0, 0
    for nid, n in nodes.items():
        if nid in fill and not str(n.get("condition") or "").strip():
            n = {**n, "condition": fill[nid]}
            changed += 1
        truth = name_fill.get(nid)
        if truth and truth != n.get("name") and _ANON_NAME.match(str(n.get("name") or "")):
            n = {**n, "name": truth}      # 只替换匿名占位名,真名永不覆盖
            renamed += 1
        out_nodes[nid] = n
    if changed:
        print(f"  [tree补判据] 从 rca_input/binding_question 合回 {changed} 个节点的判据文本(该树 condition 原为空)")
    if renamed:
        print(f"  [tree补名字] 从 rca_input 合回 {renamed} 个节点的真名(替换匿名占位,评测树名单不再失联)")
    return {**index, "nodes": out_nodes}


def build_rows(index: dict, allowed: set | None = None):
    """→ (max_depth, rows, dropped);row = [(nid, name), ...](根→端点), 端点条件文本(可为空)。

    allowed:只把这个集合里的带判据节点当种子(祖先照常补齐)——`--pruned-only` 用它把树缩到
    剪枝局部子树,是大树控 LLM 成本的主开关;None = 全部带判据节点(默认,行为不变)。

    行布局按引擎建树的真实取法设计(读过引擎源码后定下,两条都是硬约束):
    1. **每个保留节点自带一行**(路径止于该节点,判据=它自己的,没有就空)——引擎按
       「分组第一行」提取该层节点的判据;若某节点没有自己的行,它会**偷走第一个子孙行的
       判据**,判据沿整条链被涂抹(评分语义全错+多烧 LLM 匹配调用)。自带行按深度排序
       恒在其分组第一行,保证每层取到的都是节点自己的判据。
    2. **保留集 = 带判据节点 ∪ 其全部祖先**:无判据叶子/整支无判据的分支不进 xlsx——
       引擎对空判据节点白给满分 1.0(full),会屠榜 Top-K;而空判据的**中间**自带行无害
       (引擎语义=无条件进入,路径乘 1.0,正是结构节点该有的行为)。"""
    nodes = index.get("nodes") or {}
    root = index.get("root")
    if not nodes or root not in nodes:
        raise ValueError("tree_index 缺 nodes/root")
    depth = _depth_map(nodes, root)

    def _has_cond(n):
        return bool(str(n.get("condition") or "").strip()) and not n.get("pass_through")

    conditioned = [nid for nid, n in nodes.items() if nid in depth and _has_cond(n)]
    if allowed is not None:
        n_all = len(conditioned)
        conditioned = [nid for nid in conditioned if nid in allowed]
        print(f"  [pruned-only] 带判据种子 {n_all} → {len(conditioned)}(只留剪枝局部子树内的)")
    if not conditioned:
        raise ValueError("没有任何带判据的节点可喂引擎"
                         + ("(pruned-only 过滤后为空——rca_input 的 pruned_nodes 与树对不上?)" if allowed is not None
                           else "(评测树需先用 enrich_conditions 把判据从 rca_input/binding_question 合回)"))
    # 引擎建树硬止于 L6(_build_deeper_levels 在 level>6 直接返回空):更深的行会被**静默丢弃**,
    # 深层真根因无声消失、幸存链全按"无条件进入"得 1.0 高置信——宁可当场报错也不出这种假结果。
    too_deep = sorted(nid for nid in conditioned if depth[nid] > 6)
    if too_deep:
        raise ValueError(
            f"引擎只支持 6 层(L1–L6),以下带判据节点重定基后深度>6,会被引擎静默截丢:{too_deep}。"
            f"请截更小的子树(generate 时把 --start-node 下移到更深的根)再喂辩论引擎。")
    keep = {root}
    for nid in conditioned:                          # 带判据节点 + 一路到根的祖先
        cur = nid
        while cur and cur in nodes and cur not in keep:
            keep.add(cur)
            cur = nodes[cur].get("parent")
    leaves_all = [nid for nid, n in nodes.items() if not (n.get("children") or [])]
    dropped = sum(1 for nid in leaves_all if nid not in keep)

    rows, maxd = [], 0
    for nid in sorted(keep, key=lambda x: (depth.get(x, 99), x)):   # 浅在前:自带行恒居分组首行
        path = _path(nodes, nid)
        maxd = max(maxd, len(path))
        cond = str(nodes[nid].get("condition") or "").strip() if _has_cond(nodes[nid]) else ""
        rows.append(([(p, nodes[p].get("name") or p) for p in path], cond))
    maxd = max(maxd, 3)                              # 引擎建树硬依赖 L1–L3 列存在,不足则补空列
    return maxd, rows, dropped


def _est_cond_lines(cond: str) -> int:
    """按引擎 _extract_conditions 的解析规则估算一格判据会拆成几条(=几次匹配 LLM 调用)。"""
    lines = [l.strip() for l in str(cond).split("\n") if l.strip()]
    n = sum(1 for l in lines
            if re.match(r'^\d+[.、:：]\s*', l) or l.startswith(("如果", "当", "满足", "检查", "确认")))
    return n if n else 1


def write_xlsx(index: dict, out_path: Path, allowed: set | None = None) -> int:
    from openpyxl import Workbook
    maxd, rows, dropped = build_rows(index, allowed)
    if dropped:
        print(f"  [tree→xlsx] 已排除 {dropped} 个无判据/pass_through 叶子(空判据会被引擎白给满分、屠榜 Top-K)")
    n_cond_rows = sum(1 for _, c in rows if c)
    n_cond_lines = sum(_est_cond_lines(c) for _, c in rows if c)
    print(f"  [成本预估] 带判据行 {n_cond_rows}、判据约 {n_cond_lines} 条 → 引擎至少 ≈{n_cond_lines} 次"
          f"条件匹配 LLM 调用;每个零匹配兄弟组再加(组员数+评委数)次辩论/评分调用,大树一跑几十分钟——"
          f"执行环境的超时要给足(见 SKILL.md 执行手册)。")
    wb = Workbook()
    ws = wb.active
    ws.title = "fault_tree"
    header = []
    for i in range(1, maxd + 1):
        header += [f"L{i} Code", f"L{i}", f"L{i}(Chinese)"]
    header.append("Judgment Condition(Chinese)")
    ws.append(header)
    for path, cond in rows:
        row = []
        for i in range(maxd):
            if i < len(path):
                nid, name = path[i]
                row += [nid, name, name]          # Code=node_id(主键载体);中英名都填中文名(无英文源)
            else:
                row += [None, None, None]         # 必须是真空单元格(None):引擎用 pandas groupby,
                                                  # NaN 组会被丢弃;写 "" 会生出 code="" 的幽灵节点
        row.append(cond if cond else None)
        ws.append(row)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="tree_index → vendor 故障树 xlsx")
    ap.add_argument("tree_index")
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args()
    index = json.loads(Path(a.tree_index).read_text(encoding="utf-8"))
    n = write_xlsx(index, Path(a.output))
    print(f"已生成 vendor 格式故障树:{a.output}({n} 行;Code 列=node_id)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
