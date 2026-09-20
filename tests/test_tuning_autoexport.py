#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调优表自动导出测试(标准库 + openpyxl,直接 python 跑):
① assemble 组装结束自动导出 tree/<短名>.tuning.xlsx,一行一节点,打印"调优表已导出"并提示绑定列待步骤 2.5 刷新;
② merge_conditions 把绑定写进 analyzer 后自动刷新调优表,绑定列出现刚合并的绑定,不产生 .bak;
③ 调优表比 tree_index 新(可能有未导入的人工修改)→ 刷新前备份 .xlsx.bak 并转发导出器的提示;
④ 写 xlsx 用的 openpyxl 缺失 → 只提示"调优表未导出"并给手动命令,合并本身照常成功、退出码不变,原表不动;
⑤ 产物里没有 scripts/export_tree_excel.py(旧版组装的产物)→ 提示并跳过,退出码不变;
⑥ 没有任何批次写入(结果文件未到)→ 不刷新调优表,原表原样,退出码仍是未完成的 1;
⑦ assemble --keep 重跑保留已填绑定 → 调优表照样反映现有绑定,且不再提示"待步骤 2.5 刷新"。"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GEN = REPO / "skills" / "cs-scot-generate" / "scripts"
DEMO = REPO / "examples" / "compression-demo" / "tree"
STEM = "接入失败示例"
PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


def run(script: Path, *args, env=None):
    """跑一个脚本,返回 (退出码, stdout+stderr 合并文本)。"""
    r = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def read_table(xlsx: Path) -> dict:
    """调优表 → {node_id: {表头: 值}}。"""
    from openpyxl import load_workbook
    ws = load_workbook(xlsx, read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    hdr = list(rows[0])
    return {r[0]: dict(zip(hdr, r)) for r in rows[1:] if r and r[0]}


def merge(prod: Path, env=None):
    return run(GEN / "merge_conditions.py", "--out", str(prod), env=env)


def assemble(prod: Path, *extra):
    return run(GEN / "assemble_scenario.py", "--scenario", STEM, "--indicators", "chr,kpi",
               "--out", str(prod), *extra)


tmp = Path(tempfile.mkdtemp(prefix="tuning_autoexport_"))
try:
    prod = tmp / "prod"
    (prod / "tree").mkdir(parents=True)
    for f in DEMO.iterdir():
        shutil.copy2(f, prod / "tree" / f.name)
    xlsx = prod / "tree" / f"{STEM}.tuning.xlsx"
    bak = xlsx.with_suffix(".xlsx.bak")
    index = prod / "tree" / f"{STEM}.tree_index.json"

    print("① 组装结束自动导出")
    rc, out = assemble(prod)
    check("assemble 退出码 0", rc == 0, out[-400:])
    check("tree/<短名>.tuning.xlsx 已出现", xlsx.exists())
    check("打印 调优表已导出 并提示绑定列待步骤 2.5 刷新", "调优表已导出(绑定列待步骤 2.5 合并后刷新)" in out)
    n_nodes = len(json.loads(index.read_text(encoding="utf-8"))["nodes"])
    tbl = read_table(xlsx)
    check(f"一行一节点({n_nodes} 行)", len(tbl) == n_nodes, str(len(tbl)))
    check("此时绑定列为空", all(not str(r.get("绑定:chr") or "").strip() for r in tbl.values()))

    print("② 合并绑定后自动刷新")
    ddir = prod / "dispatch"
    ddir.mkdir()
    (ddir / "manifest.json").write_text('{"complete_marker": true}', encoding="utf-8")
    (ddir / "chr_batch1.txt").write_text("任务说明(测试桩)", encoding="utf-8")
    result = ddir / "chr_batch1_result.py"
    result.write_text(
        "CONDITIONS = [\n"
        "    {'node_id': 'n4', 'class': 'A', 'column': 'RelRsrp', 'op': '<', 'threshold': -120},\n"
        "    {'node_id': 'n3', 'class': 'C', 'column': 'DCErrType', 'contains': 'RNL_Unspecified'},\n"
        "]\n# BATCH_COMPLETE\n", encoding="utf-8")
    rc, out = merge(prod)
    check("merge 退出码 0", rc == 0, out[-400:])
    check("打印 调优表已按最新绑定刷新", "调优表已按最新绑定刷新" in out)
    tbl = read_table(xlsx)
    check("n4 的 绑定:chr 含刚合并的 RelRsrp", "RelRsrp" in str(tbl["n4"].get("绑定:chr") or ""))
    check("n3 的 绑定:chr 含刚合并的 RNL_Unspecified", "RNL_Unspecified" in str(tbl["n3"].get("绑定:chr") or ""))
    check("表与 index 同步,没有 .bak", not bak.exists())

    print("③ 表比 index 新 → 先备份再刷新")
    t = index.stat().st_mtime + 5
    os.utime(xlsx, (t, t))
    rc, out = merge(prod)
    check("merge 退出码 0", rc == 0, out[-400:])
    check("产生 .xlsx.bak", bak.exists())
    check("转发导出器的备份提示", "已备份到" in out and bak.name in out)

    print("④ 缺 openpyxl → 只提示不阻断")
    fake = tmp / "fake_site"
    fake.mkdir()
    (fake / "openpyxl.py").write_text("raise ImportError('测试桩:模拟未安装 openpyxl')\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(fake) + os.pathsep + env.get("PYTHONPATH", "")
    before = xlsx.read_bytes()
    rc, out = merge(prod, env=env)
    check("merge 仍退出码 0", rc == 0, out[-400:])
    check("提示 调优表未导出 并给手动命令", "⚠ 调优表未导出" in out and "手动跑" in out and "openpyxl" in out)
    check("原表未被破坏", xlsx.read_bytes() == before)

    print("⑤ 产物里没有 export_tree_excel.py → 提示并跳过")
    exp = prod / "scripts" / "export_tree_excel.py"
    moved = tmp / "export_tree_excel.py.moved"
    exp.rename(moved)
    rc, out = merge(prod)
    check("merge 仍退出码 0", rc == 0, out[-400:])
    check("提示 产物里没有 scripts/export_tree_excel.py", "调优表未导出:产物里没有 scripts/export_tree_excel.py" in out)
    moved.rename(exp)

    print("⑥ 没有批次写入 → 不刷新")
    result_moved = tmp / "chr_batch1_result.py.moved"
    result.rename(result_moved)
    before = xlsx.read_bytes()
    rc, out = merge(prod)
    check("有未完成项,退出码 1", rc == 1, out[-400:])
    check("没有写入就不导出", "调优表已" not in out and "调优表未导出" not in out)
    check("原表原样", xlsx.read_bytes() == before)
    result_moved.rename(result)

    print("⑦ assemble --keep 重跑 → 表反映保留的绑定")
    rc, out = assemble(prod, "--keep")
    check("assemble 退出码 0", rc == 0, out[-400:])
    check("打印 调优表已导出 且不再提示待步骤 2.5 刷新", "调优表已导出:" in out and "待步骤 2.5" not in out)
    tbl = read_table(xlsx)
    check("保留下来的绑定仍在表里", "RelRsrp" in str(tbl["n4"].get("绑定:chr") or ""))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
