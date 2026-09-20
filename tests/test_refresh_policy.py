#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate --refresh-only 的工具链公共文件同步策略测试(标准库,直接 python 跑):
① 全部一致 → 打印"没有文件需要刷新",不产生新的 .bak;
② 技能根 scripts/check_data.py 与 skills 目录下的源不同 → 刷新并留 .py.bak;references/ 缺文件 → 补齐;
③ 只有行尾差异(CRLF)→ 不算不同,不动;
④ assets/scripts/custom_lib.py 含手工 @register 函数且能力齐全 → 不整体覆盖,只点名;
⑤ SKILL.md 正文与 skills 目录下的源不同 → 换正文、保 frontmatter 身份、留 SKILL.md.bak;
⑥ assets/zip2csv 单个文件不同 → 只刷新该文件;assets/fault-tree-rca 有文件不同 → 整目录刷新;
⑦ 结束语逐项列出本次动了什么。"""
import hashlib
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SK = REPO / "skills"
MIG = SK / "cs-scot-migrate" / "scripts" / "migrate_fault_check.py"
PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


def norm(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def same(a: Path, b: Path) -> bool:
    return norm(a) == norm(b)


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else ""


def baks(root: Path):
    return sorted(str(q.relative_to(root)) for q in root.rglob("*") if q.name.endswith(".bak"))


def mk_skill(root: Path):
    """按当前 skills 目录下的源拼一个打包技能:执行器脚本 + assets(compression-demo 产物 + rca/zip2csv 副本)。"""
    exe = SK / "cs-scot-execute"
    (root / "scripts").mkdir(parents=True)
    for n in ("runner.py", "check_data.py", "tree_prune.py"):
        shutil.copy2(exe / "scripts" / n, root / "scripts" / n)
    shutil.copytree(exe / "references", root / "references")
    body = re.sub(r"^---\r?\n.*?\r?\n---\r?\n", "", (exe / "SKILL.md").read_text(encoding="utf-8"), count=1, flags=re.S)
    (root / "SKILL.md").write_text("---\nname: demo-scenario\ndescription: 演示场景(身份要保住)\n---\n" + body, encoding="utf-8")
    assets = root / "assets"
    demo = REPO / "examples" / "compression-demo"
    shutil.copytree(demo / "tree", assets / "tree")
    shutil.copytree(demo / "scripts", assets / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("asm_fx", SK / "cs-scot-generate" / "scripts" / "assemble_scenario.py")
    asm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asm)
    (assets / "scripts" / "cross_analyzer.py").write_text(asm.CROSS_SCAFFOLD, encoding="utf-8")
    ig = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(SK / "fault-tree-rca", assets / "fault-tree-rca", ignore=ig)
    shutil.copytree(SK / "zip2csv", assets / "zip2csv", ignore=ig)
    return assets


def refresh(root: Path):
    r = subprocess.run([sys.executable, str(MIG), "--product", str(root), "--refresh-only"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


W = Path(tempfile.mkdtemp(prefix="refresh-"))
root = W / "demo-scenario"
assets = mk_skill(root)

print("\n== 首轮:引擎脚本(compression-demo 旧代)刷新,其余一致 ==")
rc, o = refresh(root)
check("退出码 0", rc == 0, o[-600:])
check("旧代引擎脚本被刷新(condition_kernels.py 留 .bak),缺的引擎文件补齐",
      (assets / "scripts" / "condition_kernels.py.bak").exists() and "scripts/custom_lib.py(补齐)" in o, o[-600:])
check("check_data / runner / tree_prune 与源一致,没动(无 .bak)",
      not any((root / "scripts" / f"{n}.bak").exists() for n in ("runner.py", "check_data.py", "tree_prune.py")), o[-600:])

print("\n== ① 全部一致再跑一遍:没有文件需要刷新,不产生新的 .bak ==")
b0 = baks(root)
rc, o = refresh(root)
check("打印“没有文件需要刷新”", "没有文件需要刷新" in o, o[-400:])
check("没有新的 .bak", baks(root) == b0, repr(set(baks(root)) - set(b0)))

print("\n== ②~⑥ 制造各种差异后再刷 ==")
cd = root / "scripts" / "check_data.py"
cd.write_text(cd.read_text(encoding="utf-8") + "\n# 老版本多出来的一行\n", encoding="utf-8")
(root / "references" / "plan_schema.md").unlink()
tp = root / "scripts" / "tree_prune.py"
tp.write_bytes(tp.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))      # 只改行尾
cl = assets / "scripts" / "custom_lib.py"
cl.write_text(cl.read_text(encoding="utf-8") + '\n\n@register("my_manual_fn")\ndef my_manual_fn(*a, **k):\n    return True\n', encoding="utf-8")
cl_bak_before = md5(assets / "scripts" / "custom_lib.py.bak")
sk = root / "SKILL.md"
sk.write_text(sk.read_text(encoding="utf-8") + "\n手工加的一行,应被当前执行指引替换。\n", encoding="utf-8")
c2c = assets / "zip2csv" / "scripts" / "config2csv.py"
c2c.write_text(c2c.read_text(encoding="utf-8") + "\n# stale\n", encoding="utf-8")
rules = assets / "fault-tree-rca" / "references" / "rca_rules.md"
rules.write_text(rules.read_text(encoding="utf-8") + "\n旧规则多一行\n", encoding="utf-8")
rc, o = refresh(root)
check("退出码 0", rc == 0, o[-800:])
check("② check_data.py 刷新到源版本并留 .py.bak", same(cd, SK / "cs-scot-execute" / "scripts" / "check_data.py")
      and (root / "scripts" / "check_data.py.bak").exists() and "scripts/check_data.py(已刷新" in o, o[-800:])
check("② references/plan_schema.md 补齐", (root / "references" / "plan_schema.md").exists() and "references/plan_schema.md(补齐)" in o, o[-800:])
check("③ 只有行尾差异的 tree_prune.py 不动(无 .bak,仍是 CRLF)",
      not (root / "scripts" / "tree_prune.py.bak").exists() and b"\r\n" in tp.read_bytes(), o[-800:])
check("④ custom_lib.py 含手工注册函数:不覆盖、点名", "my_manual_fn" in cl.read_text(encoding="utf-8")
      and md5(assets / "scripts" / "custom_lib.py.bak") == cl_bak_before and "含手工注册的函数" in o, o[-800:])
new_sk = sk.read_text(encoding="utf-8")
src_body = re.sub(r"^---\r?\n.*?\r?\n---\r?\n", "", (SK / "cs-scot-execute" / "SKILL.md").read_text(encoding="utf-8"), count=1, flags=re.S)
check("⑤ SKILL.md 正文换成源的当前版,frontmatter 身份保留,留 SKILL.md.bak",
      new_sk.startswith("---\nname: demo-scenario\ndescription: 演示场景(身份要保住)\n---\n")
      and new_sk.split("---\n", 2)[2] == src_body and (root / "SKILL.md.bak").exists() and "SKILL.md 正文已刷新" in o, o[-800:])
check("⑥ zip2csv 只刷新变了的 config2csv.py(留 .bak),zip2csv.py 没动",
      same(c2c, SK / "zip2csv" / "scripts" / "config2csv.py") and (c2c.parent / "config2csv.py.bak").exists()
      and not (c2c.parent / "zip2csv.py.bak").exists(), o[-800:])
check("⑥ fault-tree-rca 有文件不同 → 整目录刷新,旧副本 fault-tree-rca.bak",
      same(rules, SK / "fault-tree-rca" / "references" / "rca_rules.md") and (assets / "fault-tree-rca.bak").is_dir()
      and "assets/fault-tree-rca 已刷新" in o, o[-800:])
m = re.search(r"本次动了 (\d+) 处", o)
check("⑦ 结束语逐项列清单(check_data / plan_schema / SKILL.md / config2csv / fault-tree-rca 都在)",
      m and int(m.group(1)) >= 5 and all(k in o.split("翻新完成")[-1] for k in
      ("scripts/check_data.py", "references/plan_schema.md", "SKILL.md 正文", "assets/zip2csv/scripts/config2csv.py", "assets/fault-tree-rca")), o[-900:])

print("\n== 再跑一遍:只剩 custom_lib 的手工函数差异,仍是“没有文件需要刷新” ==")
rc, o = refresh(root)
check("没有文件需要刷新(手工函数只点名,不计入)", "没有文件需要刷新" in o and "含手工注册的函数" in o, o[-500:])
shutil.rmtree(W, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
