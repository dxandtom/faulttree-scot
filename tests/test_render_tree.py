#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fault-tree-rca 树视图与聊天框回复稿(render_tree / render_report --tree)测试。标准库,直接 python 跑。
① 仓库样例 rca.json(疑似,卡点在中间层):路径展开、卡点符号、其余分支折叠;
② 合成场景:确诊落中间层(子树到叶子)+ 缺数据(子树折叠)+ 已知不成立兄弟单列 + 折叠;
③ 不截断:长判据全文保留、多行判据续行悬挂缩进;
④ render_report --tree:报告首节树视图、rca_tree.txt、rca_reply.md(树视图代码块+确证表+疑似表+待确认);
⑤ --lang en:节点名用 name_en、判据用 condition_en、方案用 solutions.json 的 en,固定文案英文,
   没有英文的节点回退中文,证据原文不译;报告与回复稿的标题/表头/判定词同步英文;zh 输出一字不变。"""
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RT = REPO / "skills" / "fault-tree-rca" / "scripts" / "render_tree.py"
RR = REPO / "skills" / "fault-tree-rca" / "scripts" / "render_report.py"
spec = importlib.util.spec_from_file_location("rt", RT)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)
WORK = Path(tempfile.mkdtemp(prefix="rtree-"))
PASSED = FAILED = 0
FENCE = "`" * 3


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


print("\n== ① 样例(compression-demo):疑似,卡点在中间层 ==")
doc = json.loads((REPO / "examples/compression-demo/sample_output/rca.json").read_text(encoding="utf-8"))
nodes, root = rt._load_tree(REPO / "examples/compression-demo/tree/接入失败示例.tree_index.json")
txt = rt.render_tree(doc, nodes, root)
print(txt)
check("头行计数", txt.startswith("树视图:确诊 0 · 疑似 1 · 缺数据 0"))
check("疑似节点带标签与符号", "⚠ 干扰导致  [疑似]" in txt)
check("卡点上级按证据符号(数据不足 ?)", "? QoS建立失败" in txt)
check("详情:判据/证据/卡点", "判据:DCErrType" in txt and "证据:RNL_Unspecified 命中 4 条" in txt
      and "卡点:QoS建立失败(n2)" in txt)
n_sib = len([k for k in nodes if nodes[k].get("parent") == "n2"]) - 1
check("其余兄弟折叠计数", f"…(其余 {n_sib} 个分支未涉及)" in txt)

print("\n== ② 合成:确诊中间层 + 缺数据 + 不成立兄弟 + 折叠 ==")
LONG_COND = ("需同时满足:\n1、接口 GigabitEthernet0/0/1 的 display current-configuration interface 回显中存在 qos car inbound 配置且 cir 值小于 1000"
             "\n2、display qos car statistics 中 pass 计数持续为 0 且 discard 计数递增\n3、vlan 参数缺失")
tree = {"root": "r", "nodes": {
    "r": {"name": "根", "parent": None, "children": ["a", "b", "c", "d"]},
    "a": {"name": "分支A", "parent": "r", "children": ["a1", "a2"], "condition": LONG_COND},
    "a1": {"name": "A叶1", "parent": "a", "children": [], "condition": "A1判据"},
    "a2": {"name": "A叶2", "parent": "a", "children": [], "condition": "A2判据"},
    "b": {"name": "分支B", "parent": "r", "children": ["b1"], "condition": "B判据"},
    "b1": {"name": "B叶1", "parent": "b", "children": [], "condition": "B1判据"},
    "c": {"name": "分支C", "parent": "r", "children": ["c1", "c2", "c3"], "condition": "C判据"},
    "c1": {"name": "C叶1", "parent": "c", "children": [], "condition": "C1判据"},
    "c2": {"name": "C叶2", "parent": "c", "children": [], "condition": "C2判据"},
    "c3": {"name": "C叶3", "parent": "c", "children": [], "condition": "C3判据"},
    "d": {"name": "分支D", "parent": "r", "children": [], "condition": "D判据"},
}}
doc2 = {
    "rca_result": [{"Root_cause": "分支A", "node_id": "a", "Fault_tree_path": ["根", "分支A"],
                    "recommend_solution": "换板", "caveats": "叶子因待定",
                    "evidence_path": [{"node": "根", "node_id": "r", "judgment": "中间节点,无独立条件"},
                                      {"node": "分支A", "node_id": "a", "condition": LONG_COND,
                                       "actual": "A 命中 3 条:qos car inbound cir 500 | pass 0 discard 1200 | vlan 缺",
                                       "how": "display a", "judgment": "成立"}]}],
    "probable_causes": [],
    "undetermined": [{"Fault_tree_path": ["分支C"], "blocked_at": "分支C(c)", "reason": "缺 display c 采集"}],
    "excluded_note": "「分支B」(b):B 实测 0 条,不成立",
}
txt2 = rt.render_tree(doc2, tree["nodes"], "r")
print(txt2)
check("确诊中间层:子树展开到叶子(未判定 ·)", "✅ 分支A  [确诊]" in txt2 and "· A叶1" in txt2 and "· A叶2" in txt2)
check("确诊详情:证据/依据/方案/注意全在", all(k in txt2 for k in ("证据:A 命中 3 条", "依据:display a", "方案:换板", "注意:叶子因待定")))
check("缺数据节点:子树折叠计数,不展开", "? 分支C  [缺数据]" in txt2 and "子树 3 个节点未判定" in txt2 and "C叶1" not in txt2)
check("已知不成立兄弟单列一行(来自排除留痕)", "✗ 分支B" in txt2 and "B叶1" not in txt2)
check("未涉及兄弟折叠", "…(其余 1 个分支未涉及)" in txt2 and "分支D" not in txt2)

print("\n== ③ 详情摘要:多行判据压成一行(去空行/空白),限长指向报告,超宽折行悬挂缩进 ==")
lines2 = txt2.splitlines()
jd = [l for l in lines2 if "判据:需同时满足" in l]
check("判据压成一行摘要(编号条目用;连,无空行)", len(jd) == 1 and "需同时满足:1、接口" in jd[0]
      and ";2、disp" in txt2 and "\n\n" not in txt2.split("\n\n", 1)[1], repr(jd))   # 头行后一个空行,树体内无空行
check("判据超过 120 字截断并指向报告全文", "…(详见报告全文)" in txt2)
ev_i = next(i for i, l in enumerate(lines2) if "证据:A 命中 3 条" in l)
check("证据一行(未超长不截断)", "vlan 缺" in lines2[ev_i] and "…" not in lines2[ev_i], lines2[ev_i])
long_line = next((l for l in lines2 if len(l) > 90), None)
check("单行 ≤ 90 字符(超宽已折行)", long_line is None, repr(long_line))
cont = lines2[lines2.index(jd[0]) + 1]
check("折行续行悬挂缩进(不带标签、比首行正文起点不早)",
      "判据" not in cont and cont.startswith("│") and (len(cont) - len(cont.lstrip("│ "))) >= 8, repr(cont))

print("\n== ④ render_report --tree:报告 + rca_tree.txt + rca_reply.md ==")
rca_p = WORK / "rca.json"
rca_p.write_text(json.dumps(doc2, ensure_ascii=False), encoding="utf-8")
tree_p = WORK / "x.tree_index.json"
tree_p.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
p = subprocess.run([sys.executable, str(RR), str(rca_p), "--tree", str(tree_p)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO))
md = (WORK / "rca_report.md").read_text(encoding="utf-8") if (WORK / "rca_report.md").exists() else ""
check("退出码 0", p.returncode == 0, p.stderr[-400:])
check("报告含树视图节且在确证节之前", "## 树视图" in md and md.index("## 树视图") < md.index("## 确证根因"))
check("rca_tree.txt 已写出", (WORK / "rca_tree.txt").exists()
      and "✅ 分支A  [确诊]" in (WORK / "rca_tree.txt").read_text(encoding="utf-8"))
rp = (WORK / "rca_reply.md").read_text(encoding="utf-8") if (WORK / "rca_reply.md").exists() else ""
print(rp)
check("回复稿已写出且头行有计数", rp.startswith("**诊断结论:确证根因 1 条 · 疑似 0 条 · 待确认 1 条**"))
check("回复稿:树视图在 text 代码块里", (FENCE + "text") in rp and "✅ 分支A  [确诊]" in rp)
check("回复稿:确证根因表(根因/路径/原文匹配/方案)",
      "| # | 根因 | 根因路径 | 原文匹配(实测 / 依据) | 推荐方案 |" in rp
      and "| 1 | 分支A | 根 → 分支A | A 命中 3 条:qos car inbound cir 500 \\| pass 0 discard 1200 \\| vlan 缺 / display a | 换板 |" in rp,
      rp[-700:])
check("回复稿:待确认列表", "- 分支C:缺 display c 采集" in rp)
check("回复稿:末尾补充说明占位", rp.rstrip().endswith("数字以上文为准)_"))
check("[回复稿] 提示打印", "[回复稿]" in p.stdout)
p2 = subprocess.run([sys.executable, str(RR), str(rca_p), "-o", str(WORK / "plain.md")],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO))
check("不带 --tree:报告无树视图节,回复稿照出(无树)", p2.returncode == 0
      and "## 树视图" not in (WORK / "plain.md").read_text(encoding="utf-8")
      and (FENCE + "text") not in (WORK / "rca_reply.md").read_text(encoding="utf-8"))

print("\n== ⑤ --lang en:英文树视图 / 报告 / 回复稿,缺英文回退中文 ==")
tree_en = json.loads(json.dumps(tree, ensure_ascii=False))
for nid, en in (("r", "Root"), ("a", "Branch A"), ("a1", "A leaf 1"), ("c", "Branch C"), ("d", "Branch D")):
    tree_en["nodes"][nid]["name_en"] = en                                # 分支B / A叶2 故意不给英文 → 回退中文
tree_en["nodes"]["a"]["condition_en"] = "All of: 1. qos car inbound cir < 1000; 2. pass stays 0 and discard grows; 3. vlan missing"
sols = {"a": {"zh": "换板", "en": "Replace the board"}}
txt_en = rt.render_tree(doc2, tree_en["nodes"], "r", lang="en", solutions=sols)
print(txt_en)
check("英文头行", txt_en.startswith("Tree view: confirmed 1 · probable 0 · missing data 1 (only paths"))
check("节点名用 name_en、标签英文", "✅ Branch A  [Confirmed]" in txt_en and "· A leaf 1" in txt_en and "? Branch C  [Missing data]" in txt_en)
check("没有英文的节点回退中文", "✗ 分支B" in txt_en and "· A叶2" in txt_en)
check("判据用 condition_en、方案用 solutions.json 的 en、证据原文不译",
      "Criterion:All of: 1. qos car inbound cir < 1000" in txt_en and "Solution:Replace the board" in txt_en
      and "Evidence:A 命中 3 条" in txt_en and "Basis:display a" in txt_en and "Caveat:叶子因待定" in txt_en)
check("折叠提示英文", "…(1 more branches not involved)" in txt_en and "…(3 nodes in subtree not evaluated; rerun after data is supplied)" in txt_en
      and "Missing:缺 display c 采集" in txt_en)
check("zh 输出不受影响(与 ② 逐字相同)", rt.render_tree(doc2, tree_en["nodes"], "r") == txt2)
tree_en_p = WORK / "en" / "x.tree_index.json"
tree_en_p.parent.mkdir()
tree_en_p.write_text(json.dumps(tree_en, ensure_ascii=False), encoding="utf-8")
(WORK / "en" / "solutions.json").write_text(json.dumps(sols, ensure_ascii=False), encoding="utf-8")
rca_en_p = WORK / "en" / "rca.json"
rca_en_p.write_text(json.dumps(doc2, ensure_ascii=False), encoding="utf-8")
p3 = subprocess.run([sys.executable, str(RR), str(rca_en_p), "--tree", str(tree_en_p), "--lang", "en"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(REPO))
md_en = (WORK / "en" / "rca_report.md").read_text(encoding="utf-8")
rp_en = (WORK / "en" / "rca_reply.md").read_text(encoding="utf-8")
print(rp_en)
check("退出码 0", p3.returncode == 0, p3.stderr[-400:])
check("报告:英文标题、树视图节、确证节、根因名与路径用英文、判定词译成英文",
      md_en.startswith("# Diagnosis Report") and "## Tree view" in md_en and "## Confirmed root causes" in md_en
      and "### 1. Branch A ✅" in md_en and "- Path: Root· → Branch A✓ (✓holds" in md_en
      and "- Verdict:holds" in md_en and "- Observed:A 命中 3 条" in md_en, md_en[:900])
check("报告:待确认节点名英文、原因原文;方案用 solutions 的 en", "- Branch C:缺 display c 采集" in md_en
      and "- Suggested solution: Replace the board" in md_en, md_en[-900:])
check("回复稿:英文头行、树视图、确证表(根因/路径英文、方案英文)、待确认、Notes",
      rp_en.startswith("**Diagnosis: confirmed root causes 1 · probable 0 · to be confirmed 1**")
      and "**Tree view** (only paths" in rp_en and "✅ Branch A  [Confirmed]" in rp_en
      and "| 1 | Branch A | Root → Branch A | A 命中 3 条:qos car inbound cir 500 \\| pass 0 discard 1200 \\| vlan 缺 / display a | Replace the board |" in rp_en
      and "- Branch C:缺 display c 采集" in rp_en and rp_en.rstrip().endswith("numbers as stated above)_"), rp_en[-800:])
check("rca_tree.txt 也是英文", "✅ Branch A  [Confirmed]" in (WORK / "en" / "rca_tree.txt").read_text(encoding="utf-8"))

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(1 if FAILED else 0)
