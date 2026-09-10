#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引擎副本与发行包重复文件测试:
① migrate/engine/ 副本与同门源文件逐字节一致(sync_engine.py --check);
② package_skill 打出的发行包里不含 migrate/engine/,且全包内没有内容完全相同的重复文件;
③ 发行包内的 migrate(无 engine/)真跑一单迁移,csv/xlsx 表都过;
④ 单独部署的 migrate(旁边没有任何同门)靠 engine/ 里的整目录副本,把旧打包技能的
   assets/fault-tree-rca(补上树视图 render_tree.py)与 SKILL.md 正文(带 rca_reply.md 指引)刷新到当前代。"""
import hashlib
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


print("\n== ① 引擎副本与源一致 ==")
p = subprocess.run([sys.executable, str(REPO / "skills/cs-scot-migrate/scripts/sync_engine.py"), "--check"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check("sync_engine.py --check 退出码 0(无漂移、无游离文件)", p.returncode == 0, (p.stdout + p.stderr)[-600:])

print("\n== ② 发行包零重复 ==")
out = Path(tempfile.mkdtemp(prefix="pkgdup-"))
p2 = subprocess.run([sys.executable, str(REPO / "skills/cs-scot-generate-optimize/scripts/package_skill.py"),
                     "--out", str(out), "--no-zip"], capture_output=True, text=True, encoding="utf-8",
                    errors="replace", cwd=str(REPO))
pkg = out / "cs-scot-generate-optimize"
check("打包退出码 0", p2.returncode == 0, (p2.stdout + p2.stderr)[-600:])
check("包里没有 migrate/engine/", not (pkg / "migrate" / "engine").exists())
check("包里有同门源(generate/scripts/xlsx_reader.py、cs-scot-execute/scripts/runner.py)",
      (pkg / "generate" / "scripts" / "xlsx_reader.py").exists() and (pkg / "cs-scot-execute" / "scripts" / "runner.py").exists())
by_hash = defaultdict(list)
for f in pkg.rglob("*"):
    if f.is_file() and f.suffix in (".py", ".md", ".json", ".yaml", ".txt") and f.name != "MANIFEST.txt":
        by_hash[hashlib.md5(f.read_bytes()).hexdigest()].append(f.relative_to(pkg).as_posix())
dups = [v for v in by_hash.values() if len(v) > 1]
check("全包内无内容完全相同的重复文件", not dups, repr(dups[:5]))

print("\n== ③ 发行包形态:包内 migrate(无 engine/)真跑一单迁移,csv/xlsx 表都过 ==")
import json
import zipfile
import importlib.util
GOOD = (
    "import os\n\n"
    "def fault_check(data_dir):\n"
    "    hits, n = [], 0\n"
    "    for root, _d, files in os.walk(data_dir):\n"
    "        for f in files:\n"
    "            if f.lower().endswith(('.txt', '.log')):\n"
    "                n += 1\n"
    "                for line in open(os.path.join(root, f), encoding='utf-8', errors='replace'):\n"
    "                    if 'KW_X' in line:\n"
    "                        hits.append(line.strip())\n"
    "    if hits:\n"
    "        return {'matched': 'true', 'evidence': [{'type': 'ne', 'id': 'x', 'matched': 'true',\n"
    "                'data': {'source_files': [], 'matched_items': hits[:5]}}], 'details': {}}\n"
    "    if n == 0:\n"
    "        return {'matched': 'undetermined', 'evidence': [], 'details': {'reason': '无数据文件'}}\n"
    "    return {'matched': 'false', 'evidence': [], 'details': {'reason': '未命中'}}\n")


def mk_product(d):
    demo = REPO / "examples" / "compression-demo"
    shutil.copytree(demo / "tree", d / "tree")
    shutil.copytree(demo / "scripts", d / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("asm_fx", REPO / "skills/cs-scot-generate/scripts/assemble_scenario.py")
    asm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asm)
    (d / "scripts" / "cross_analyzer.py").write_text(asm.CROSS_SCAFFOLD, encoding="utf-8")


def mk_pkg(store, name):
    pkg = store / f"_{name}"
    (pkg / "测试数据" / "网元A" / "CMD").mkdir(parents=True)
    (pkg / "测试数据" / "网元A" / "CMD" / "cmd.txt").write_text("blah KW_X\n", encoding="utf-8")
    (pkg / f"判断方法_{name}.md").write_text(
        f"# 判断方法:{name}\n\n## 节点名称\n{name}\n\n## 故障描述\n演练。\n\n## 判断条件\n1. CMD 中找 KW_X。\n\n"
        "### 命中条件\nA\n\n## 输出\n命中输出故障。\n\n## 数据来源\n| 数据 | 命令 | 目录 |\n|---|---|---|\n| x | display x | CMD/ |\n",
        encoding="utf-8")
    (pkg / f"fault_check_{name}.py").write_text(GOOD, encoding="utf-8")
    z = store / f"{name}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for q in sorted(pkg.rglob("*")):
            if q.is_file():
                zf.write(q, f"{name}/" + q.relative_to(pkg).as_posix())
    shutil.rmtree(pkg)


W = Path(tempfile.mkdtemp(prefix="pkgrun-"))
out2 = W / "dist"
subprocess.run([sys.executable, str(REPO / "skills/cs-scot-generate-optimize/scripts/package_skill.py"),
                "--out", str(out2), "--no-zip"], capture_output=True, text=True, cwd=str(REPO))
pkg_mig = out2 / "cs-scot-generate-optimize" / "migrate" / "scripts" / "migrate_fault_check.py"
check("包内迁移脚本存在、engine/ 不存在", pkg_mig.exists() and not (pkg_mig.parents[1] / "engine").exists())
HDR = "L1_Name_CN,L1_Name_EN,L2_Name_CN,L2_Name_EN,L3_Name_CN,L3_Name_EN,Product_Obs,Product_Name\n"
for form in ("csv", "xlsx"):
    prod = W / f"prod_{form}"
    mk_product(prod)
    store = W / f"store_{form}"
    store.mkdir()
    mk_pkg(store, "干扰导致")
    row = ["接入失败示例", "Root", "QoS建立失败", "Qos", "干扰导致", "Intf", "obs://b/x.zip", "干扰导致.zip"]
    tbl = W / f"map.{form}"
    if form == "csv":
        tbl.write_text(HDR + ",".join(row) + "\n", encoding="utf-8-sig")
    else:
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(HDR.strip().split(",")); ws.append(row); wb.save(tbl)
    r = subprocess.run([sys.executable, str(pkg_mig), "--package", str(store), "--product", str(prod),
                        "--auto", "--map", str(tbl), "--map-mode", "multi"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(W))
    o = (r.stdout or "") + (r.stderr or "")
    check(f"包内 migrate 跑 {form} 表:退出码 0 且落地 ✓ n3", r.returncode == 0 and "✓ n3「干扰导致」" in o, o[-700:])
    check(f"{form}:引擎补全走的是包内同门源(无 [warn] 拿不出可信版本)", "拿不出可信版本" not in o and "无法自动升级" not in o, o[-500:])
shutil.rmtree(W, ignore_errors=True)
shutil.rmtree(out, ignore_errors=True)

print("\n== ④ 单独部署形态:只装 cs-scot-migrate(无同门),靠 engine/ 刷新 assets/fault-tree-rca 树视图与 SKILL.md 正文 ==")
S = Path(tempfile.mkdtemp(prefix="solo-"))
solo = S / "skills" / "cs-scot-migrate"                  # 只拷 migrate 自己,旁边什么同门都没有
shutil.copytree(REPO / "skills" / "cs-scot-migrate", solo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
check("单独部署目录里只有 cs-scot-migrate(没有 fault-tree-rca/zip2csv/cs-scot-execute)",
      sorted(p.name for p in (S / "skills").iterdir()) == ["cs-scot-migrate"])
check("engine/ 里带着 fault-tree-rca 整目录(含 render_tree.py)、zip2csv 整目录、cs-scot-execute/SKILL.md",
      (solo / "engine" / "fault-tree-rca" / "scripts" / "render_tree.py").exists()
      and (solo / "engine" / "zip2csv" / "vendor" / "ifilter.py").exists()
      and (solo / "engine" / "cs-scot-execute" / "SKILL.md").exists())
# 旧打包技能:正文没有 rca_reply.md 指引;assets/fault-tree-rca 是上一代(没有 render_tree.py)
old_skill = S / "old-scenario-skill"
(old_skill / "scripts").mkdir(parents=True)
(old_skill / "SKILL.md").write_text("---\nname: old-scenario-skill\ndescription: 旧场景技能(保留身份)\n---\n# 旧正文\n\n只有一句。\n",
                                    encoding="utf-8")
shutil.copy2(REPO / "skills" / "cs-scot-execute" / "scripts" / "runner.py", old_skill / "scripts" / "runner.py")
assets = old_skill / "assets"
mk_product(assets)
shutil.copytree(REPO / "skills" / "fault-tree-rca", assets / "fault-tree-rca",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
(assets / "fault-tree-rca" / "scripts" / "render_tree.py").unlink()
shutil.copytree(REPO / "skills" / "zip2csv", assets / "zip2csv", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
store4 = S / "store"
store4.mkdir()
mk_pkg(store4, "干扰导致")
tbl4 = S / "map.csv"
tbl4.write_text(HDR + ",".join(["接入失败示例", "Root", "QoS建立失败", "Qos", "干扰导致", "Intf",
                                 "obs://b/x.zip", "干扰导致.zip"]) + "\n", encoding="utf-8-sig")
r4 = subprocess.run([sys.executable, str(solo / "scripts" / "migrate_fault_check.py"), "--package", str(store4),
                     "--product", str(assets), "--auto", "--map", str(tbl4), "--map-mode", "multi"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(S))
o4 = (r4.stdout or "") + (r4.stderr or "")
check("单独 migrate 跑通一单:退出码 0 且落地 ✓ n3", r4.returncode == 0 and "✓ n3「干扰导致」" in o4, o4[-700:])
check("assets/fault-tree-rca 整目录已刷新,补上了 render_tree.py,旧副本备份 fault-tree-rca.bak",
      "assets/fault-tree-rca 已刷新" in o4 and (assets / "fault-tree-rca" / "scripts" / "render_tree.py").exists()
      and (assets / "fault-tree-rca.bak").is_dir(), o4[-700:])
check("刷新后的 render_tree.py 与仓库 fault-tree-rca 当前版逐字节相同",
      (assets / "fault-tree-rca" / "scripts" / "render_tree.py").read_bytes()
      == (REPO / "skills" / "fault-tree-rca" / "scripts" / "render_tree.py").read_bytes())
sk4 = (old_skill / "SKILL.md").read_text(encoding="utf-8")
check("SKILL.md 正文已刷新到带 rca_reply.md 的执行指引,frontmatter 身份保留",
      "SKILL.md 正文已刷新" in o4 and "rca_reply.md" in sk4 and "--tree" in sk4
      and sk4.startswith("---\nname: old-scenario-skill\ndescription: 旧场景技能(保留身份)\n---\n"), o4[-700:])
check("全程没有一句“本机没有同门 … 可刷新/可拷”的告警", "没有同门" not in o4, o4[-700:])
shutil.rmtree(S, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
