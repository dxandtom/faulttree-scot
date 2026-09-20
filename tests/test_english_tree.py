#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""树的英文名与英文判据:生成 → 调优表往返 → 渲染(标准库 + openpyxl 写表,直接 python 跑)。
① 老格式母表(excel_to_tree.py):L<n>(English)、Judgment Condition(English) 存进 name_en / condition_en;
   没有英文列的表一切照旧(不产生这两个字段);
② 新格式母表(excel_to_tree_v2.py,中英双列):同层裸 L n 为英文格、Judgment Condition 英文列 → 存进树,
   英文短名(slug)照常来自英文格;
③ 调优表往返:export 带出 L<n>(English) / Judgment Condition(English) 列 → import 原样读回;
   清掉某节点的中文判据 → 英文判据一并清空;删掉英文列 → 拦下不写盘;
④ 没有英文的产物:调优表不带英文列(表样不变)。"""
import csv
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GEN = REPO / "skills" / "cs-scot-generate" / "scripts"
PASSED = FAILED = 0
W = Path(tempfile.mkdtemp(prefix="entree-"))


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


def run(script, *args):
    r = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(REPO))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def by_name(nodes, name):
    return next((n for n in nodes.values() if n["name"] == name), None)


print("\n== ① 老格式母表:英文列存进 name_en / condition_en ==")
hdr = ["L1(Chinese)", "L1(English)", "L2(Chinese)", "L2(English)", "L3(Chinese)", "L3(English)",
       "Judgment Condition(Chinese)", "Judgment Condition(English)"]
rows = [
    ["大类", "Category", "接入失败", "Access Failure", "", "", "", ""],
    ["大类", "Category", "接入失败", "Access Failure", "弱覆盖", "Weak Coverage", "RSRP 低于 -110", "RSRP below -110"],
    ["大类", "Category", "接入失败", "Access Failure", "干扰", "", "SINR 低于 0", ""],          # 英文格空:不存
]
csv_p = W / "v1.csv"
with csv_p.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(hdr)
    w.writerows(rows)
rc, o = run(GEN / "excel_to_tree.py", csv_p, "--start-node", "接入失败", "-o", W / "v1" / "t.md")
idx = json.loads((W / "v1" / "t.tree_index.json").read_text(encoding="utf-8"))["nodes"]
check("退出码 0 并提示带英文名节点数", rc == 0 and "带英文名节点 2" in o, o[-500:])
root, weak, intf = by_name(idx, "接入失败"), by_name(idx, "弱覆盖"), by_name(idx, "干扰")
check("场景根与叶子存了 name_en", root and root.get("name_en") == "Access Failure" and weak and weak.get("name_en") == "Weak Coverage", repr((root, weak)))
check("英文判据存进 condition_en,中文判据不变", weak.get("condition_en") == "RSRP below -110" and weak.get("condition") == "RSRP 低于 -110")
check("英文格为空的节点不产生 name_en / condition_en", intf and "name_en" not in intf and "condition_en" not in intf, repr(intf))
with csv_p.open("w", encoding="utf-8-sig", newline="") as f:                      # 没有英文列的老表
    w = csv.writer(f)
    w.writerow([hdr[0], hdr[2], hdr[4], hdr[6]])
    w.writerows([[r[0], r[2], r[4], r[6]] for r in rows])
rc, o = run(GEN / "excel_to_tree.py", csv_p, "--start-node", "接入失败", "-o", W / "v1b" / "t.md")
idx_b = json.loads((W / "v1b" / "t.tree_index.json").read_text(encoding="utf-8"))["nodes"]
check("没有英文列的表:一切照旧,不产生英文字段", rc == 0 and not any("name_en" in n or "condition_en" in n for n in idx_b.values()))

print("\n== ② 新格式母表(中英双列):英文格与英文判据列存进树 ==")
from openpyxl import Workbook
wb = Workbook()
ws = wb.active
ws.title = "NSA NR"
ws.append(["L1", "L1(Chinese)", "L2", "L2(Chinese)", "L3", "L3(Chinese)",
           "Judgment Condition", "Judgment Condition(Chinese)", "Recommend Solution(Chinese)", "Recommend Solution(English)"])
ws.append(["Category", "大类", "Layer 3 Service Issues", "三层业务问题", "", "", "", "", "", ""])
ws.append(["Category", "大类", "Layer 3 Service Issues", "三层业务问题", "Route Missing", "路由缺失",
           "display ip routing-table has no default route", "display ip routing-table 无默认路由", "补默认路由", "Add a default route"])
ws.append(["Category", "大类", "Layer 3 Service Issues", "三层业务问题", "", "ARP 学习失败", "", "ARP 表无对端", "", ""])
xlsx_p = W / "v2.xlsx"
wb.save(xlsx_p)
rc, o = run(GEN / "excel_to_tree_v2.py", xlsx_p, "--start-node", "三层业务问题", "-o", W / "v2" / "t.md")
check("退出码 0、英文短名来自英文格、提示带英文名节点数", rc == 0 and "layer-3-service-issues" in o and "带英文名节点 2" in o, o[-600:])
idx2 = json.loads((W / "v2" / "t.tree_index.json").read_text(encoding="utf-8"))["nodes"]
r2, rm, arp = by_name(idx2, "三层业务问题"), by_name(idx2, "路由缺失"), by_name(idx2, "ARP 学习失败")
check("同层英文格 → name_en;英文判据列 → condition_en;中文判据带源标签不变",
      r2.get("name_en") == "Layer 3 Service Issues" and rm.get("name_en") == "Route Missing"
      and rm.get("condition_en") == "display ip routing-table has no default route" and "[通用] display ip routing-table 无默认路由" == rm.get("condition"),
      repr((r2, rm)))
check("英文格空的节点没有英文字段", arp and "name_en" not in arp and "condition_en" not in arp, repr(arp))
sol2 = json.loads((W / "v2" / "solutions.json").read_text(encoding="utf-8"))
check("解决方案中英照常进 solutions.json", any(v == {"zh": "补默认路由", "en": "Add a default route"} for v in sol2.values()), repr(sol2))

print("\n== ③ 调优表往返:英文列导出 → 导入读回;清中文判据则英文判据清空;删英文列被拦 ==")
prod = W / "prod"
demo = REPO / "examples" / "compression-demo"
shutil.copytree(demo / "tree", prod / "tree")
shutil.copytree(demo / "scripts", prod / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
ti_p = next((prod / "tree").glob("*.tree_index.json"))
ti = json.loads(ti_p.read_text(encoding="utf-8"))
nodes = ti["nodes"]
n_weak = next(k for k, n in nodes.items() if n["name"] == "弱覆盖")
n_root = ti["root"]
nodes[n_root]["name_en"] = "Access Failure Demo"
nodes[n_weak]["name_en"] = "Weak Coverage"
nodes[n_weak]["condition_en"] = "RSRP below threshold"
ti_p.write_text(json.dumps(ti, ensure_ascii=False, indent=2), encoding="utf-8")
(prod / "tree" / "solutions.json").write_text(json.dumps({n_weak: {"zh": "调功率", "en": "Adjust power"}}, ensure_ascii=False), encoding="utf-8")
rc, o = run(GEN / "export_tree_excel.py", "--out", prod)
xls = next((prod / "tree").glob("*.tuning.xlsx"))
from openpyxl import load_workbook
wbx = load_workbook(xls)
wsx = wbx.active
hdr_x = [str(c.value or "") for c in wsx[1]]
check("导出退出码 0,表头带 L<n>(English) 与 Judgment Condition(English)", rc == 0 and "L1(English)" in hdr_x
      and "L3(English)" in hdr_x and "Judgment Condition(English)" in hdr_x, repr(hdr_x))
col = {h: i + 1 for i, h in enumerate(hdr_x)}
row_weak = next(r for r in range(2, wsx.max_row + 1) if str(wsx.cell(row=r, column=col["node_id"]).value) == n_weak)
check("弱覆盖行:自己那层的英文格、祖先的英文格、英文判据格都填了",
      wsx.cell(row=row_weak, column=col["L3(English)"]).value == "Weak Coverage"
      and wsx.cell(row=row_weak, column=col["L1(English)"]).value == "Access Failure Demo"
      and wsx.cell(row=row_weak, column=col["Judgment Condition(English)"]).value == "RSRP below threshold")
rc, o = run(GEN / "import_tree_excel.py", "--out", prod)
nodes_b = json.loads(ti_p.read_text(encoding="utf-8"))["nodes"]
check("零改动导入:英文原样读回,其余节点仍无英文字段", rc == 0 and nodes_b[n_weak].get("name_en") == "Weak Coverage"
      and nodes_b[n_weak].get("condition_en") == "RSRP below threshold" and nodes_b[n_root].get("name_en") == "Access Failure Demo"
      and not any("name_en" in n for k, n in nodes_b.items() if k not in (n_weak, n_root)), o[-500:])
wbx = load_workbook(xls)
wsx = wbx.active
raw_cols = [i + 1 for i, h in enumerate(hdr_x) if h.startswith("判据:")]
for c in raw_cols:                                                                # 清掉弱覆盖的中文判据(所有判据列)
    wsx.cell(row=row_weak, column=c).value = None
wbx.save(xls)
rc, o = run(GEN / "import_tree_excel.py", "--out", prod)
nodes_c = json.loads(ti_p.read_text(encoding="utf-8"))["nodes"]
check("清掉中文判据 → 英文判据一并清空,英文名保留", rc == 0 and not nodes_c[n_weak].get("condition")
      and "condition_en" not in nodes_c[n_weak] and nodes_c[n_weak].get("name_en") == "Weak Coverage"
      and "英文名/英文判据改动 1" in o, o[-500:])
wbx = load_workbook(xls)
wsx = wbx.active
wsx.delete_cols(col["L1(English)"], 1)                                            # 误删英文名列
wbx.save(xls)
before = ti_p.read_text(encoding="utf-8")
rc, o = run(GEN / "import_tree_excel.py", "--out", prod)
check("删掉一层英文名列 → 拦下(退出码 2),一个文件都不写", rc == 2 and "L1(English)" in o and ti_p.read_text(encoding="utf-8") == before, o[-400:])

print("\n== ④ 没有英文的产物:调优表不带英文列 ==")
prod2 = W / "prod2"
shutil.copytree(demo / "tree", prod2 / "tree")
shutil.copytree(demo / "scripts", prod2 / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
rc, o = run(GEN / "export_tree_excel.py", "--out", prod2)
hdr2 = [str(c.value or "") for c in load_workbook(next((prod2 / "tree").glob("*.tuning.xlsx")), read_only=True).active[1]]
check("表头没有任何英文列", rc == 0 and not any("(English)" in h and h.startswith("L") for h in hdr2) and "Judgment Condition(English)" not in hdr2, repr(hdr2))

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
shutil.rmtree(W, ignore_errors=True)
sys.exit(1 if FAILED else 0)
