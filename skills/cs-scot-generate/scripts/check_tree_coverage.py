#!/usr/bin/env python3
"""树驱动的「叶子覆盖度」检查（generate 第3步用）。

为什么要它:`validate_conditions.py` 的覆盖检查是**数据值级**的——只能逮"绑了的列里有哪些值没被认领"。
但一个**整支故障的叶子根本没人绑、它的判别码这次数据里又没出现**的情况,值级检查看不见
(没 node_id → 没列 → 隐形)。结果弱模型容易**数据驱动**地只绑"这次出现过的码"、把没出现的叶子静默丢掉,
既欠覆盖、又过拟合当前数据(换份数据就用不了)。

本检查**树驱动、与数据取值无关**:从 `tree_index.json` 枚举所有"需要绑定的诊断节点"(带判别条件、
非 pass_through;优先取无子节点的叶子,若该树把判据挂在中间节点则退而取全部带条件节点),对照各源
`CONDITIONS`/事实里出现过的 `node_id`,把**没有任何绑定认领的节点全列出来**——不管它的码这次出没出现。

判别"本源大概率能答":若节点的判别条件正文里**(按词边界)提到了本次已喂数据源的某个列名**(--columns 给),
则标 `[可能本源可答]` —— 这类未绑的节点是高置信"漏绑",检查**退出码 1**(要求重绑);其余未绑节点多半
是"缺源"(判据要信令跟踪/配置等本次没喂的数据),只列出来知会,不阻断。无论是否被启发式标记,未绑节点
都会**全量列出**,真正的价值是"让漏绑可见、不可静默"。

另附**跨源认领对账**(软提示,不改退出码):各源各认领了哪些节点;"判据像多个子条件却只有 1 条绑定认领"
和"告警类判据多源在场却单源认领"两类最隐蔽的欠绑定会被点名(子代理按源并行、互相看不见,
碰了一条就算认领整个节点 / 互相谦让,值级和节点级检查都看不出来,这里机械对账补上)。

退出码:0=无漏绑;1=有漏绑(条件提到已喂列却没绑);2=无法判定(树里没有任何带条件节点 / 参数缺失)。

用法:
  check_tree_coverage.py <tree_index.json> \
      [--analyzer <ind>_analyzer.py ...] [--facts <ind>_facts.json ...] \
      [--columns "DCInnerCause,SgNBAddRejCause,..."]   # 可省:缺省自动读 <OUT>/overview/<ind>_overview.json
至少给一个 --analyzer 或 --facts(可多次,跨源合并 claimed 集)。--columns 显式给了以给的为准
(只填**本次已喂源**的列名);不给则按 --analyzer 对应的概览文件自动取列名。
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


def _claims_from_analyzer(path: Path) -> dict:
    """从 <ind>_analyzer.py 静态抽 CONDITIONS:node_id → 绑定单元数(AST 走读,只取字符串字面量)。
    「绑定单元」= 该节点被认领了几个子条件:普通绑定记 1,D 组合按 sub 条数记——
    一个节点的判据常是多个子条件都要满足,绑定单元数才能看出"只碰了其中一条"。"""
    try:
        mod = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as e:
        print(f"[warn] 读不了/解析失败 {path}: {e}", file=sys.stderr)
        return {}
    units = {}
    for stmt in mod.body:
        if not (isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "CONDITIONS" for t in stmt.targets)):
            continue
        elems = stmt.value.elts if isinstance(stmt.value, (ast.List, ast.Tuple)) else []
        for el in elems:
            if not isinstance(el, ast.Dict):
                continue
            nid, n_sub = None, 0
            for k, v in zip(el.keys, el.values):
                if not isinstance(k, ast.Constant):
                    continue
                if k.value == "node_id" and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    nid = v.value
                elif k.value == "sub" and isinstance(v, (ast.List, ast.Tuple)):
                    n_sub = len(v.elts)
            if nid:
                units[nid] = units.get(nid, 0) + max(1, n_sub)
        if not elems:                                   # 备用做法:CONDITIONS 不是字面量列表 → 只遍历计次数
            for d in ast.walk(stmt.value):
                if isinstance(d, ast.Dict):
                    for k, v in zip(d.keys, d.values):
                        if (isinstance(k, ast.Constant) and k.value == "node_id"
                                and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                            units[v.value] = units.get(v.value, 0) + 1
    return units


def _claims_from_facts(path: Path):
    """<ind>_facts.json → (源标签, {node_id: 绑定单元数});D 组合按 sub/sub_facts 条数记。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[warn] 读不了/解析失败 {path}: {e}", file=sys.stderr)
        return None, {}
    label = str(doc.get("indicator") or path.stem)
    units = {}
    for c in doc.get("conditions", []):
        nid = c.get("node_id")
        if not nid:
            continue
        n_sub = len(c.get("sub") or (c.get("fact") or {}).get("sub_facts") or [])
        units[str(nid)] = units.get(str(nid), 0) + max(1, n_sub)
    return label, units


# 判据"像几个子条件"的保守估计:只按强分隔切(换行/;/①…⑩/且/并且/以及)。
# 特意**不切"或"**——"A 或 B"常是同列多取值、一条 equals/contains 列表就是正确绑法,切了会误报。
_CLAUSE_SPLIT = re.compile(r"[\n;;]|[①②③④⑤⑥⑦⑧⑨⑩]|且|并且|以及")
_ALARMISH = re.compile(r"告警|[Aa]larm|ALM-|_active|_clear|hw[A-Z]")


def _clause_count(cond: str) -> int:
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(str(cond))]
    return sum(1 for p in parts if len(p) >= 4)


def _renorm(units: dict, code2nid: dict) -> dict:
    """把以 code 写的键归一回短 id,同键累加。"""
    out = {}
    for k, v in units.items():
        nk = code2nid.get(k, k)
        out[nk] = out.get(nk, 0) + v
    return out


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

    # cross_analyzer 漏传点名:cross 通道的绑定同样认领叶子,漏传它 → 已绑数虚低、"整支未触达"虚高,
    # 实测就这么误报过 ❌ 逼人白排查。位置按产物布局推(<OUT>/scripts/,与 tree_index 同圈)。
    cross_ap = Path(args.tree_index).resolve().parent.parent / "scripts" / "cross_analyzer.py"
    if cross_ap.exists() and cross_ap.resolve() not in {Path(a).resolve() for a in args.analyzer}:
        n_cx = sum(_claims_from_analyzer(cross_ap).values())
        if n_cx:
            print(f"⚠ {cross_ap.name} 带 {n_cx} 个绑定单元却没传给本闸——已绑数会虚低、整支未触达会虚高。"
                  f"把它也加上再跑:--analyzer \"{cross_ap}\"(有 cross_facts 再加 --facts)。")

    per_source = {}                                     # 源标签 → {node_id: 绑定单元数}(跨源认领对账用)
    def _merge(label, units):
        d = per_source.setdefault(label, {})
        for k, v in units.items():
            d[k] = d.get(k, 0) + v
    for a in args.analyzer:
        p = Path(a)
        _merge(p.stem.replace("_analyzer", "") or p.stem, _claims_from_analyzer(p))
    for fp in args.facts:
        label, units = _claims_from_facts(Path(fp))
        if label is not None:
            _merge(label, units)
    # node_id 兼容 code 形式:把 claimed 里写成 code 的归一回短 id（schema 明确两者都认,本检查也要认,否则误报漏绑）
    code2nid = {str(n["code"]): nid for nid, n in nodes.items() if n.get("code")}
    per_source = {lab: _renorm(d, code2nid) for lab, d in per_source.items()}
    claimed = set().union(*per_source.values()) if per_source else set()

    cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    if not cols:
        # 缺省自动读概览列名——"可答"判定的触发条件不再依赖模型手抄 --columns(传空/传错 = 检查永远不会失败,
        # 号称机械的检查,触发条件却由模型供给,评审时被点名)。--columns 显式给了则以给的为准。
        auto_from = []
        for apath in args.analyzer:
            p = Path(apath)
            ind = p.stem[:-len("_analyzer")] if p.stem.endswith("_analyzer") else p.stem
            if ind == "cross":                          # cross 无自己的概览(它吃全部源)
                continue
            ov = p.resolve().parent.parent / "overview" / f"{ind}_overview.json"
            if not ov.exists():
                continue
            try:
                cs = ((json.loads(ov.read_text(encoding="utf-8")).get("summary") or {}).get("columns")) or []
            except Exception:  # noqa: BLE001
                continue
            cols += [str(c) for c in cs if str(c).strip()]
            auto_from.append(ov.name)
        cols = sorted(set(cols))
        if auto_from:
            print(f"[info] --columns 未给,已自动读概览列名({', '.join(auto_from)};共 {len(cols)} 列)"
                  "用于「本源大概率可答」标记。")

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

    # 跨源候选清单(软注记原料):dispatch/cross_candidates.json 是"cross 通道正在处理谁"的权威清单
    # (merge 汇集 cross_source 标记 + make_dispatch --cross 机械补漏扫入无人认领者后写回)。
    # 未绑节点若在这份清单上 = 流程中(等 cross 合并),不是真漏——路由批上线后每条判据基本只有
    # 一次绑定机会,这类"已进候选"的未绑会变多,注记就是把它和真漏分开,防止中途跑本检查误判。
    cross_ids = set()
    try:
        cand_p = Path(args.tree_index).resolve().parent.parent / "dispatch" / "cross_candidates.json"
        if cand_p.exists():
            cross_ids = {str(c.get("node_id")) for c in json.loads(cand_p.read_text(encoding="utf-8"))
                         if isinstance(c, dict) and c.get("node_id")}
    except (OSError, json.JSONDecodeError, AttributeError):
        cross_ids = set()

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
    n_pending = sum(1 for nid, *_ in (miss_answerable + leaf_only + miss_branch) if nid in cross_ids)
    print(f"{unit}覆盖度:树共 {total} 个需绑{unit};已绑 {len(covered)};未绑 {total - len(covered)} "
          f"= 漏绑(条件提到已喂列){len(miss_answerable)} + 分支已入未细绑{len(leaf_only)} + 整支未触达{len(miss_branch)}。")
    if n_pending:
        print(f"ℹ 未绑{unit}中 {n_pending} 个已进跨源候选(dispatch/cross_candidates.json,下面逐项标"
              f"[跨源候选中])——它们在 cross 补绑流程中,不是终态;**先把 --cross 简报派完、"
              f"merge 合并进 cross_analyzer.py,再跑本闸收口**(中途跑,未绑数会虚高)。")

    def _xc(nid):
        return " [跨源候选中]" if nid in cross_ids else ""

    if miss_answerable:
        print(f"\n❌ 漏绑(条件正文提到已喂列 {cols},本源能答却没绑)—— 补绑 C 类(没出现就 matched=0)或显式 decision:insufficient_data:")
        for nid, n, be in sorted(miss_answerable, key=lambda x: x[0]):
            print(f"  - {nid} 「{n.get('name','')[:34]}」 L{n.get('level')} {'[分支已入]' if be else '[整支未入]'} src={_ds(n)}{_xc(nid)}\n      cond: {_snip(n)}")

    if miss_branch:
        print(f"\n⚠ 整支未触达(根→叶无任何绑定;逐支确认是'缺源(没喂对应信令/配置)'还是'漏判一整支'):")
        roots_un, roots_xc = {}, {}
        for nid, n in miss_branch:
            # 聚到"未触达区域的入口"= 路径上未绑且非根的最高祖先(根本身不直接绑,聚到根没信息量)
            anc = [a for a in _ancestors(nid) if a not in claimed and nodes.get(a, {}).get("parent") is not None]
            top = anc[-1] if anc else nid
            roots_un[top] = roots_un.get(top, 0) + 1
            if nid in cross_ids:
                roots_xc[top] = roots_xc.get(top, 0) + 1
        for top, cnt in sorted(roots_un.items(), key=lambda x: -x[1])[:40]:
            tn = nodes.get(top, {})
            xc = roots_xc.get(top, 0)
            print(f"  - 子树 {top} 「{tn.get('name','')[:34]}」 src={_ds(tn)} —— 含 {cnt} 个未绑{unit}"
                  + (f"(其中 {xc} 个在跨源候选中,等 cross 合并)" if xc else ""))

    if leaf_only:
        n_lx = sum(1 for nid, _ in leaf_only if nid in cross_ids)
        print(f"\nℹ 分支已入、未细绑 {len(leaf_only)} 个(分支级已绑、定不到具体子因;多因缺信令/配置,确认无碍即可)"
              + (f",其中 {n_lx} 个在跨源候选中。" if n_lx else "。"))

    # ---- 跨源认领对账(软提示,不改退出码)----
    # 子代理按源并行绑、互相看不见:一个节点的判据常是**多个子条件都要满足**、且同一事实可能落在多个源里。
    # 这里从"谁认领了几条"机械对账,逮两种最隐蔽的漏绑:碰了一条就算认领了整个节点 / 该多源都绑却只有一源绑。
    if len(per_source) >= 1 and covered:
        dist = " / ".join(f"{lab} 认领 {len(d)} 个节点" for lab, d in sorted(per_source.items()))
        multi = sum(1 for nid in covered if sum(1 for d in per_source.values() if nid in d) >= 2)
        print(f"\n跨源认领分布:{dist}" + (f"(其中 {multi} 个节点被多源同绑)" if len(per_source) >= 2 else ""))
        # (a) 判据像多个子条件(强分隔:换行/;/①…/且),全部源加起来却只有 1 条绑定单元 → 多半只碰了其中一条
        under = []
        for nid in covered:
            n = targets[nid]
            est = _clause_count(str(n.get("condition") or ""))
            tot = sum(d.get(nid, 0) for d in per_source.values())
            if est >= 2 and tot == 1:
                under.append((nid, n, est))
        if under:
            print(f"⚠ 多子条件疑漏 {len(under)} 个:判据看着有 ≥2 段(换行/;/且分隔),却只有 1 条绑定认领——"
                  "逐个确认其余子条件已被 D 组合/其他源覆盖,或确属缺源(decision:insufficient_data 也算认领,别静默丢):")
            for nid, n, est in sorted(under, key=lambda x: x[0])[:20]:
                print(f"  - {nid} 「{n.get('name','')[:34]}」 约{est}段/1条绑定\n      cond: {_snip(n, 88)}")
            if len(under) > 20:
                print(f"  …(还有 {len(under) - 20} 个,略)")
        # (b) 告警类判据在多源在场时只有单源认领 → 告警可能同时落 cmd 回显 / dblg·log Summary / diag Alarm*,
        #     子代理互相看不见,"让别的源去绑"=谦让漏绑。软提示过一眼,不是硬错。
        if len(per_source) >= 2:
            lonely = []
            for nid in covered:
                n = targets[nid]
                srcs = [lab for lab, d in per_source.items() if nid in d]
                if len(srcs) == 1 and _ALARMISH.search(str(n.get("condition") or "")):
                    lonely.append((nid, n, srcs[0]))
            if lonely:
                print(f"ℹ 告警类判据单源认领 {len(lonely)} 个(多源在场;告警常同时落多源,确认其他源是否也该绑——都能答就都绑,不许谦让):")
                for nid, n, lab in sorted(lonely, key=lambda x: x[0])[:20]:
                    print(f"  - {nid} 「{n.get('name','')[:34]}」 仅 {lab} 认领")
                if len(lonely) > 20:
                    print(f"  …(还有 {len(lonely) - 20} 个,略)")

    print("\n要点:绑定要**树驱动**——遍历每个叶子按树条件要的值绑(C 类 contains 那个码),"
          "不管这次数据出没出现;值没出现=matched=0(确定排除),不是 insufficient_data。"
          "本源真answer不了的(缺信令/配置源)才 decision:insufficient_data 或归'非典型/其他'兜底。")
    # 漏绑(条件提到已喂列却没绑)= 硬不合格;缺源/分支级未细绑不阻断(那是没喂数据,不是没绑对)
    return 1 if miss_answerable else 0


if __name__ == "__main__":
    sys.exit(main())
