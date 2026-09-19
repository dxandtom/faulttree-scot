#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rca.json 机械说明的中英并列(draft_rca → finalize_rca → render --lang en)。标准库,直接 python 跑。
① 局部子树带 name_en:候选清单与 rca.json 里的机械说明多出 _en 字段(祖先注记、卡点、待确认原因、排除留痕),
   中文字段与不带英文时逐字一致;
② 不带 name_en 的输入:中文输出不变,机械说明的英文版仍在(节点名回退中文);
③ render_report --lang en:注意/缺/卡点/排除留痕用英文版,证据原文不译;zh 渲染不受 _en 字段影响;
④ 层级矛盾降疑似:blocked_at_en / caveats_en 由候选清单的英文预填而来。"""
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RCA = REPO / "skills" / "fault-tree-rca" / "scripts"
W = Path(tempfile.mkdtemp(prefix="rcabi-"))
PASSED = FAILED = 0
EN = {"接入失败示例": "Access Failure Demo", "QoS建立失败": "QoS Setup Failure", "干扰导致": "Interference", "弱覆盖": "Weak Coverage"}


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


def strip_en(x):
    if isinstance(x, dict):
        return {k: strip_en(v) for k, v in x.items() if not (k.endswith("_en") or k in ("node_en", "status_en"))}
    if isinstance(x, list):
        return [strip_en(v) for v in x]
    return x


def pipeline(tag, inp):
    d = W / tag
    d.mkdir()
    (d / "rca_input.json").write_text(json.dumps(inp, ensure_ascii=False), encoding="utf-8")
    rc1, o1 = run(RCA / "draft_rca.py", d / "rca_input.json", "-o", d / "draft.json")
    rc2, o2 = run(RCA / "finalize_rca.py", d / "draft.json", "-o", d / "rca.json", "--rca-input", d / "rca_input.json")
    assert rc1 == 0 and rc2 == 0, o1 + o2
    return json.load(open(d / "draft.json", encoding="utf-8")), json.load(open(d / "rca.json", encoding="utf-8")), d


base = json.load(open(REPO / "examples/compression-demo/sample_output/rca_input.json", encoding="utf-8"))
with_en = copy.deepcopy(base)
for n in with_en["fault_tree"]["pruned_nodes"].values():
    if n["name"] in EN:
        n["name_en"] = EN[n["name"]]

print("\n== ① 局部子树带 name_en:机械说明多出 _en 字段,中文逐字不变 ==")
draft_a, rca_a, dir_a = pipeline("a", with_en)
draft_b, rca_b, dir_b = pipeline("b", base)
check("中文字段:带/不带英文名的两次输出逐字一致(候选清单与 rca.json)",
      strip_en(draft_a) == strip_en(draft_b) and strip_en(rca_a) == strip_en(rca_b))
conf = rca_a["rca_result"][0]
check("确证条目的 caveats_en:祖先未验证注记英文版,节点名用 name_en",
      conf.get("caveats_en", "").startswith("Ancestor nodes not verified (no binding / insufficient data")
      and "QoS Setup Failure(n2)" in conf["caveats_en"] and "祖先节点未验证" in conf["caveats"], repr(conf.get("caveats_en")))
check("evidence_path 条目带 node_en", all(e.get("node_en") == EN[e["node"]] for e in conf["evidence_path"] if e["node"] in EN))
und = rca_a["undetermined"][0]
check("待确认条目的 blocked_at_en 用英文名;reason 是事实原文时不重复存英文",
      und.get("blocked_at_en") == "QoS Setup Failure(n2)" and und["blocked_at"] == "QoS建立失败(n2)"
      and ("reason_en" not in und or und["reason_en"] != und["reason"]), repr(und))

print("\n== ② 不带 name_en:中文不变,机械说明英文版仍在(节点名回退中文)==")
conf_b = rca_b["rca_result"][0]
check("caveats_en 仍生成,节点名回退中文", "QoS建立失败(n2)" in conf_b.get("caveats_en", "") and conf_b["caveats_en"].startswith("Ancestor nodes"))
check("不带英文名时 evidence_path 没有 node_en", not any("node_en" in e for e in conf_b["evidence_path"]))

print("\n== ③ render_report --lang en 用 _en 字段;zh 渲染不受影响 ==")
tree_src = json.load(open(REPO / "examples/compression-demo/tree/接入失败示例.tree_index.json", encoding="utf-8"))
for n in tree_src["nodes"].values():
    if n["name"] in EN:
        n["name_en"] = EN[n["name"]]
(dir_a / "t.tree_index.json").write_text(json.dumps(tree_src, ensure_ascii=False), encoding="utf-8")
rc, o = run(RCA / "render_report.py", dir_a / "rca.json", "--tree", dir_a / "t.tree_index.json", "--lang", "en", "-o", dir_a / "en.md")
md_en = (dir_a / "en.md").read_text(encoding="utf-8")
reply_en = (dir_a / "rca_reply.md").read_text(encoding="utf-8")
tree_en = (dir_a / "rca_tree.txt").read_text(encoding="utf-8")
check("退出码 0", rc == 0, o[-300:])
check("报告 en:注意用 caveats_en、待确认节点名英文、证据原文保留",
      "- Caveat: Ancestor nodes not verified" in md_en and "### 1. Interference ✅" in md_en
      and "- QoS Setup Failure:" in md_en and "RNL_Unspecified" in md_en, md_en[-800:])
check("树视图 en:Caveat 用英文注记、Missing 行在", "Caveat:Ancestor nodes not verified" in tree_en and "Missing:" in tree_en, tree_en)
check("回复稿 en:确证表与待确认用英文名", "| 1 | Interference |" in reply_en and "- QoS Setup Failure:" in reply_en, reply_en[-600:])
rc, o = run(RCA / "render_report.py", dir_a / "rca.json", "--tree", dir_a / "t.tree_index.json", "-o", dir_a / "zh.md")
rc2, o2 = run(RCA / "render_report.py", dir_b / "rca.json", "--tree", REPO / "examples/compression-demo/tree/接入失败示例.tree_index.json", "-o", dir_b / "zh.md")
check("zh 渲染:带 _en 字段的 rca.json 与不带的渲染结果逐字一致", rc == 0 and rc2 == 0
      and (dir_a / "zh.md").read_text(encoding="utf-8") == (dir_b / "zh.md").read_text(encoding="utf-8"))

print("\n== ④ 层级矛盾降疑似:blocked_at_en / caveats_en 来自候选清单的英文预填 ==")
contra = copy.deepcopy(with_en)
pn = contra["fault_tree"]["pruned_nodes"]
n2 = pn["n2"]                                                    # QoS建立失败:改成实测不成立(强反证 A 类)
n2["facts"] = [{"class": "A", "status": "ok", "predicate_satisfied": False, "value": 99, "op": ">", "threshold": 100,
                "explain": "kpi succ rate 99 > 100 不成立"}]
draft_c, rca_c, _ = pipeline("c", contra)
prob = rca_c["probable_causes"]
check("候选机械降疑似", len(prob) == 1 and rca_c["rca_result"] == [], repr(rca_c)[:300])
p = prob[0] if prob else {}
check("blocked_at / blocked_at_en 成对,英文用 name_en 与英文说明",
      p.get("blocked_at", "").startswith("上级「QoS建立失败」(n2)实测不成立")
      and p.get("blocked_at_en") == "ancestor 「QoS Setup Failure」(n2) observed not holding", repr(p.get("blocked_at_en")))
check("caveats_en 是英文的层级矛盾说明", p.get("caveats_en", "").startswith("Hierarchy contradiction: candidate 「Interference」")
      and p.get("caveats", "").startswith("层级矛盾:候选「干扰导致」"), repr(p.get("caveats_en"))[:200])

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
shutil.rmtree(W, ignore_errors=True)
sys.exit(1 if FAILED else 0)
