#!/usr/bin/env python3
"""case_gen.py - 从 migrate 迁移台账 + tester 交付包机械生成评估用例库

原理：tester 交付包天然就是带标注的用例——每个包 = 单节点判据 + 一份"跑出来就该是
这个节点"的测试数据（tester 的交付契约）。migrate 对号落地时已在 产物/tree/migrations.json
台账里机械记下"节点 ↔ 包 ↔ 入口"。本脚本只是把这份已知信息落成 cases/<case_id>/expected.json，
零人工、可重复：故障树结构调整（增删节点、node_id 重排）后重跑一遍即全库刷新。

用法:
    python case_gen.py --product <产物目录或打包技能目录> \
                       --packages <交付包根目录（含各节点包）或单包目录> \
                       --cases-out <cases 输出目录> \
                       [--copy-data] [--force]

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
import json
import re
import shutil
import sys
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


def find_package_dir(packages_root: Path, package_node: str) -> Optional[Path]:
    """在交付包根目录下按包节点名找子包目录。

    优先目录名精确等于包节点名；其次目录名包含包节点名（唯一时才认）。
    packages_root 本身就是该包（单包形态）也认。
    """
    if packages_root.name == package_node:
        return packages_root
    exact = [d for d in packages_root.rglob(package_node) if d.is_dir()]
    if exact:
        return sorted(exact)[0]
    fuzzy = [d for d in packages_root.iterdir()
             if d.is_dir() and package_node in d.name] if packages_root.is_dir() else []
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def find_test_data(pkg_dir: Path) -> Optional[Path]:
    """包内测试数据目录：tester 规范名「测试数据」（任意深度，取最浅一个）。"""
    cands = sorted((p for p in pkg_dir.rglob("测试数据") if p.is_dir()),
                   key=lambda p: len(p.parts))
    return cands[0] if cands else None


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
    ap.add_argument("--packages", required=True, help="交付包根目录（含各节点包）或单包目录")
    ap.add_argument("--cases-out", required=True, help="cases 输出目录")
    ap.add_argument("--copy-data", action="store_true",
                    help="把包内 测试数据/ 拷进 cases/<id>/data/（默认只记录来源路径，评估执行时"
                         "直接用包内数据跑；数据大、包目录稳定时不必拷）")
    ap.add_argument("--force", action="store_true",
                    help="已存在的用例目录默认跳过（幂等、不冲人工修改）；加本参数强制重生成")
    a = ap.parse_args()

    product = locate_product(Path(a.product).expanduser().resolve())
    packages_root = Path(a.packages).expanduser().resolve()
    cases_out = Path(a.cases_out).expanduser().resolve()
    if not packages_root.exists():
        raise SystemExit(f"--packages 不存在：{packages_root}")

    ledger = load_json(product / "tree" / "migrations.json") \
        if (product / "tree" / "migrations.json").exists() else {}
    if not ledger:
        raise SystemExit(f"产物没有迁移台账（tree/migrations.json）——"
                         f"还没做过交付包迁移，无从生成用例：{product}")

    nodes, scenario = load_tree(product)

    made, skipped, problems = [], [], []
    for nid, entry in sorted(ledger.items()):
        package_node = entry.get("package_node", "")
        node = nodes.get(nid)
        if node is None:
            problems.append(f"{nid}「{package_node}」：台账里有但树上已不存在"
                            f"（结构调整后的孤儿台账项）——跳过，待人工确认")
            continue
        node_name = node.get("name", package_node)

        pkg_dir = find_package_dir(packages_root, package_node)
        if pkg_dir is None:
            problems.append(f"{nid}「{package_node}」：在 {packages_root} 下找不到同名包目录——跳过")
            continue
        data_dir = find_test_data(pkg_dir)
        if data_dir is None:
            problems.append(f"{nid}「{package_node}」：包里没有 测试数据/ 目录——跳过"
                            f"（无数据的包生成不了可执行用例）")
            continue

        case_id = f"{nid}-{sanitize(package_node)}"
        case_dir = cases_out / case_id
        if case_dir.exists() and not a.force:
            skipped.append(case_id)
            continue

        case_dir.mkdir(parents=True, exist_ok=True)
        if a.copy_data:
            dest = case_dir / "data"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(data_dir, dest)
            data_note = "数据已拷入 data/"
        else:
            data_note = f"数据在包内：{data_dir}"

        expected = build_expected(case_id, scenario, nid, node_name,
                                  package_node, entry, data_note)
        expected["data_source"] = str(case_dir / "data") if a.copy_data else str(data_dir)
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
