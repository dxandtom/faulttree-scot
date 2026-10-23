#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据入口检查 check_data.py 的通用表识别测试(标准库,直接 python 跑):
① 无线的话统/CHR 宽表(表头不是数通三表)→ 认作通用数据表,退出码 0,给出按指标名的传入方式;混在目录里的故障树母表不算数据;
② 技能形态(scripts/check_data.py 旁有 assets/plan.json)→ 文件名对上指标名,建议里直接给 --data "chr=…";
③ 数通三表仍按表头认(CmdName → cmd);
④ 目录里只有采集清单 csv → 清单点名、不当数据,退出码 2;
⑤ 空目录 → 退出码 2。"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "skills" / "cs-scot-execute" / "scripts" / "check_data.py"
DEMO = REPO / "examples" / "compression-demo"
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


tmp = Path(tempfile.mkdtemp(prefix="check_data_"))
try:
    print("① 无线宽表 → 通用数据表")
    d1 = tmp / "wireless"
    d1.mkdir()
    shutil.copy2(DEMO / "data" / "chr.csv", d1 / "chr.csv")
    shutil.copy2(DEMO / "data" / "kpi.csv", d1 / "kpi.csv")
    shutil.copy2(DEMO / "fault_tree.csv", d1 / "fault_tree.csv")
    rc, out = run(CHECK, str(d1))
    check("退出码 0", rc == 0, out[-500:])
    check("两张表都认作通用数据表", "通用数据表" in out and "chr.csv" in out and "kpi.csv" in out, out[-500:])
    check("故障树母表不算数据", "fault_tree.csv" not in out, out[-500:])
    check("给出按指标名的传入方式", '--data "<指标>=<表路径>"' in out, out[-500:])

    print("② 技能形态:文件名对上 plan 里的指标")
    skill = tmp / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "assets").mkdir()
    shutil.copy2(CHECK, skill / "scripts" / "check_data.py")
    (skill / "assets" / "plan.json").write_text(json.dumps({"nodes": [
        {"id": "chr", "kind": "script", "name": "chr_analyzer"},
        {"id": "kpi", "kind": "script", "name": "kpi_analyzer"},
        {"id": "cross", "kind": "script", "name": "cross_analyzer"},
        {"id": "rca", "kind": "agent_skill", "name": "fault-tree-rca"}]}, ensure_ascii=False), encoding="utf-8")
    d2 = tmp / "data2"
    d2.mkdir()
    shutil.copy2(DEMO / "data" / "chr.csv", d2 / "chr_20260101.csv")
    shutil.copy2(DEMO / "data" / "kpi.csv", d2 / "other.csv")
    rc, out = run(skill / "scripts" / "check_data.py", str(d2))
    check("退出码 0", rc == 0, out[-500:])
    check("chr_20260101.csv 对上指标 chr", "对上指标「chr」" in out, out[-500:])
    check("other.csv 点名对不上任何指标", "other.csv" in out and "对不上本技能的任何指标" in out, out[-500:])
    check("建议里直接给 --data \"chr=…\"", '--data "chr=' in out, out[-500:])

    print("③ 数通三表仍按表头认")
    d3 = tmp / "datacom"
    d3.mkdir()
    (d3 / "cmd.csv").write_text("NEName,CmdName,CmdResult\nSW1,display stp,ok\n", encoding="utf-8")
    rc, out = run(CHECK, str(d3))
    check("退出码 0 且认作 cmd 表", rc == 0 and "像 cmd 表" in out, out[-500:])
    check("不再当通用表重复报", "通用数据表" not in out, out[-500:])

    print("④ 只有采集清单")
    d4 = tmp / "manifest"
    d4.mkdir()
    (d4 / "list.csv").write_text("neName,fileType,relativePath,zipFile\nSW1,CMD,a/b.txt,x.zip\n", encoding="utf-8")
    rc, out = run(CHECK, str(d4))
    check("清单点名、退出码 2", rc == 2 and "采集清单" in out, out[-500:])

    print("⑤ 空目录")
    d5 = tmp / "empty"
    d5.mkdir()
    rc, out = run(CHECK, str(d5))
    check("退出码 2", rc == 2, out[-300:])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
