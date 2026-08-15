#!/usr/bin/env python3
"""case_gen.py - 从 migrate 迁移台账 + tester 交付包机械生成评估用例库

原理：tester 交付包天然就是带标注的用例——每个包 = 单节点判据 + 一份"跑出来就该是
这个节点"的测试数据（tester 的交付契约）。migrate 对号落地时已在 产物/tree/migrations.json
台账里机械记下"节点 ↔ 包 ↔ 入口"。本脚本只是把这份已知信息落成 cases/<case_id>/expected.json，
零人工、可重复：故障树结构调整（增删节点、node_id 重排）后重跑一遍即全库刷新。

用法:
    python case_gen.py --product <产物目录或打包技能目录> \
                       --packages <交付包根目录/单包目录/zip（合集 zip、目录里躺一堆 zip 都行）> \
                       --cases-out <cases 输出目录> \
                       [--copy-data] [--force]

--packages 的消化语义与 migrate 一致：zip → 解到临时区并递归解嵌套 zip；目录里有 zip →
拷到临时区再解（绝不动用户目录）；纯目录 → 原样用。来自 zip/临时区的测试数据会**强制拷贝**
进用例目录（临时区跑完即清，记路径就成空引用了）。

输出（每个台账节点一个用例）:
    <cases-out>/<node_id>-<包节点名>/
    ├── expected.json    # 期望：该节点为唯一期望根因，predicate_satisfied=true
    └── data/            # 包内 测试数据/ 的拷贝（--copy-data 时；默认只在 expected 里记录来源路径）

防泄题红线（与 SKILL.md 一致）:
    cases/ 目录不进产物、不进 assets/、不随 result-* 携带；
    跑诊断（execute/RCA）的必须是没读过 expected 的独立会话。
    生成 expected 不构成泄题——"包对哪个节点"本来就是 migrate 的本职信息。

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


def prepare_packages_root(packages: Path, tmp: Path) -> Tuple[Path, bool]:
    """把 --packages 消化成"全是目录"的形态（与 migrate 的 unpack 语义一致）。

    返回 (有效根目录, 是否临时区)。临时区在本次运行结束后清理——来自临时区的
    测试数据必须拷进用例目录，记路径会变成空引用。
    """
    if packages.is_dir():
        if not any(packages.rglob("*.zip")):
            return packages, False
        dst = tmp / "packages"
        print(f"[case_gen] --packages 里有 zip，拷到临时区解包（不动你的目录）…")
        shutil.copytree(packages, dst)
        _explode(dst)
        return dst, True
    if packages.suffix.lower() != ".zip":
        raise SystemExit(f"--packages 应是目录或 .zip：{packages}")
    dst = tmp / "packages"
    print(f"[case_gen] --packages 是 zip，解到临时区…")
    _unzip(packages, dst)
    _explode(dst)
    return dst, True


_FW = str.maketrans({"（": "(", "）": ")", "【": "[", "】": "]", "：": ":",
                     "－": "-", "—": "-", "　": " ", "，": ",", "、": ","})


def _norm(s: str) -> str:
    """名字归一化：全角转半角、ASCII 小写、去空白/下划线/连字符/点——
    「未迭代到LDP LSP」「未迭代到ldp_lsp」「未迭代到ldp lsp」都归成一个。"""
    s = str(s or "").translate(_FW).lower()
    return re.sub(r"[\s_\-–·.]+", "", s)


def _md_name(md: Path) -> Optional[str]:
    """从 判断方法*.md 提包节点名——与 migrate 同口径：
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


def find_test_data(pkg_dir: Path) -> Optional[Path]:
    """包内测试数据目录：目录名含「测试数据」（测试数据/测试数据集/测试数据(新) 都认，
    任意深度取最浅）；退而认平台导出名 formated_result / formatted_result。"""
    cands = sorted((p for p in pkg_dir.rglob("*") if p.is_dir() and "测试数据" in p.name),
                   key=lambda p: len(p.parts))
    if cands:
        return cands[0]
    cands = sorted((p for p in pkg_dir.rglob("*") if p.is_dir()
                    and p.name.lower() in ("formated_result", "formatted_result")),
                   key=lambda p: len(p.parts))
    return cands[0] if cands else None


def index_packages(root: Path) -> List[Dict[str, Any]]:
    """全根扫一遍，建候选包索引：[{dir, aliases(归一化名集合), data}]。

    包身份**优先取包内 判断方法*.md**（台账包名的同源出处，目录叫什么都不影响对号）：
    md 标题名 + md 文件名名 + 所在目录名 + 各级到 root 的中间目录名都记为别名。
    没有 md、但自己带测试数据目录的目录（散数据包）也收，以目录名为别名。
    """
    pkgs: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(pdir: Path, names) -> None:
        if pdir in seen:
            for p in pkgs:
                if p["dir"] == pdir:
                    p["aliases"].update(_norm(n) for n in names if n)
            return
        seen.add(pdir)
        aliases = {_norm(n) for n in names if n}
        aliases.discard("")
        pkgs.append({"dir": pdir, "aliases": aliases, "data": find_test_data(pdir)})

    for md in sorted(root.rglob("判断方法*.md")):
        if "规范" in md.name:
            continue
        pdir = md.parent
        names = [_md_name(md), re.sub(r"^判断方法[\s_\-::]*", "", md.stem), pdir.name]
        if pdir != root:                                  # 中间层目录名也算别名(多套一层的形态)
            names += [p.name for p in pdir.parents if p != root and root in p.parents]
        _add(pdir, names)

    # 没被 md 覆盖的"散数据包"：自身直下有测试数据目录的目录
    for d in sorted(p for p in root.rglob("*") if p.is_dir()):
        if d in seen or "测试数据" in d.name:
            continue
        if any(c.is_dir() and "测试数据" in c.name for c in d.iterdir()):
            _add(d, [d.name])
    if root not in seen and find_test_data(root):          # 根目录本身就是单包
        _add(root, [root.name])
    return pkgs


def match_package(pkgs: List[Dict[str, Any]], package_node: str):
    """按归一化阶梯对号：①别名精确相等 ②别名包含包名 ③包名包含别名(别名≥4字防碰瓷)。

    返回 (包, None) 或 (None, 问题说明)。同级多个命中=歧义，点名候选交人，不瞎猜。
    """
    key = _norm(package_node)
    if not key:
        return None, "包名为空"
    tiers = (
        [p for p in pkgs if key in p["aliases"]],
        [p for p in pkgs if any(key in a for a in p["aliases"])],
        [p for p in pkgs if any(a and len(a) >= 4 and a in key for a in p["aliases"])],
    )
    for tier in tiers:
        if len(tier) == 1:
            return tier[0], None
        if len(tier) > 1:
            names = ", ".join(str(p["dir"].name) for p in tier[:4])
            return None, f"多个候选包都对得上（{names}…）——歧义不瞎猜，把包目录名改唯一或人工指认"
    return None, "找不到能对上的包（目录名/判断方法md标题都不匹配）"


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


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
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
    a = ap.parse_args()

    product = locate_product(Path(a.product).expanduser().resolve())
    given_packages = Path(a.packages).expanduser().resolve()
    cases_out = Path(a.cases_out).expanduser().resolve()
    if not given_packages.exists():
        raise SystemExit(f"--packages 不存在：{given_packages}")

    tmp = Path(tempfile.mkdtemp(prefix="case-gen-"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)   # 含 SystemExit 在内退出即清临时区
    packages_root, ephemeral = prepare_packages_root(given_packages, tmp)
    copy_data = a.copy_data or ephemeral
    if ephemeral and not a.copy_data:
        print("[case_gen] 包来自 zip/临时解包区 → 测试数据将自动拷入各用例的 data/"
              "（临时区跑完即清，只记路径会变空引用）")

    ledger = load_json(product / "tree" / "migrations.json") \
        if (product / "tree" / "migrations.json").exists() else {}
    if not ledger:
        raise SystemExit(f"产物没有迁移台账（tree/migrations.json）——"
                         f"还没做过交付包迁移，无从生成用例：{product}")

    nodes, scenario = load_tree(product)

    pkg_index = index_packages(packages_root)
    print(f"[case_gen] 在 {packages_root} 下扫到 {len(pkg_index)} 个候选包"
          f"（身份以包内 判断方法*.md 为准，目录名只是别名之一）")

    made, skipped, problems = [], [], []
    for nid, entry in sorted(ledger.items()):
        package_node = entry.get("package_node", "")
        node = nodes.get(nid)
        if node is None:
            problems.append(f"{nid}「{package_node}」：台账里有但树上已不存在"
                            f"（结构调整后的孤儿台账项）——跳过，待人工确认")
            continue
        node_name = node.get("name", package_node)

        pkg, why = match_package(pkg_index, package_node)
        if pkg is None:
            problems.append(f"{nid}「{package_node}」：{why}——跳过")
            continue
        data_dir = pkg["data"]
        if data_dir is None:
            problems.append(f"{nid}「{package_node}」：对上了包 {pkg['dir'].name}，但里面没有"
                            f"测试数据目录（找了 *测试数据* 和 formated_result）——跳过")
            continue

        case_id = f"{nid}-{sanitize(package_node)}"
        case_dir = cases_out / case_id
        if case_dir.exists() and not a.force:
            skipped.append(case_id)
            continue

        case_dir.mkdir(parents=True, exist_ok=True)
        if copy_data:
            dest = case_dir / "data"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(data_dir, dest)
            data_note = "数据已拷入 data/"
        else:
            data_note = f"数据在包内：{data_dir}"

        expected = build_expected(case_id, scenario, nid, node_name,
                                  package_node, entry, data_note)
        expected["data_source"] = str(case_dir / "data") if copy_data else str(data_dir)
        (case_dir / "expected.json").write_text(
            json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
        made.append(case_id)

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


if __name__ == "__main__":
    main()
