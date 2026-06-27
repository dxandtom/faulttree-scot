#!/usr/bin/env python3
"""树驱动的「叶子覆盖度」闸门（generate 第3步用）。

为什么要它:`validate_conditions.py` 的覆盖检查是**数据值级**的——只能逮"绑了的列里有哪些值没被认领"。
但一个**整支故障的叶子根本没人绑、它的判别码这次数据里又没出现**的情况,值级检查看不见
(没 node_id → 没列 → 隐形)。结果弱模型容易**数据驱动**地只绑"这次出现过的码"、把没出现的叶子静默丢掉,
既欠覆盖、又过拟合当前数据(换份数据就用不了)。

本闸门**树驱动、与数据取值无关**:从 `tree_index.json` 枚举所有"需要绑定的诊断节点"(带判别条件、
非 pass_through;优先取无子节点的叶子,若该树把判据挂在中间节点则退而取全部带条件节点),对照各源
`CONDITIONS`/事实里出现过的 `node_id`,把**没有任何绑定认领的节点全列出来**——不管它的码这次出没出现。

判别"本源大概率能答":若节点的判别条件正文里**(按词边界)提到了本次已喂数据源的某个列名**(--columns 给),
则标 `[可能本源可答]` —— 这类未绑的节点是高置信"漏绑",闸门**退出码 1**(逼重绑);其余未绑节点多半
是"缺源"(判据要信令跟踪/配置等本次没喂的数据),只列出来知会,不阻断。无论是否被启发式标记,未绑节点
都会**全量列出**,真正的价值是"让漏绑可见、不可静默"。

退出码:0=无漏绑;1=有漏绑(条件提到已喂列却没绑);2=闸门无法判定(树里没有任何带条件节点 / 参数缺失)。

用法:
  check_tree_coverage.py <tree_index.json> \
      [--analyzer <ind>_analyzer.py ...] [--facts <ind>_facts.json ...] \
      [--columns "DCInnerCause,SgNBAddRejCause,..."]   # 只填**本次已喂源**的列名,别把没喂的源的列也塞进来
至少给一个 --analyzer 或 --facts(可多次,跨源合并 claimed 集)。
node_id 兼容两种写法:短 id `nN`(tree_index 的 key)或节点 `code` 字段——与 schema/RCA 一致。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


def _has_cond(node: dict) -> bool:
    c = node.get("condition")
    return bool(c and str(c).strip())


def _diagnostic(node: dict) -> bool:
    """诊断节点 = 带非空判别条件 且 非 pass_through(pass_through 不带判据、不阻断路径)。"""
    return _has_cond(node) and not node.get("pass_through")


def _ids_from_analyzer(path: Path) -> set:
    """从 <ind>_analyzer.py 静态抽 CONDITIONS 里每个 dict 的 node_id(AST 走读,
    只取 node_id 的字符串字面量——某个**别的**字段是 f-string/常量引用等非字面量也不会整份丢失)。"""
    try:
        mod = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as e:
        print(f"[warn] 读不了/解析失败 {path}: {e}", file=sys.stderr)
        return set()
    ids = set()
    for stmt in mod.body:
        if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in stmt.targets):
            for d in ast.walk(stmt.value):          # 走读 CONDITIONS 里所有 dict 字面量
                if isinstance(d, ast.Dict):
                    for k, v in zip(d.keys, d.values):
                        if (isinstance(k, ast.Constant) and k.value == "node_id"
                                and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                            ids.add(v.value)
    return ids


def _ids_from_facts(path: Path) -> set:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[warn] 读不了/解析失败 {path}: {e}", file=sys.stderr)
        return set()
    return {str(c["node_id"]) for c in doc.get("conditions", []) if c.get("node_id")}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="树驱动的叶子覆盖度闸门")
    ap.add_argument("tree_index")
    ap.add_argument("--analyzer", action="append", default=[], help="<ind>_analyzer.py(可多次,跨源合并)")
    ap.add_argument("--facts", action="append", default=[], help="<ind>_facts.json(可多次,跨源合并)")
    ap.add_argument("--columns", default="", help="**本次已喂源**的列名(逗号分隔),用于标记'本源大概率可答'的漏绑;别塞没喂的源的列")
    args = ap.parse_args()

    try:
        idx = json.loads(Path(args.tree_index).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"无法读取 tree_index: {e}", file=sys.stderr)
        return 2
    nodes = idx.get("nodes") or {}
    if not nodes:
        print("tree_index 里没有 nodes", file=sys.stderr)
        return 2
    if not args.analyzer and not args.facts:
        print("至少给一个 --analyzer 或 --facts", file=sys.stderr)
        return 2

    claimed = set()
    for a in args.analyzer:
        claimed |= _ids_from_analyzer(Path(a))
    for fp in args.facts:
        claimed |= _ids_from_facts(Path(fp))
    # node_id 兼容 code 形式:把 claimed 里写成 code 的归一回短 id（schema 明确两者都认,闸门也要认,否则误报漏绑）
    code2nid = {str(n["code"]): nid for nid, n in nodes.items() if n.get("code")}
    claimed = {code2nid.get(x, x) for x in claimed}

    cols = [c.strip() for c in args.columns.split(",") if c.strip()]

    def _mentions(cond: str, col: str) -> bool:
        """条件正文按**词边界**提到该列名(避免 'Cause' 命中 'DCInnerCause' 内部 → 假漏绑)。"""
        return re.search(r"(?<![A-Za-z0-9_])" + re.escape(col) + r"(?![A-Za-z0-9_])", cond) is not None

    # 诊断节点集:优先"带条件的叶子";若该树把判据挂在中间节点(没有带条件的叶子)→ 退而用全部带条件节点。
    cond_nodes = {nid: n for nid, n in nodes.items() if _diagnostic(n)}
    if not cond_nodes:
        print("⚠ 闸门无法判定:该 tree_index 没有任何「带 condition 的非 pass_through 节点」——"
              "判据可能没导出到 tree_index,或这棵树结构特殊。请核对建树产物,别把本次当'已全覆盖'。")
        return 2
    leaf_diag = {nid: n for nid, n in cond_nodes.items() if not n.get("children")}
    if leaf_diag:
        targets, unit = leaf_diag, "叶子"
    else:
        print("⚠ 该树的判别条件挂在中间节点(无带条件的叶子)——改用「全部带条件节点」判覆盖度。")
        targets, unit = cond_nodes, "带条件节点"

    def _ancestors(nid):
        out, cur, seen = [], nodes.get(nid, {}).get("parent"), set()
        while cur and cur in nodes and cur not in seen:
            seen.add(cur); out.append(cur); cur = nodes[cur].get("parent")
        return out

    covered, leaf_only, miss_answerable, miss_branch = [], [], [], []
    for nid, n in targets.items():
        if nid in claimed:
            covered.append(nid)
            continue
        branch_entered = any(a in claimed for a in _ancestors(nid))   # 路径上有祖先被绑 = 该支已进入
        cond = str(n.get("condition") or "")
        answerable = any(_mentions(cond, col) for col in cols) if cols else False
        if answerable:
            miss_answerable.append((nid, n, branch_entered))          # 条件提到已喂列却没绑 → 高置信漏绑
        elif branch_entered:
            leaf_only.append((nid, n))                                # 分支已入、未细绑(多半缺信令/配置定不到具体子因)
        else:
            miss_branch.append((nid, n))                              # 整支未触达(根→叶无任何绑定)

    def _ds(n):
        d = n.get("data_source") or {}
        return "/".join(f"{k}:{v}" for k, v in d.items() if v and v != "不涉及") or "—"

    def _snip(n, k=64):
        s = " ".join(str(n.get("condition") or "").split())
        return s[:k] + ("…" if len(s) > k else "")

    total = len(targets)
    print(f"{unit}覆盖度:树共 {total} 个需绑{unit};已绑 {len(covered)};未绑 {total - len(covered)} "
          f"= 漏绑(条件提到已喂列){len(miss_answerable)} + 分支已入未细绑{len(leaf_only)} + 整支未触达{len(miss_branch)}。")

    if miss_answerable:
        print(f"\n❌ 漏绑(条件正文提到已喂列 {cols},本源能答却没绑)—— 补绑 C 类(没出现就 matched=0)或显式 decision:insufficient_data:")
        for nid, n, be in sorted(miss_answerable, key=lambda x: x[0]):
            print(f"  - {nid} 「{n.get('name','')[:34]}」 L{n.get('level')} {'[分支已入]' if be else '[整支未入]'} src={_ds(n)}\n      cond: {_snip(n)}")

    if miss_branch:
        print(f"\n⚠ 整支未触达(根→叶无任何绑定;逐支确认是'缺源(没喂对应信令/配置)'还是'漏判一整支'):")
        roots_un = {}
        for nid, n in miss_branch:
            # 聚到"未触达区域的入口"= 路径上未绑且非根的最高祖先(根本身不直接绑,聚到根没信息量)
            anc = [a for a in _ancestors(nid) if a not in claimed and nodes.get(a, {}).get("parent") is not None]
            top = anc[-1] if anc else nid
            roots_un[top] = roots_un.get(top, 0) + 1
        for top, cnt in sorted(roots_un.items(), key=lambda x: -x[1])[:40]:
            tn = nodes.get(top, {})
            print(f"  - 子树 {top} 「{tn.get('name','')[:34]}」 src={_ds(tn)} —— 含 {cnt} 个未绑{unit}")

    if leaf_only:
        print(f"\nℹ 分支已入、未细绑 {len(leaf_only)} 个(分支级已绑、定不到具体子因;多因缺信令/配置,确认无碍即可)。")

    print("\n要点:绑定要**树驱动**——遍历每个叶子按树条件要的值绑(C 类 contains 那个码),"
          "不管这次数据出没出现;值没出现=matched=0(确定排除),不是 insufficient_data。"
          "本源真answer不了的(缺信令/配置源)才 decision:insufficient_data 或归'非典型/其他'兜底。")
    # 漏绑(条件提到已喂列却没绑)= 硬不合格;缺源/分支级未细绑不阻断(那是没喂数据,不是没绑对)
    return 1 if miss_answerable else 0


if __name__ == "__main__":
    sys.exit(main())
