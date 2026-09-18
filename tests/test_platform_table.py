#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台正式全量表适配测试(标准库,直接 python 跑)。表头:
Node_ID | L1_Name_CN | L1_Name_EN | … | Diagnosis_Method_CN | Diagnosis_Method_EN | Solution_CN | Solution_EN
| Product_Obs | Product_Name | Need_Optimize
① 表头识别:Node_ID 忽略、Diagnosis_Method_CN 当判据列、Need_Optimize 当标记列、Solution_CN 当方案列;
   我们自己的调优表(node_id + 判据:/绑定: 列)仍被拒;带 Need_Optimize 的一律按平台表认(备用规则);
② 全 FALSE、表与树一致(ALL 模式)→ 零结构动作,工具链公共文件刷新照做;未标记的 zip 行只汇总一行;
③ 全 FALSE、表里少了一个节点 → 删除;
④ TRUE 行:有包 → 迁移;判据改了没包 → 只同步判据;方案改了 → 同步方案;新路径带包 → 新增;
⑤ TRUE 行判据与包都空 → 清除判据与绑定,节点保留。"""
import csv
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIG = REPO / "skills" / "cs-scot-migrate" / "scripts" / "migrate_fault_check.py"
PASSED = FAILED = 0
SCRIPT = (
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


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


def load_module():
    spec = importlib.util.spec_from_file_location("mig_under_test", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mk_product(d: Path):
    demo = REPO / "examples" / "compression-demo"
    shutil.copytree(demo / "tree", d / "tree")
    shutil.copytree(demo / "scripts", d / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("asm_fx", REPO / "skills/cs-scot-generate/scripts/assemble_scenario.py")
    asm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asm)
    (d / "scripts" / "cross_analyzer.py").write_text(asm.CROSS_SCAFFOLD, encoding="utf-8")


def mk_pkg(store: Path, name: str):
    pkg = store / f"_{name}"
    (pkg / "测试数据" / "网元A" / "CMD").mkdir(parents=True)
    (pkg / "测试数据" / "网元A" / "CMD" / "cmd.txt").write_text("blah KW_X\n", encoding="utf-8")
    (pkg / f"判断方法_{name}.md").write_text(
        f"# 判断方法:{name}\n\n## 节点名称\n{name}\n\n## 故障描述\n演练。\n\n## 判断条件\n1. CMD 中找 KW_X。\n\n"
        "### 命中条件\nA\n\n## 输出\n命中输出故障。\n\n## 数据来源\n| 数据 | 命令 | 目录 |\n|---|---|---|\n| x | display x | CMD/ |\n",
        encoding="utf-8")
    (pkg / f"fault_check_{name}.py").write_text(SCRIPT, encoding="utf-8")
    with zipfile.ZipFile(store / f"{name}.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for q in sorted(pkg.rglob("*")):
            if q.is_file():
                zf.write(q, f"{name}/" + q.relative_to(pkg).as_posix())
    shutil.rmtree(pkg)


def load_tree(product: Path):
    p = next((product / "tree").glob("*.tree_index.json"))
    return json.loads(p.read_text(encoding="utf-8"))["nodes"]


def chain(nodes, nid):
    out = []
    while nid:
        out.append(nodes[nid]["name"])
        nid = nodes[nid].get("parent")
    return out[::-1]


DEPTH = 3
HDR = ["Node_ID"] + [c for i in range(1, DEPTH + 1) for c in (f"L{i}_Name_CN", f"L{i}_Name_EN")] \
    + ["Diagnosis_Method_CN", "Diagnosis_Method_EN", "Solution_CN", "Solution_EN",
       "Product_Obs", "Product_Name", "Need_Optimize"]


def row(names, jd="", sol="", zname="", mark=False):
    """一行:路径名字 + 判据 + 方案 + 包名 + 标记;Node_ID 随机 UUID(技能应整列忽略)。"""
    r = {h: "" for h in HDR}
    r["Node_ID"] = str(uuid.uuid4())
    for i, n in enumerate(names, start=1):
        r[f"L{i}_Name_CN"] = n
        r[f"L{i}_Name_EN"] = f"EN-{n}"
    r.update({"Diagnosis_Method_CN": jd, "Diagnosis_Method_EN": "EN-" + jd if jd else "",
              "Solution_CN": sol, "Solution_EN": "EN-" + sol if sol else "",
              "Product_Obs": f"obs://bucket/{zname}" if zname else "", "Product_Name": zname,
              "Need_Optimize": "TRUE" if mark else "FALSE"})
    return r


def rows_from_tree(nodes):
    """按当前树导出全 FALSE 的全量表行(零改动基线),键 = 路径元组。"""
    return {tuple(chain(nodes, nid)): row(chain(nodes, nid), jd=str(n.get("condition") or ""))
            for nid, n in nodes.items()}


def write_table(path: Path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HDR)
        w.writeheader()
        w.writerows(rows)


def run_all(store, product, table):
    r = subprocess.run([sys.executable, str(MIG), "--package", str(store), "--product", str(product),
                        "--auto", "--map", str(table), "--map-mode", "all"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def node_by_name(nodes, name):
    return next((n for n in nodes.values() if n["name"] == name), None)


mod = load_module()
print("\n== ① 表头识别 ==")
lv, zc, jd, mk, sol = mod._parse_map_header(HDR)
check("认作对应表:三层中文名列、Product_Name 当 zip 列", lv is not None and len(lv) == 3 and zc == "Product_Name", repr((lv, zc)))
check("Diagnosis_Method_CN 当判据列(弃 EN)、Need_Optimize 当标记列、Solution_CN 当方案列",
      (jd, mk, sol) == ("Diagnosis_Method_CN", "Need_Optimize", "Solution_CN"), repr((jd, mk, sol)))
tuning = ["node_id", "L1(Chinese)", "L2(Chinese)", "判据:原文", "绑定:kpi", "Recommend Solution(Chinese)", "meta(勿改)"]
check("我们自己的调优表仍被拒(不当对应表)", mod._parse_map_header(tuning)[0] is None)
fallback = ["node_id", "L1_Name_CN", "L2_Name_CN", "判据:原文", "Product_Name", "Need_Optimize"]
check("备用规则:带 Need_Optimize 的表即使像调优表也按平台表认", mod._parse_map_header(fallback)[0] is not None)

W = Path(tempfile.mkdtemp(prefix="ptable-"))
product, store = W / "prod", W / "store"
mk_product(product)
store.mkdir()
mk_pkg(store, "干扰导致")
mk_pkg(store, "新增节点甲")
table = W / "full.csv"

print("\n== ② 全 FALSE、表与树一致:零结构动作,只刷新工具链公共文件 ==")
nodes0 = load_tree(product)
rows = rows_from_tree(nodes0)
rows[("接入失败示例", "QoS建立失败", "干扰导致")]["Product_Name"] = "干扰导致.zip"      # 有包但 FALSE
rows[("接入失败示例", "QoS建立失败", "干扰导致")]["Product_Obs"] = "obs://bucket/干扰导致.zip"
write_table(table, rows.values())
rc, o = run_all(store, product, table)
check("退出码 0", rc == 0, o[-800:])
check("首轮刷新工具链公共文件(compression-demo 引擎脚本旧代)并说明“只刷新了工具链公共文件”",
      "工具链公共文件刷新" in o and "只刷新了工具链公共文件" in o, o[-800:])
check("未标记的 zip 行只汇总一行,不逐行刷屏", o.count("[标记]") == 1 and "干扰导致" in o.split("[标记]")[1].split("\n")[0], o[-800:])
check("没有 ✓ / + / ✂ / ~ 动作,节点数不变", not any(s in o for s in ("✓ n", "✂ n", "+ n")) and len(load_tree(product)) == len(nodes0), o[-800:])
rc, o = run_all(store, product, table)
check("再跑一遍:工具链公共文件已一致,汇报“没有任何改动”", rc == 0 and "没有任何改动" in o, o[-600:])

print("\n== ③ 全 FALSE、表里少了 License受限 → 删除 ==")
nodes1 = load_tree(product)
rows = rows_from_tree(nodes1)
rows.pop(("接入失败示例", "QoS建立失败", "License受限"))
write_table(table, rows.values())
rc, o = run_all(store, product, table)
nodes2 = load_tree(product)
check("退出码 0,✂ 删除 License受限,节点数减一", rc == 0 and "✂" in o and "License受限" in o
      and len(nodes2) == len(nodes1) - 1 and node_by_name(nodes2, "License受限") is None, o[-800:])

print("\n== ④ TRUE 行:迁移 / 判据同步 / 方案同步 / 新增 ==")
rows = rows_from_tree(nodes2)
r = rows[("接入失败示例", "QoS建立失败", "干扰导致")]
r.update({"Product_Name": "干扰导致.zip", "Product_Obs": "obs://bucket/干扰导致.zip", "Need_Optimize": "TRUE"})
r = rows[("接入失败示例", "QoS建立失败", "弱覆盖")]
r.update({"Diagnosis_Method_CN": "弱覆盖新判据:RSRP 低于门限", "Need_Optimize": "TRUE"})
r = rows[("接入失败示例", "QoS建立失败")]
r.update({"Solution_CN": "方案甲:检查 QoS 配置", "Need_Optimize": "TRUE"})
rows[("接入失败示例", "QoS建立失败", "新增节点甲")] = row(["接入失败示例", "QoS建立失败", "新增节点甲"],
                                                       jd="新节点判据", zname="新增节点甲.zip", mark=True)
write_table(table, rows.values())
rc, o = run_all(store, product, table)
nodes3 = load_tree(product)
check("退出码 0", rc == 0, o[-1000:])
check("干扰导致 迁移落地 ✓", "✓ n3「干扰导致」" in o, o[-800:])
weak = node_by_name(nodes3, "弱覆盖")
check("弱覆盖 判据同步 ~(树里判据已是表的文字)", "判据同步 1" in o and weak and "RSRP 低于门限" in str(weak.get("condition")), o[-800:])
check("QoS建立失败 解决方案同步", "解决方案同步 1" in o, o[-800:])
added = node_by_name(nodes3, "新增节点甲")
check("新增节点甲 新增并绑定脚本 +", "新增节点 1" in o and added is not None
      and "新增节点甲" in (json.loads((product / "tree" / "migrations.json").read_text(encoding="utf-8")).__str__()), o[-800:])

print("\n== ⑤ TRUE 行判据与包都空 → 清除,节点保留 ==")
rows = rows_from_tree(nodes3)
rows[("接入失败示例", "QoS建立失败", "弱覆盖")].update({"Diagnosis_Method_CN": "", "Need_Optimize": "TRUE"})
write_table(table, rows.values())
rc, o = run_all(store, product, table)
nodes4 = load_tree(product)
weak = node_by_name(nodes4, "弱覆盖")
check("清除 1 个节点,弱覆盖判据清空、节点仍在", rc == 0 and "清除 1 个节点" in o and weak is not None
      and not str(weak.get("condition") or "").strip(), o[-800:])
shutil.rmtree(W, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
