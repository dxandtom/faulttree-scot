#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无线版生成技能 cs-scot-generate-wireless 的测试(标准库,直接 python 跑):
① 无线版内核自测全过,且 B/E/H 三类可用;数通版 cs-scot-generate 对 B/E/H 仍按封存拒收(两版分开);
② 无线版与数通版的脚本清单一致(只是内核集与派发示例不同),引用的参考文档都存在;
③ 用示例母表(老格式)建树 → 无线版 assemble 组装 → 产物里的内核副本带 B/E/H,调优表自动导出。"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WL = REPO / "skills" / "cs-scot-generate-wireless"
DC = REPO / "skills" / "cs-scot-generate"
PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


def run(script: Path, *args):
    r = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("① 内核集:无线版有 B/E/H,数通版仍封存")
rc, out = run(WL / "scripts" / "kernel_selftest.py")
check("无线版内核自测全过", rc == 0 and "全部通过" in out, out[-300:])
rc, out = run(DC / "scripts" / "kernel_selftest.py")
check("数通版内核自测全过", rc == 0 and "全部通过" in out, out[-300:])
kw = load(WL / "scripts" / "condition_kernels.py", "kw")
kd = load(DC / "scripts" / "condition_kernels.py", "kd")
tbl = (["att", "succ"], [{"att": "10", "succ": "7"}])
b = {"class": "B", "minuend": ["att"], "subtrahend": ["succ"], "op": ">", "threshold": 0}
fw = kw.evaluate_binding(b, {"kpi": tbl}, "kpi")
fd = kd.evaluate_binding(b, {"kpi": tbl}, "kpi")
check("无线版 B 类算出差值 3 且成立", fw.get("status") == "ok" and fw.get("value") == 3.0 and fw.get("predicate_satisfied") is True, fw)
check("数通版 B 类按封存拒收(数据不足并指路)", fd.get("status") == "insufficient_data" and "封存" in str(fd.get("reason")), fd)
check("两版的现役类别集合", set(kw._KERNELS) == {"A", "B", "C", "E", "G", "H", "P"} and set(kd._KERNELS) == {"A", "C", "G", "P"},
      (sorted(kw._KERNELS), sorted(kd._KERNELS)))

print("② 两版脚本清单一致、参考文档齐全")
ws = sorted(p.name for p in (WL / "scripts").glob("*.py"))
ds = sorted(p.name for p in (DC / "scripts").glob("*.py"))
check("脚本文件名清单相同", ws == ds, (set(ws) ^ set(dc for dc in ds)))
sk = (WL / "SKILL.md").read_text(encoding="utf-8")
check("frontmatter 名字是 cs-scot-generate-wireless", "name: cs-scot-generate-wireless" in sk.splitlines()[1])
check("无线数据源说明存在且被 SKILL.md 引用", (WL / "references" / "wireless_sources.md").exists() and "wireless_sources.md" in sk)
check("不再引用数通接入说明", not (WL / "references" / "datacom_log_adapter.md").exists()
      and "datacom_log_adapter" not in (sk + "".join(p.read_text(encoding="utf-8") for p in (WL / "references").glob("*.md"))))
brief = (WL / "scripts" / "make_dispatch.py").read_text(encoding="utf-8")
check("派发任务说明的示例按无线写(含 B/E/H)", '"class":"B"' in brief and '"class":"E"' in brief and '"class":"H"' in brief and "display vxlan" not in brief)

print("③ 老格式母表建树 → 无线版组装")
tmp = Path(tempfile.mkdtemp(prefix="wireless_skill_"))
try:
    prod = tmp / "prod"
    (prod / "tree").mkdir(parents=True)
    rc, out = run(WL / "scripts" / "excel_to_tree.py", str(REPO / "examples" / "compression-demo" / "fault_tree.csv"),
                  "--start-node", "接入失败示例", "-o", str(prod / "tree" / "接入失败示例.md"))
    check("老格式母表建树成功", rc == 0 and (prod / "tree" / "接入失败示例.tree_index.json").exists(), out[-300:])
    rc, out = run(WL / "scripts" / "assemble_scenario.py", "--scenario", "接入失败示例", "--indicators", "kpi,chr", "--out", str(prod))
    check("组装成功", rc == 0, out[-400:])
    kern = (prod / "scripts" / "condition_kernels.py").read_text(encoding="utf-8") if (prod / "scripts" / "condition_kernels.py").exists() else ""
    check("产物里的内核副本带 B/E/H", "def k_signaling" in kern and "def k_difference" in kern and "def k_proportion" in kern)
    check("调优表自动导出", any((prod / "tree").glob("*.tuning.xlsx")))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
