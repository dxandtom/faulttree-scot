#!/usr/bin/env python3
"""case_gen.py - 从 migrate 迁移台账 + 节点交付包机械生成评估用例库

没有台账时的备用做法:产物没有 tree/migrations.json(generate 直接生成、没走过 migrate 的技能)时,
退回**按名字匹配**——包内判断方法的节点名 ↔ 树节点名,配得上且全树唯一才生成用例(绝不猜;
同名或对不上的逐包点名)。要零歧义的精确匹配,先对技能跑一次 migrate 再建库。

原理：节点交付包天然就是带标注的用例——每个包 = 单节点判据 + 一份"跑出来就该是
这个节点"的测试数据（交付方的约定）。migrate 匹配导入时已在 产物/tree/migrations.json
台账里机械记下"节点 ↔ 包 ↔ 入口"。本脚本只是把这份已知信息落成 cases/<case_id>/expected.json，
零人工、可重复：故障树结构调整（增删节点、node_id 重排）后重跑一遍即全库刷新。

用法:
    python case_gen.py --product <产物目录或打包技能目录> \
                       --packages <交付包根目录/单包目录/zip（合集 zip、目录里躺一堆 zip 都行）> \
                       --cases-out <cases 输出目录> \
                       [--map <节点↔zip对应表 xlsx/csv>] [--copy-data] [--force]

包目录混乱(「互补」变体、多时间戳版本、改名 zip)导致名字匹配大面积歧义时,**加 --map**:
调优表导出天然带 node_id 列和产物 zip 文件名列,照表精确匹配,不用猜。

--packages 的消化语义与 migrate 一致：zip → 解到临时区并递归解嵌套 zip；目录里有 zip →
拷到临时区再解（绝不动用户目录）；纯目录 → 原样用。来自 zip/临时区的测试数据会**强制拷贝**
进用例目录（临时区跑完即清，记路径就成空引用了）。

输出（每个台账节点一个用例）:
    <cases-out>/<node_id>-<包节点名>/
    ├── expected.json    # 期望：该节点为唯一期望根因，predicate_satisfied=true
    └── data/            # 包内 测试数据/ 的拷贝（--copy-data 时；默认只在 expected 里记录来源路径）

防止泄露答案的规则（与 SKILL.md 一致）:
    cases/ 目录不进产物、不进 assets/、不随 result-* 携带；
    跑诊断（execute/RCA）的必须是没读过 expected 的独立会话。
    生成 expected 不构成泄露答案——"包对哪个节点"本来就是 migrate 的本职信息。

阴性用例（健康数据、期望无根因）没有机械来源，不由本脚本生成，人工按 expected_schema.md 放少量即可。
"""
from __future__ import annotations

import argparse
import atexit
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[error] 无法读取 {path}: {e}", file=sys.stderr)
        sys.exit(1)


def locate_product(product: Path) -> Path:
    """--product 给的是打包技能根目录（产物在 assets/）时自动进一层。"""
    if (product / "tree").is_dir():
        return product
    if (product / "assets" / "tree").is_dir():
        print(f"[info] --product 是打包技能目录，已自动定位到产物：{product / 'assets'}")
        return product / "assets"
    raise SystemExit(f"--product 不像场景产物目录（缺 tree/）：{product}")


def load_tree(product: Path) -> Tuple[Dict[str, Any], str]:
    """读 tree_index：返回 (nodes 字典, scenario)。nodes 的 key 即 node_id。"""
    tis = sorted((product / "tree").glob("*.tree_index.json"))
    if not tis:
        raise SystemExit(f"产物里没有 tree_index：{product / 'tree'}")
    ti = load_json(tis[0])
    nodes = ti.get("nodes", {})
    if not isinstance(nodes, dict):
        nodes = {n.get("node_id") or n.get("id"): n for n in nodes
                 if n.get("node_id") or n.get("id")}
    return nodes, ti.get("scenario", "")


def _unzip(zp: Path, dst: Path) -> None:
    try:
        zf = zipfile.ZipFile(zp, metadata_encoding="gbk")   # Windows 打的包常见 GBK 文件名
    except TypeError:                                        # 老 Python 无该参数
        zf = zipfile.ZipFile(zp)
    with zf:
        zf.extractall(dst)


def _explode(dst: Path) -> None:
    """就地递归解嵌套 zip,3 层封顶;解到同名目录,解完删 zip(与 migrate 同款语义)。"""
    for _ in range(3):
        inner = sorted(dst.rglob("*.zip"))
        if not inner:
            break
        for z in inner:
            _unzip(z, z.with_suffix(""))
            z.unlink()


def prepare_packages_root(packages: Path, tmp: Path) -> Tuple[List[Path], bool]:
    """把 --packages 消化成可扫描的根目录清单（与 migrate 的 unpack 语义一致）。

    目录里有 zip 时**只把 zip 文件拷进临时区解包**、原目录保持只读扫描（双根）——不整目录
    copytree：实测用户的包目录里躺着 _work/__pycache__ 等深层嵌套，整拷会顶爆 Windows 路径
    长度上限；而这些目录本就不该被复制——zip 才需要解，散开的包目录（含 _work 下带测试数据的）原地读。
    返回 (根目录列表, 是否用了临时区)。临时区在本次运行结束后清理——来自临时区的
    测试数据必须拷进用例目录，记路径会变成空引用。
    """
    if packages.is_dir():
        zips = [z for z in sorted(packages.rglob("*.zip")) if "__pycache__" not in z.parts]
        if not zips:
            return [packages], False
        dst = tmp / "packages"
        dst.mkdir(parents=True, exist_ok=True)
        print(f"[case_gen] --packages 里有 {len(zips)} 个 zip——只拷 zip 进临时区解包，"
              "原目录只读扫描（不整目录拷：_work/__pycache__ 深层嵌套会顶爆 Windows 路径长度，实测）…")
        for z in zips:
            t = dst / z.name
            i = 1
            while t.exists():                              # 不同子目录里的同名 zip：改名共存，不覆盖
                t = dst / f"{z.stem}~{i}{z.suffix}"
                i += 1
            shutil.copy2(z, t)
        _explode(dst)
        return [dst, packages], True
    if packages.suffix.lower() != ".zip":
        raise SystemExit(f"--packages 应是目录或 .zip：{packages}")
    dst = tmp / "packages"
    print(f"[case_gen] --packages 是 zip，解到临时区…")
    # 解进以 zip 名命名的子目录:zip 内容平铺(没有包装目录)时 zip 名就是包的唯一身份,
    # 丢了它 --map 的 src_zip 对不上、名字匹配也少一条别名(实测单 zip 直喂踩坑)
    _unzip(packages, dst / sanitize(packages.stem))
    _explode(dst)
    return [dst], True


_FW = str.maketrans({"（": "(", "）": ")", "【": "[", "】": "]", "：": ":",
                     "－": "-", "—": "-", "　": " ", "，": ",", "、": ","})


def _norm(s: str) -> str:
    """名字归一化：全角转半角、ASCII 小写、去空白/下划线/连字符/点——
    「未迭代到LDP LSP」「未迭代到ldp_lsp」「未迭代到ldp lsp」都归成一个。"""
    s = str(s or "").translate(_FW).lower()
    return re.sub(r"[\s_\-–·.]+", "", s)


def _md_name(md: Path) -> Optional[str]:
    """从 判断方法*.md 提包节点名——与 migrate 同一规则：
    标题行「# 判断方法: <名字>」优先，退文件名 stem 去「判断方法」前缀。"""
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = md.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if text:
        m = re.search(r"^#\s*判断方法[::]\s*(\S.*?)\s*$", text, re.M)
        if m:
            return m.group(1)
    stem = re.sub(r"^判断方法[\s_\-::]*", "", md.stem)
    return stem or None


def _md_path(md: Path) -> List[str]:
    """从 判断方法*.md 提可选「## 节点路径」(根→节点,/ 分隔)——同名节点消歧的机械依据。"""
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = md.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if not text:
        return []
    m = re.search(r"^##\s*节点路径\s*$\n+([^\n#]+)", text, re.M)
    if not m:
        return []
    return [seg.strip() for seg in re.split(r"[/\\>→]+", m.group(1).strip()) if seg.strip()]


def find_test_data(pkg_dir: Path) -> Optional[Path]:
    """包内测试数据目录：目录名含「测试数据」（测试数据/测试数据集/测试数据(新) 都识别，
    任意深度取最浅）；退而认平台导出名 formated_result / formatted_result。"""
    cands = sorted((p for p in pkg_dir.rglob("*") if p.is_dir() and "测试数据" in p.name),
                   key=lambda p: len(p.parts))
    if cands:
        return cands[0]
    cands = sorted((p for p in pkg_dir.rglob("*") if p.is_dir()
                    and p.name.lower() in ("formated_result", "formatted_result")),
                   key=lambda p: len(p.parts))
    return cands[0] if cands else None


def index_packages(root: Path, zip_root: bool = False) -> List[Dict[str, Any]]:
    """全根扫一遍，建候选包索引：[{dir, aliases(归一化名集合), title, src_zip, data}]。
    zip_root=True(root 是 zip 解包临时区)时记 src_zip=包所在的顶层解包目录名(即来源 zip 的
    文件名去扩展名,归一化)——--map 按它精确匹配。

    包身份**优先取包内 判断方法*.md**（台账包名的同源出处，目录叫什么都不影响匹配）：
    md 标题名 + md 文件名名 + 所在目录名 + 各级到 root 的中间目录名都记为别名。
    没有 md、但自己带测试数据目录的目录（散数据包）也收，以目录名为别名。
    """
    pkgs: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(pdir: Path, names, title: str = "", path=()) -> None:
        if pdir in seen:
            for p in pkgs:
                if p["dir"] == pdir:
                    p["aliases"].update(_norm(n) for n in names if n)
                    if not p["title"] and title:
                        p["title"] = _norm(title)
                    if not p["path"] and path:
                        p["path"] = list(path)
            return
        seen.add(pdir)
        aliases = {_norm(n) for n in names if n}
        aliases.discard("")
        src = ""
        if zip_root:
            try:
                rel = pdir.relative_to(root)
                seg = rel.parts[0] if rel.parts else pdir.name
            except ValueError:
                seg = pdir.name
            src = _norm(re.sub(r"~\d+$", "", seg))         # 去掉同名 zip 改名共存加的 ~N 后缀
        # title=包的"正名"(md 标题优先):同名多版本判定的依据——两个候选 title 相同=同包不同批次
        pkgs.append({"dir": pdir, "aliases": aliases, "title": _norm(title),
                     "src_zip": src, "data": find_test_data(pdir), "path": list(path)})

    for md in sorted(root.rglob("判断方法*.md")):
        if "规范" in md.name:
            continue
        pdir = md.parent
        primary = _md_name(md) or re.sub(r"^判断方法[\s_\-::]*", "", md.stem)
        names = [primary, re.sub(r"^判断方法[\s_\-::]*", "", md.stem), pdir.name]
        if pdir != root:                                  # 中间层目录名也算别名(多套一层的形态)
            names += [p.name for p in pdir.parents if p != root and root in p.parents]
        _add(pdir, names, title=primary or "", path=_md_path(md))

    # 没被 md 覆盖的"散数据包"：自身直下有测试数据目录的目录
    for d in sorted(p for p in root.rglob("*") if p.is_dir()):
        if d in seen or "测试数据" in d.name:
            continue
        if any(c.is_dir() and "测试数据" in c.name for c in d.iterdir()):
            _add(d, [d.name], title=d.name)
    if not pkgs and root not in seen and find_test_data(root):   # 根目录本身就是单包
        _add(root, [root.name], title=root.name)          # (已识别出子包时根不算——单 zip 解进
    return pkgs                                           #  stem 子目录后,根只是解包区,不是包)


def _version_stamp(p: Dict[str, Any]) -> tuple:
    """同名多版本的排序键：路径里最大的「日期[-时刻]」串（tester 包名自带,如 _20260811-090000），
    退目录 mtime。只用于 title 相同的候选间选最新，不参与身份判定。"""
    stamps = [a + (b or "000000")
              for a, b in re.findall(r"(\d{8})[-_T]?(\d{6})?", str(p["dir"]))]
    try:
        mt = p["dir"].stat().st_mtime
    except OSError:
        mt = 0.0
    return (max(stamps) if stamps else "", mt)


def _pkg_label(p: Dict[str, Any]) -> str:
    """播报用的包标识:取末两段路径——zip 解包形态里内层目录常同名(接口DOWN),
    时间戳在外层(接口DOWN_20260811-090000/接口DOWN),只打内层名分不清取了哪版。"""
    parts = p["dir"].parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else p["dir"].name


def _match_tiers(pkgs: List[Dict[str, Any]], key: str) -> tuple:
    """按归一化名取三级候选:①别名精确相等 ②别名包含包名 ③包名包含别名(别名≥4字,防止短名误配)。"""
    return (
        [p for p in pkgs if key in p["aliases"]],
        [p for p in pkgs if any(key in a for a in p["aliases"])],
        [p for p in pkgs if any(a and len(a) >= 4 and a in key for a in p["aliases"])],
    )


def _pick_from_tier(tier: List[Dict[str, Any]]):
    """同级多个命中:正名相同=同包不同批次,机械取最新并注记;正名不同=真歧义,点名交人。返回 (包或None, 说明)。"""
    titles = {p.get("title") or "" for p in tier}
    if len(titles) == 1 and "" not in titles:
        pick = max(tier, key=_version_stamp)
        others = "、".join(_pkg_label(p) for p in tier if p is not pick)
        return pick, (f"同名多版本 {len(tier)} 个(判断方法标题一致=同包不同批次),"
                      f"机械取最新 {_pkg_label(pick)};忽略:{others}")
    names = ", ".join(_pkg_label(p) for p in tier[:4])
    return None, f"多个候选包都对得上（{names}…）——歧义不瞎猜，把包目录名改唯一或人工指认"


def match_package(pkgs: List[Dict[str, Any]], package_node: str):
    """按归一化后的三级规则匹配：①别名精确相等 ②别名包含包名 ③包名包含别名(别名≥4字,防止短名误配)。

    返回 (包, 注记或None) 或 (None, 问题说明)。同级多个命中时分两种：
    - 候选的「正名」(判断方法 md 标题)完全相同 = **同一个包的不同批次版本**(tester 重复生成,
      文件名只差时间戳,实测一个文件夹里躺 84 个 zip 大半是这种)→ 机械取最新、明确提示,不整体放弃;
    - 正名不同 = 真歧义，点名候选交人，不瞎猜。
    """
    key = _norm(package_node)
    if not key:
        return None, "包名为空"
    for tier in _match_tiers(pkgs, key):
        if len(tier) == 1:
            return tier[0], None
        if len(tier) > 1:
            return _pick_from_tier(tier)
    return None, "找不到能对上的包（目录名/判断方法md标题都不匹配）"


def _read_map_rows(path: Path) -> List[list]:
    """读对应表全部行:csv 用标准库;xlsx 用 openpyxl(migrate 调优表同款依赖),多页取第一页。"""
    if path.suffix.lower() == ".csv":
        import csv
        with path.open(encoding="utf-8-sig", newline="") as f:
            return [list(r) for r in csv.reader(f)]
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise SystemExit("--map 读 xlsx 需要 openpyxl(migrate 调优表同款依赖);"
                         "装不上就把对应表另存为 csv 再传")
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _map_columns(header: list) -> Tuple[int, Optional[int]]:
    """列识别:node_id 列=表头恰为 node_id(没有即停);zip 列=表头含 zip/产物/文件名/优化包/package(可无)。"""
    low = [str(h or "").strip().lower() for h in header]
    if "node_id" not in low:
        raise SystemExit("--map 表头里找不到 node_id 列——用带 node_id 的表(调优表导出即有)")
    nid_col = low.index("node_id")
    zip_col = next((i for i, h in enumerate(low)
                    if any(k in h for k in ("zip", "产物", "文件名", "优化包", "package"))), None)
    return nid_col, zip_col


def _row_zip_name(r: list, zip_col: Optional[int]) -> str:
    """取该行的 zip 文件名:zip 列优先;识别不出就退化为"该行里以 .zip 结尾的单元格"。"""
    val = str(r[zip_col] or "").strip() if zip_col is not None and zip_col < len(r) else ""
    if not val.lower().endswith(".zip"):
        val = next((str(c).strip() for c in r
                    if isinstance(c, str) and c.strip().lower().endswith(".zip")), "")
    return val


def load_node_zip_map(path: Path) -> Dict[str, str]:
    """--map 对应表 → {node_id: 归一化 zip 名(去扩展名)}。**权威的匹配依据**:交付方实际产出的包目录里
    「互补」变体(真不同包)、多时间戳版本、改名 zip 会让名字模糊匹配大面积歧义/落空(实测 51 项
    只对上 8),而调优表导出天然带 node_id 列 + 产物 zip 文件名列——照表匹配,不用猜。
    xlsx 用 openpyxl(migrate 调优表同款依赖);csv 用标准库。列识别:node_id 列=表头恰为
    node_id;zip 列=表头含 zip/产物/文件名/优化包/package,识别不出就退化为"该行里以 .zip 结尾
    的单元格"。值可带全路径,只取文件名。多页 xlsx 取第一页(调优表导出的汇总页)。"""
    rows = _read_map_rows(path)
    if not rows:
        raise SystemExit(f"--map 空表：{path}")
    nid_col, zip_col = _map_columns(rows[0])
    out: Dict[str, str] = {}
    for r in rows[1:]:
        nid = str(r[nid_col] or "").strip() if nid_col < len(r) else ""
        if not nid:
            continue
        val = _row_zip_name(r, zip_col)
        if not val:
            continue
        stem = _norm(re.sub(r"\.zip$", "", Path(val.replace("\\", "/")).name, flags=re.I))
        if nid in out and out[nid] != stem:
            print(f"[case_gen] ⚠ --map 里 {nid} 对应多个不同 zip,取首见", file=sys.stderr)
            continue
        out[nid] = stem
    return out


def sanitize(name: str) -> str:
    """目录名安全化：路径分隔符等一律换下划线（中文保留——用例库本就在中文环境用）。"""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(name)).strip("_") or "unnamed"


def build_expected(case_id: str, scenario: str, nid: str, node_name: str,
                   package_node: str, entry: dict, data_note: str) -> Dict[str, Any]:
    return {
        "case_id": case_id,
        "scenario": scenario or node_name,
        "negative": False,
        "expected_root_causes": [
            {"node_id": nid, "node_name": node_name,
             "note": f"由交付包「{package_node}」的测试数据机械生成（case_gen）；{data_note}"}
        ],
        "expected_facts": {
            nid: {"predicate_satisfied": True}
        },
        "generated_by": {
            "tool": "case_gen",
            "package_node": package_node,
            "migration_fingerprint": entry.get("fingerprint", ""),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
        }
    }


def _parse_args() -> argparse.Namespace:
    """命令行参数。"""
    ap = argparse.ArgumentParser(description="从 migrate 台账 + 交付包机械生成评估用例库")
    ap.add_argument("--product", required=True, help="场景产物目录（或打包技能根目录）")
    ap.add_argument("--packages", required=True,
                    help="交付包根目录/单包目录/zip（合集 zip、目录里躺一堆 zip 都行）")
    ap.add_argument("--cases-out", required=True, help="cases 输出目录")
    ap.add_argument("--copy-data", action="store_true",
                    help="把包内 测试数据/ 拷进 cases/<id>/data/（默认只记录来源路径，评估执行时"
                         "直接用包内数据跑；数据大、包目录稳定时不必拷。--packages 是 zip 或"
                         "目录里带 zip 时自动强制拷贝——临时解包区跑完即清）")
    ap.add_argument("--force", action="store_true",
                    help="已存在的用例目录默认跳过（幂等、不冲人工修改）；加本参数强制重生成")
    ap.add_argument("--map", dest="map_path", default="",
                    help="节点↔zip 对应表(xlsx/csv,须含 node_id 列 + zip 文件名列,如调优表导出的"
                         "「产物文件名」)。给了它,台账节点优先按表精确匹配——治「互补」变体/多时间戳"
                         "版本/改名 zip 造成的名字匹配歧义;表里没有的节点回落名字匹配")
    return ap.parse_args()


def _plan_items(ledgerless: bool, ledger: Dict[str, Any], nodes: Dict[str, Any]) -> Tuple[list, Dict[str, int]]:
    """待配对清单:有台账按台账项;无台账逐树节点试配(按 nX 序号排),并统计同名节点数。返回 (items, name_dup)。"""
    if not ledgerless:
        return sorted(ledger.items()), {}
    # 无台账:逐树节点试配包(按名字匹配);先统计同名节点数,配上包但名字重复的不猜
    name_dup: Dict[str, int] = {}
    for nd in nodes.values():
        nm = str(nd.get("name") or "").strip()
        if nm:
            name_dup[nm] = name_dup.get(nm, 0) + 1
    items = [(nid, {"package_node": str(nd.get("name") or "").strip()})
             for nid, nd in sorted(nodes.items(),
                                   key=lambda kv: int(kv[0][1:]) if kv[0][1:].isdigit() else 10 ** 9)
             if str(nd.get("name") or "").strip()]
    return items, name_dup


def _match_by_map(pkg_index: List[Dict[str, Any]], want: str, nid: str, package_node: str):
    """--map 是权威依据:按来源 zip 精确匹配;同一个 zip 解出多个子包时在子集内再按名字分。
    返回 (包, 注记, 问题说明);包为 None 时问题说明非空。"""
    hits = [p for p in pkg_index if p.get("src_zip") == want]
    if not hits:
        return None, None, (f"{nid}「{package_node}」：--map 指到 zip「{want}」,但 --packages 里"
                            f"没有这个 zip(或没解出内容)——核对表/补包;跳过")
    if len(hits) == 1:
        pkg, why = hits[0], None
    else:                                              # 同一个 zip 解出多个子包:在子集内再按名字分
        pkg, why = match_package(hits, package_node)
        if pkg is None:
            return None, None, (f"{nid}「{package_node}」：--map 的 zip「{want}」里有多个子包"
                                f"且分不开({why})——跳过")
    return pkg, _join_notes(f"按 --map 匹配 zip「{want}」", why), None


def _match_item(pkg_index: List[Dict[str, Any]], want, nid: str, package_node: str, ledgerless: bool):
    """给一条台账/树节点配包:--map 指到 zip 就按 zip 精确匹配,否则名字三级匹配。
    返回 (包, 注记, 问题说明);包为 None 且问题为 None = 静默跳过(无台账模式树节点没配到包是常态)。"""
    if want:                                           # --map 是权威依据:按来源 zip 精确匹配
        return _match_by_map(pkg_index, want, nid, package_node)
    pkg, why = match_package(pkg_index, package_node)
    if pkg is None:
        if not ledgerless:                             # 无台账模式:树节点没配到包是常态,不点名
            return None, None, f"{nid}「{package_node}」：{why}——跳过"
        return None, None, None
    return pkg, why, None


def _pick_same_name_node(nodes: Dict[str, Any], pkg: Dict[str, Any], package_node: str) -> Optional[str]:
    """同名冲突:包内有「## 节点路径」就机械消歧(路径须是树上该节点祖先链的子序列),唯一命中才返回 node_id,否则 None(不猜)。"""
    ppath = [s for s in (pkg.get("path") or []) if s]
    if not ppath:
        return None
    full = ppath if _norm(ppath[-1]) == _norm(package_node) else ppath + [package_node]
    fulln = [_norm(x) for x in full]

    def _chain(k):
        out, cur = [], k
        while cur and cur in nodes:
            out.append(_norm(str(nodes[cur].get("name") or "")))
            cur = nodes[cur].get("parent")
        return list(reversed(out))

    def _sub(subseq, fullseq):
        it = iter(fullseq)
        return all(any(x == y for y in it) for x in subseq)

    same = [k for k, nd2 in nodes.items()
            if str(nd2.get("name") or "").strip() == package_node]
    hits = [k for k in same if _sub(_chain(k), fulln)]
    return hits[0] if len(hits) == 1 else None


def _join_notes(*parts) -> str:
    """把若干条注记(可为 None/空)用「; 」拼成一条。"""
    return "; ".join(x for x in parts if x)


def _write_case(case_dir: Path, data_dir: Path, copy_data: bool, why: Optional[str], case_id: str,
                scenario: str, nid: str, node_name: str, package_node: str, entry: dict) -> None:
    """落一个用例目录:按需把测试数据拷进 data/,写 expected.json(匹配注记进留痕)。"""
    case_dir.mkdir(parents=True, exist_ok=True)
    if copy_data:
        dest = case_dir / "data"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(data_dir, dest)
        data_note = "数据已拷入 data/"
    else:
        data_note = f"数据在包内：{data_dir}"
    if why:
        data_note += f"；{why}"                        # 版本取舍进留痕,审计时知道数据来自哪一版

    expected = build_expected(case_id, scenario, nid, node_name,
                              package_node, entry, data_note)
    expected["data_source"] = str(case_dir / "data") if copy_data else str(data_dir)
    (case_dir / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")


def _generate_cases(items: list, nodes: Dict[str, Any], pkg_index: List[Dict[str, Any]],
                    nid_zip: Dict[str, str], ledgerless: bool, name_dup: Dict[str, int],
                    cases_out: Path, copy_data: bool, force: bool, scenario: str):
    """逐条配包并落用例。返回 (生成的 case_id, 跳过的 case_id, 问题清单, 用到的包目录集合)。"""
    made, skipped, problems = [], [], []
    dup_reported: set = set()
    used_pkg_dirs = set()
    for nid, entry in items:
        package_node = entry.get("package_node", "")
        node = nodes.get(nid)
        if node is None:
            problems.append(f"{nid}「{package_node}」：台账里有但树上已不存在"
                            f"（结构调整后的孤儿台账项）——跳过，待人工确认")
            continue
        node_name = node.get("name", package_node)

        want = nid_zip.get(nid)
        pkg, why, problem = _match_item(pkg_index, want, nid, package_node, ledgerless)
        if problem:
            problems.append(problem)
        if pkg is None:
            continue
        if ledgerless and want is None and name_dup.get(package_node, 0) > 1:
            # 同名冲突(--map 显式给了 node_id 的行不做此检查——表是权威依据):
            # 包内有「## 节点路径」就机械消歧(子序列),否则不猜
            picked = _pick_same_name_node(nodes, pkg, package_node)
            if picked is None:
                if package_node not in dup_reported:
                    dup_reported.add(package_node)
                    problems.append(f"包对上节点名「{package_node}」,但树上有多个同名节点且包内无"
                                    "「节点路径」可消歧——不猜,跳过(交付包补 ## 节点路径,"
                                    "或先对技能跑一次 migrate 按台账精确匹配)")
                continue
            if picked != nid:
                continue                                 # 这个同名 nid 不是路径指向的那个:静默让位
            why = _join_notes(f"同名 {name_dup[package_node]} 个,包内节点路径消歧", why)
        if ledgerless:
            why = _join_notes("无台账,按节点名匹配", why)
        used_pkg_dirs.add(str(pkg["dir"]))
        if why:                                            # 匹配上了但带注记(按 map 匹配/同名多版本取最新)
            print(f"  ⚠ {nid}「{package_node}」：{why}")
        data_dir = pkg["data"]
        if data_dir is None:
            problems.append(f"{nid}「{package_node}」：对上了包 {pkg['dir'].name}，但里面没有"
                            f"测试数据目录（找了 *测试数据* 和 formated_result）——跳过")
            continue

        case_id = f"{nid}-{sanitize(package_node)}"
        case_dir = cases_out / case_id
        if case_dir.exists() and not force:
            skipped.append(case_id)
            continue
        _write_case(case_dir, data_dir, copy_data, why, case_id, scenario, nid, node_name, package_node, entry)
        made.append(case_id)
    return made, skipped, problems, used_pkg_dirs


def _leftover_problems(pkg_index: List[Dict[str, Any]], used_pkg_dirs: set) -> List[str]:
    """无台账模式:带测试数据却在树上没配到同名节点的包,逐个点名。"""
    left = [p for p in pkg_index if str(p["dir"]) not in used_pkg_dirs and p.get("data")]
    return [f"包「{_pkg_label(p)}」在树上找不到同名节点(按名字匹配失败)——"
            "确认该包属于本场景;或先对技能跑一次 migrate,用台账精确匹配" for p in left]


def _report(made: list, skipped: list, problems: list, cases_out: Path) -> None:
    """收尾播报:生成/跳过/未能生成清单;一个都没生成也没跳过 → 退出码 1。"""
    print(f"[case_gen] 生成 {len(made)} 个用例 → {cases_out}")
    for c in made:
        print(f"  + {c}")
    if skipped:
        print(f"[case_gen] 跳过已存在 {len(skipped)} 个（重生成加 --force）：{', '.join(skipped[:8])}"
              + ("…" if len(skipped) > 8 else ""))
    if problems:
        print(f"\n[case_gen] ⚠ 未能生成 {len(problems)} 项（不影响已生成的）：")
        for p in problems:
            print(f"  - {p}")
    if not made and not skipped:
        sys.exit(1)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    a = _parse_args()

    product = locate_product(Path(a.product).expanduser().resolve())
    given_packages = Path(a.packages).expanduser().resolve()
    cases_out = Path(a.cases_out).expanduser().resolve()
    if not given_packages.exists():
        raise SystemExit(f"--packages 不存在：{given_packages}")

    tmp = Path(tempfile.mkdtemp(prefix="case-gen-"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)   # 含 SystemExit 在内退出即清临时区
    package_roots, ephemeral = prepare_packages_root(given_packages, tmp)
    copy_data = a.copy_data or ephemeral
    if ephemeral and not a.copy_data:
        print("[case_gen] 包来自 zip/临时解包区 → 测试数据将自动拷入各用例的 data/"
              "（临时区跑完即清，只记路径会变空引用）")

    ledger = load_json(product / "tree" / "migrations.json") \
        if (product / "tree" / "migrations.json").exists() else {}
    nodes, scenario = load_tree(product)
    ledgerless = not ledger
    if ledgerless:
        print("[case_gen] 产物没有迁移台账（tree/migrations.json，技能可能是 generate 直接生成、"
              "没走过 migrate）→ 退回**按名字匹配**建库：包内判断方法的节点名 ↔ 树节点名，"
              "对得上且全树唯一才生成（绝不猜）；要零歧义的台账精确匹配，先对技能跑一次 migrate。")

    pkg_index = []
    for root in package_roots:
        pkg_index += index_packages(root, zip_root=(ephemeral and root == package_roots[0]))
    print(f"[case_gen] 在 {' 与 '.join(str(r) for r in package_roots)} 下扫到 {len(pkg_index)} 个候选包"
          f"（身份以包内 判断方法*.md 为准，目录名只是别名之一）")
    nid_zip = load_node_zip_map(Path(a.map_path).expanduser().resolve()) if a.map_path else {}
    if nid_zip:
        print(f"[case_gen] --map 载入 {len(nid_zip)} 条节点↔zip 对应——这些节点按表精确匹配,不走名字匹配")

    items, name_dup = _plan_items(ledgerless, ledger, nodes)
    made, skipped, problems, used_pkg_dirs = _generate_cases(
        items, nodes, pkg_index, nid_zip, ledgerless, name_dup, cases_out, copy_data, a.force, scenario)
    if ledgerless:
        problems += _leftover_problems(pkg_index, used_pkg_dirs)
    _report(made, skipped, problems, cases_out)


if __name__ == "__main__":
    main()
