#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""migrate 迁入交付包、按对应表批量处理时的补齐模式(refresh_engine 的 full=False)测试(标准库,直接 python 跑):
① 能力标记齐全但内容不同的公共文件(check_data.py、SKILL.md 正文、zip2csv、fault-tree-rca 的规则说明)
   一概不覆盖,不产生 .bak;
② 缺能力标记的旧版引擎脚本(compression-demo 的 condition_kernels.py)照样刷新;
③ 缺失的文件(references/plan_schema.md、custom_lib.py)照样补齐;
④ 同一个技能再按整体刷新(full=True)跑,①里没动的文件全部刷到源版本。
目的:别的工具(批量适配)升级过的公共文件,不会被迁移覆盖回本机工具链的版本。"""
import importlib.util
import re
import shutil
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


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mk_skill(root: Path):
    """按当前 skills 目录下的源组装一个打包技能(与 test_refresh_policy 同一组装方式)。"""
    exe = SK / "cs-scot-execute"
    (root / "scripts").mkdir(parents=True)
    for n in ("runner.py", "check_data.py", "tree_prune.py"):
        shutil.copy2(exe / "scripts" / n, root / "scripts" / n)
    shutil.copytree(exe / "references", root / "references")
    body = re.sub(r"^---\r?\n.*?\r?\n---\r?\n", "", (exe / "SKILL.md").read_text(encoding="utf-8"), count=1, flags=re.S)
    (root / "SKILL.md").write_text("---\nname: demo-scenario\ndescription: 演示场景\n---\n" + body, encoding="utf-8")
    assets = root / "assets"
    demo = REPO / "examples" / "compression-demo"
    shutil.copytree(demo / "tree", assets / "tree")
    shutil.copytree(demo / "scripts", assets / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    asm = load(SK / "cs-scot-generate" / "scripts" / "assemble_scenario.py", "asm_fx")
    (assets / "scripts" / "cross_analyzer.py").write_text(asm.CROSS_SCAFFOLD, encoding="utf-8")
    ig = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(SK / "fault-tree-rca", assets / "fault-tree-rca", ignore=ig)
    shutil.copytree(SK / "zip2csv", assets / "zip2csv", ignore=ig)
    return assets


def append_line(p: Path, line: str) -> None:
    p.write_text(p.read_text(encoding="utf-8") + line, encoding="utf-8")


W = Path(tempfile.mkdtemp(prefix="refresh-min-"))
root = W / "demo-scenario"
assets = mk_skill(root)
mig = load(MIG, "mig_fx")

# 构造旧版文件与内容差异:能力标记齐全但内容不同(模拟被批量适配升级过、比本机更新的文件)
cd = root / "scripts" / "check_data.py"
append_line(cd, "\n# 批量适配升级过的版本多出来的一行\n")
sk = root / "SKILL.md"
append_line(sk, "\n批量适配升级过的正文多一行。\n")
c2c = assets / "zip2csv" / "scripts" / "config2csv.py"
append_line(c2c, "\n# newer\n")
rules = assets / "fault-tree-rca" / "references" / "rca_rules.md"
append_line(rules, "\n新版规则多一行\n")
(root / "references" / "plan_schema.md").unlink()                      # 缺失的文件要补
before = {p: norm(p) for p in (cd, sk, c2c, rules)}

print("\n== 补齐模式(full=False):只补缺失的文件和缺能力标记的旧版文件,已有文件不覆盖 ==")
touched = mig.refresh_engine(assets, full=False)
print("  动了:", touched)
check("① check_data.py 能力标记齐全、内容不同:不覆盖、无 .bak",
      norm(cd) == before[cd] and not cd.with_name("check_data.py.bak").exists())
check("① SKILL.md 正文能力标记齐全、内容不同:不换、无 .bak",
      norm(sk) == before[sk] and not (root / "SKILL.md.bak").exists())
check("① zip2csv 已存在:不动", norm(c2c) == before[c2c] and not c2c.with_name("config2csv.py.bak").exists())
check("① fault-tree-rca 能力标记齐全、内容不同:不整目录刷新",
      norm(rules) == before[rules] and not (assets / "fault-tree-rca.bak").exists())
ck = assets / "scripts" / "condition_kernels.py"
check("② 缺能力标记的旧版 condition_kernels.py 被刷新并留 .bak",
      same(ck, SK / "cs-scot-generate" / "scripts" / "condition_kernels.py")
      and ck.with_name("condition_kernels.py.bak").exists())
check("③ 缺失的 references/plan_schema.md 补齐", (root / "references" / "plan_schema.md").exists())
check("③ 缺失的 custom_lib.py 补齐", (assets / "scripts" / "custom_lib.py").exists())
check("清单里只有补齐与旧版刷新,没有 check_data / SKILL.md / zip2csv / fault-tree-rca",
      not any(k in t for t in touched for k in ("check_data", "SKILL.md", "zip2csv", "fault-tree-rca")), touched)

print("\n== 再按整体刷新(full=True)跑:上面没动的文件全部刷到源版本 ==")
touched = mig.refresh_engine(assets, full=True)
check("④ check_data.py 刷到源版本并留 .bak",
      same(cd, SK / "cs-scot-execute" / "scripts" / "check_data.py") and cd.with_name("check_data.py.bak").exists())
check("④ SKILL.md 正文换成源版本并留 .bak", (root / "SKILL.md.bak").exists()
      and norm(sk).split("---\n", 2)[2] == re.sub(r"^---\r?\n.*?\r?\n---\r?\n", "",
                                                     norm(SK / "cs-scot-execute" / "SKILL.md"), count=1, flags=re.S))
check("④ zip2csv 的 config2csv.py 刷到源版本", same(c2c, SK / "zip2csv" / "scripts" / "config2csv.py"))
check("④ fault-tree-rca 整目录刷新,旧副本 fault-tree-rca.bak",
      same(rules, SK / "fault-tree-rca" / "references" / "rca_rules.md") and (assets / "fault-tree-rca.bak").is_dir())
shutil.rmtree(W, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
