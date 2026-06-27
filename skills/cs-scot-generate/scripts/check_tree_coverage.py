#!/usr/bin/env python3
"""树驱动的「叶子覆盖度」闸门（generate 第3步用）。

为什么要它:`validate_conditions.py` 的覆盖检查是**数据值级**的——只能逮"绑了的列里有哪些值没被认领"。
但一个**整支故障的叶子根本没人绑、它的判别码这次数据里又没出现**的情况,值级检查看不见
(没 node_id → 没列 → 隐形)。结果弱模型容易**数据驱动**地只绑"这次出现过的码"、把没出现的叶子静默丢掉,
既欠覆盖、又过拟合当前数据(换份数据就用不了)。

本闸门**树驱动、与数据取值无关**:从 `tree_index.json` 枚举所有"需要绑定的叶子"(无子节点、有判别条件、
非 pass_through),对照各源 `CONDITIONS`/事实里出现过的 `node_id`,把**没有任何绑定认领的叶子全列出来**——
不管它的码这次出没出现。这样模型/人在第3步就拿到一张"还差哪些叶子"的清单,谁也没法再悄悄漏一整支。

判别"本源大概率能答":若叶子的判别条件正文里**提到了本次已喂数据源的某个列名**(--columns 给),
则标 `[可能本源可答]` —— 这类未绑的叶子是高置信"漏绑",闸门**退出码 1**(逼重绑);
其余未绑叶子多半是"缺源"(判据要信令跟踪/配置等本次没喂的数据),只列出来知会,不阻断。
列名启发式只是**提示**:无论是否标记,未绑叶子都会**全量列出**,真正的价值是"让漏绑可见、不可静默"。

用法:
  check_tree_coverage.py <tree_index.json> \
      [--analyzer <ind>_analyzer.py ...] [--facts <ind>_facts.json ...] \
      [--columns "DCInnerCause,SgNBAddRejCause,..."]
至少给一个 --analyzer 或 --facts(可多次,跨源合并 claimed 集)。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

# tree_index 里 pass_through 节点不带判据、不阻断路径,不需要绑定
def _needs_binding(node: dict) -> bool:
    if node.get("pass_through"):
        return False
    if node.get("children"):                      # 有子节点 = 靠子节点成立,父节点本身不要求直接绑
        return False
    cond = node.get("condition")
    return bool(cond and str(cond).strip())       # 叶子且有非空判别条件 → 必须有人绑(绑定 或 显式 insufficient)


def _claimed_from_analyzer(path: Path):
    """从 <ind>_analyzer.py 静态抽 CONDITIONS 里的 node_id(不执行脚本,ast.literal_eval 纯字面量)。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as e:
        print(f"[warn] 读不了/解析失败 {path}: {e}", file=sys.stderr)
        return set()
    ids = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in stmt.targets):
            try:
                conds = ast.literal_eval(stmt.value)
            except (ValueError, SyntaxError):
                print(f"[warn] {path} 的 CONDITIONS 不是纯字面量,跳过静态抽取", file=sys.stderr)
                return ids
            for c in conds if isinstance(conds, list) else []:
                nid = c.get("node_id") if isinstance(c, dict) else None
                if nid:
                    ids.add(str(nid))
    return ids


def _claimed_from_facts(path: Path):
    """从 <ind>_facts.json 收 node_id(运行后的事实,带 node_id)。"""
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
    ap.add_argument("--columns", default="", help="本次已喂数据源的列名(逗号分隔),用于标记'本源大概率可答'的漏绑")
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

    claimed = set()
    for a in args.analyzer:
        claimed |= _claimed_from_analyzer(Path(a))
    for fp in args.facts:
        claimed |= _claimed_from_facts(Path(fp))
    if not args.analyzer and not args.facts:
        print("至少给一个 --analyzer 或 --facts", file=sys.stderr)
        return 2

    cols = [c.strip() for c in args.columns.split(",") if c.strip()]

    def _ancestors(nid):
        out, cur = [], nodes.get(nid, {}).get("parent")
        seen = set()
        while cur and cur in nodes and cur not in seen:
            seen.add(cur); out.append(cur); cur = nodes[cur].get("parent")
        return out

    leaves = [(nid, n) for nid, n in nodes.items() if _needs_binding(n)]
    covered, leaf_only, miss_answerable, miss_branch = [], [], [], []
    for nid, n in leaves:
        if nid in claimed:
            covered.append(nid)
            continue
        branch_entered = any(a in claimed for a in _ancestors(nid))   # 路径上有祖先被绑=该支已进入
        cond = str(n.get("condition") or "")
        answerable = any(col in cond for col in cols) if cols else False
        if answerable:
            miss_answerable.append((nid, n, branch_entered))          # 条件提到已喂列却没绑 → 高置信漏绑
        elif branch_entered:
            leaf_only.append((nid, n))                                # 分支已入、叶子未细绑(多半缺信令/配置定不到具体子因)
        else:
            miss_branch.append((nid, n))                              # 整支未触达(根→叶无任何绑定)

    def _ds(n):
        d = n.get("data_source") or {}
        return "/".join(f"{k}:{v}" for k, v in d.items() if v and v != "不涉及") or "—"

    def _snip(n, k=64):
        s = " ".join(str(n.get("condition") or "").split())
        return s[:k] + ("…" if len(s) > k else "")

    total = len(leaves)
    print(f"叶子覆盖度:树共 {total} 个需绑叶子;已绑 {len(covered)};未绑 {total - len(covered)} "
          f"= 漏绑(条件提到已喂列){len(miss_answerable)} + 分支已入未细绑{len(leaf_only)} + 整支未触达{len(miss_branch)}。")

    if miss_answerable:
        print(f"\n❌ 漏绑(条件正文提到已喂列 {cols},本源能答却没绑)—— 补绑 C 类(没出现就 matched=0)或显式 decision:insufficient_data:")
        for nid, n, be in sorted(miss_answerable, key=lambda x: x[0]):
            print(f"  - {nid} 「{n.get('name','')[:34]}」 L{n.get('level')} {'[分支已入]' if be else '[整支未入]'} src={_ds(n)}\n      cond: {_snip(n)}")

    if miss_branch:
        print(f"\n⚠ 整支未触达(根→叶无任何绑定;逐支确认是'缺源(没喂对应信令/配置)'还是'漏判一整支'):")
        # 按"最高的未触达祖先"聚合,避免一支几十个叶子刷屏
        roots_un = {}
        for nid, n in miss_branch:
            # 聚到"未触达区域的入口"= 路径上未绑且非根的最高祖先(根本身不直接绑,聚到根没信息量)
            anc = [a for a in _ancestors(nid) if a not in claimed and nodes.get(a, {}).get("parent") is not None]
            top = anc[-1] if anc else nid
            roots_un.setdefault(top, 0)
            roots_un[top] += 1
        for top, cnt in sorted(roots_un.items(), key=lambda x: -x[1])[:40]:
            tn = nodes.get(top, {})
            print(f"  - 子树 {top} 「{tn.get('name','')[:34]}」 src={_ds(tn)} —— 含 {cnt} 个未绑叶子")

    if leaf_only:
        print(f"\nℹ 分支已入、叶子未细绑 {len(leaf_only)} 个(分支级已绑、定不到具体子因;多因缺信令/配置,确认无碍即可)。")

    print("\n要点:绑定要**树驱动**——遍历每个叶子按树条件要的值绑(C 类 contains 那个码),"
          "不管这次数据出没出现;值没出现=matched=0(确定排除),不是 insufficient_data。"
          "本源真answer不了的(缺信令/配置源)才 decision:insufficient_data 或归'非典型/其他'兜底。")
    # 漏绑(条件提到已喂列却没绑)= 硬不合格;缺源/分支级未细绑不阻断(那是没喂数据,不是没绑对)
    return 1 if miss_answerable else 0


if __name__ == "__main__":
    sys.exit(main())
