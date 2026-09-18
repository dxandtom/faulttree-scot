#!/usr/bin/env python3
"""从现行 skill 产物生成平台1 **增强全量表**(联调工装,不进技能)。

平台约定(已对齐):每次优化都给全量表 + 标记哪些节点改了,请求带 optimizeType=ALL。
本脚本把现行 skill 的树原样导成这张表,作为"零改动基线"——不动它直接喂驱动,预期零动作;
在上面改几格(改判据/加一行/删一行/标记"是"),就是一次真实的树更新实验,不用等平台样例。

表的样子(层级列数按树深度动态,列名与平台正式全量表一致):
  Node_ID                                                    平台的节点 UUID(本地导出留空;技能整列忽略)
  | L1_Name_CN | L1_Name_EN | … | Ln_Name_CN | Ln_Name_EN
  | Diagnosis_Method_CN | Diagnosis_Method_EN                判据(取 CN;空+无 zip=清除该节点判据与绑定)
  | Solution_CN | Solution_EN                                解决方案(取 CN;空≠删除)
  | Product_Obs | Product_Name                               交付 zip(obs 地址 / 包名;结构行留空)
  | Need_Optimize                                            标记列:TRUE 才做内容动作(迁移/判据同步/方案同步)
行语义(ALL):树上每个节点一行(含根与中间结构层);表里没出现的节点=删除;树上没有的行=新增。

用法:
  python integration/platform1/make_full_table.py --product <skill 的 assets 目录> --out 全量表.csv
      [--platform-excel 老平台表.xlsx]   按路径把老表的 Product_Obs/Product_Name 抄进来
      [--zip-store <目录>]               本地货架:目录里有 <节点名>.zip 的行填 Product_Name
      [--mark 节点甲,节点乙 | --mark-all] 标记列填 TRUE(缺省全 FALSE=零改动基线)
输出 .csv(utf-8-sig)或 .xlsx(需 openpyxl);驱动与 migrate 两种都认。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load_tree(product: Path):
    tis = sorted((product / "tree").glob("*.tree_index.json"))
    if not tis:
        raise SystemExit(f"产物目录里没有 tree/*.tree_index.json:{product}")
    idx = json.loads(tis[0].read_text(encoding="utf-8"))
    nodes = idx.get("nodes") or {}
    roots = [nid for nid, n in nodes.items() if not n.get("parent")]
    root = idx.get("root") if idx.get("root") in nodes else (roots[0] if roots else None)
    if root is None:
        raise SystemExit("tree_index 里找不到根节点")
    return nodes, root


def dfs_order(nodes: dict, root: str):
    """根→叶深度优先,子节点顺序按 children 字段(没有就按 id 排)。"""
    out, seen = [], set()

    def kids(nid):
        ks = [k for k in (nodes[nid].get("children") or []) if k in nodes]
        if not ks:
            ks = sorted(k for k, n in nodes.items() if n.get("parent") == nid)
        return ks

    def walk(nid):
        if nid in seen:
            return
        seen.add(nid)
        out.append(nid)
        for k in kids(nid):
            walk(k)

    walk(root)
    for nid in nodes:                                     # 孤儿/多根的处理:也列出来,别悄悄丢
        if nid not in seen:
            walk(nid)
    return out


def chain(nodes: dict, nid: str):
    names, cur, guard = [], nid, 0
    while cur and cur in nodes and guard < 64:
        names.append(str(nodes[cur].get("name") or cur))
        cur = nodes[cur].get("parent")
        guard += 1
    return list(reversed(names))


def load_solutions(product: Path) -> dict:
    sp = product / "tree" / "solutions.json"
    if not sp.exists():
        return {}
    try:
        raw = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for nid, v in (raw.items() if isinstance(raw, dict) else []):
        out[nid] = str((v.get("zh") if isinstance(v, dict) else v) or "")
    return out


def load_platform_zips(path: Path) -> dict:
    """老平台表 → {路径元组: (obs, zip 名)}(只取有 Product_Name 的行;标记列忽略)。"""
    from driver import _read_table
    header, rows = _read_table(path)
    cn_cols, obs_col, name_col = [], None, None
    for h in header:
        hn = str(h).strip()
        low = hn.lower()
        if low.startswith("l") and low.endswith("_name_cn"):
            try:
                cn_cols.append((int(hn[1:hn.index("_")]), h))
            except ValueError:
                pass
        elif low == "product_obs":
            obs_col = h
        elif low == "product_name":
            name_col = h
    if not cn_cols or name_col is None:
        raise SystemExit(f"--platform-excel 不是平台1 导出表(需要 L*_Name_CN + Product_Name):{header}")
    cn_cols.sort()
    out = {}
    for r in rows:
        name = str(r.get(name_col) or "").strip()
        if not name:
            continue
        names = tuple(v for v in (str(r.get(c) or "").strip() for _l, c in cn_cols) if v)
        out[names] = (str(r.get(obs_col) or "").strip() if obs_col else "", name)
    return out


def build_rows(product: Path, platform_excel: Path | None, zip_store: Path | None,
               marks: set[str], mark_all: bool):
    nodes, root = load_tree(product)
    order = dfs_order(nodes, root)
    chains = {nid: chain(nodes, nid) for nid in order}
    depth = max(len(c) for c in chains.values())
    sols = load_solutions(product)
    pz = load_platform_zips(platform_excel) if platform_excel else {}
    header = ["Node_ID"]
    for i in range(1, depth + 1):
        header += [f"L{i}_Name_CN", f"L{i}_Name_EN"]
    header += ["Diagnosis_Method_CN", "Diagnosis_Method_EN", "Solution_CN", "Solution_EN",
               "Product_Obs", "Product_Name", "Need_Optimize"]
    rows, n_zip, n_mark = [], 0, 0
    for nid in order:
        n = nodes[nid]
        names = chains[nid]
        row = [""]                                       # Node_ID:本地没有平台 UUID,留空
        for i in range(depth):
            row += [names[i] if i < len(names) else "", ""]
        en = str(n.get("name_en") or n.get("en") or "")
        if en and len(names) <= depth:
            row[(len(names) - 1) * 2 + 2] = en
        obs, zname = pz.get(tuple(names), ("", ""))
        if not zname and zip_store is not None and (zip_store / f"{names[-1]}.zip").exists():
            obs, zname = f"fake://store/{names[-1]}.zip", f"{names[-1]}.zip"
        mark = "TRUE" if (mark_all or names[-1] in marks) else "FALSE"
        n_zip += bool(zname)
        n_mark += mark == "TRUE"
        row += [str(n.get("condition") or ""), "", sols.get(nid, ""), "", obs, zname, mark]
        rows.append(row)
    return header, rows, {"nodes": len(order), "depth": depth, "zip_rows": n_zip, "marked": n_mark}


def write_table(out: Path, header, rows):
    if out.suffix.lower() == ".xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            out = out.with_suffix(".csv")
            print(f"[warn] 没装 openpyxl,改写 csv:{out}")
        else:
            wb = Workbook()
            ws = wb.active
            ws.append(header)
            for r in rows:
                ws.append(r)
            wb.save(out)
            return out
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="从 skill 产物生成平台1 增强全量表(零改动基线)")
    ap.add_argument("--product", required=True, help="skill 的产物目录(含 tree/*.tree_index.json;技能形态是 assets/)")
    ap.add_argument("--out", required=True, help="输出 .csv 或 .xlsx")
    ap.add_argument("--platform-excel", help="老平台表:按路径抄 Product_Obs/Product_Name")
    ap.add_argument("--zip-store", help="本地货架目录:有 <节点名>.zip 的行填 Product_Name")
    ap.add_argument("--mark", default="", help="标记为「是」的节点名,逗号分隔")
    ap.add_argument("--mark-all", action="store_true", help="全部标记「是」")
    a = ap.parse_args()
    marks = {s.strip() for s in a.mark.split(",") if s.strip()}
    header, rows, st = build_rows(Path(a.product).expanduser().resolve(),
                                  Path(a.platform_excel).expanduser().resolve() if a.platform_excel else None,
                                  Path(a.zip_store).expanduser().resolve() if a.zip_store else None,
                                  marks, a.mark_all)
    out = write_table(Path(a.out).expanduser().resolve(), header, rows)
    print(f"[全量表] {out}")
    print(f"  节点 {st['nodes']} 行(含根与结构层),层级 {st['depth']},带 zip {st['zip_rows']} 行,"
          f"标记 TRUE {st['marked']} 行")
    print("  不改直接喂驱动(optimizeType=ALL)= 零改动基线,预期零动作;要做实验就在表上改:")
    print("    改判据 → 该行 Diagnosis_Method_CN 改文字 + Need_Optimize=TRUE     (预期 ~ 判据同步)")
    print("    换包   → 该行 Product_Name 指向货架上新 zip + Need_Optimize=TRUE   (预期 ✓ 迁移生效)")
    print("    加节点 → 新增一行,上级各层照抄,填判据,zip 留空                  (预期 + 新增)")
    print("    删节点 → 整行删掉(含其子孙行)                                    (预期 ✂ 删除)")
    print("    清判据 → Diagnosis_Method_CN 与 Product_Name 都清空 + Need_Optimize=TRUE (预期 清除判据的行执行)")
    print("    全 FALSE 且没改 → 结构对账零动作,只刷新工具链公共文件(等于刷新技能)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
