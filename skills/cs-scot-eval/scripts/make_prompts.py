#!/usr/bin/env python3
"""为每个评测场景生成一份【自包含题面 prompt】——把"模型只能答题、没法写脚本绕过"落到实处。

为什么要它:用"带工具的 agent"跑评测,模型会图省事写个确定性脚本(读 predicate 直接遍历)批量产
rca.json,那测的是脚本不是模型。把每个场景做成一道**纯问答题**(判据法则 + 该场景 rca_input + 输出契约),
喂给【普通模型/聊天,不给写代码的工具】,它就只能靠推理答 → 才是真的在测模型。

产出:suite_real/<场景>/prompt.txt(逐场景题面)+ suite_real/_HOWTO_prompts.md(用法)。
用法: python make_prompts.py   (先 inject_on_tree.py 备好 suite_real/)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SUITE = HERE.parent / "suite_real"

RULES = """你是根因诊断器(fault-tree-rca)。下面给你一棵【已剪枝的故障子树】:每个节点有 condition(判据)和
facts(已解析事实,含实际值;ok 事实带 predicate_satisfied=内核字面判定,数据不足则 status=insufficient_data)。
请**只凭这份输入、靠你自己的推理**定位根因,**严禁写任何脚本/程序来求解**。

判定法则:
1. 逐个带 condition 的节点判:facts 为空或 status=insufficient_data → 该节点【数据不足】;否则据实际值与判据
   (predicate_satisfied=true)判【成立】、false 判【不成立】。pass_through 节点无条件、不阻断路径、不计入证据。
2. 四档分流(沿根→叶整条路径看):
   • 路径上每个带条件节点都【成立】且终点是叶子 → 【确证根因】放 rca_result;
   • 路径上有节点【不成立】→ 该支【排除】,不报为根因;
   • 无不成立、但有【数据不足】、且已成立到某中间层(叶子待定)→ 【疑似】放 probable_causes
     (给 confidence/blocked_at/caveats/support);
   • 其余 → undetermined。
3. 诚实铁律:判【成立】的节点必须真有成立事实;**绝不给"数据不足/不成立/无事实"的节点判成立**;
   rca_result 为 [] 完全正常(没有任何闭合成立路径时就该是空)。evidence_path 的 actual 要逐字引用事实里的真实值。

**只输出下面这个 JSON,不要任何额外文字/解释/代码块标记:**
{
  "rca_result": [ {"Root_cause":"…","Fault_tree_path":["根…叶的节点名"],"support":"…",
    "evidence_path":[{"node":"…","node_id":"…","condition":"<逐字>","actual":"<逐字引用事实值>","judgment":"成立/不成立/数据不足"}]} ],
  "probable_causes": [ {"Root_cause":"…","Fault_tree_path":["…"],"confidence":"high/medium/low",
    "blocked_at":"<数据不足的节点>","caveats":"…","support":"…","evidence_path":[…]} ],
  "undetermined": [ {"Fault_tree_path":["…"],"blocked_at":"…","reason":"缺哪个量","observed":["…"]} ],
  "tree_issues": { "uncovered_condition_nodes":[…], "orphan_node_ids":[…] }
}
"""

HOWTO = """# 怎么用这些 prompt 跑出【真·模型】准确率

每个 `<场景>/prompt.txt` 是一道自包含题面(判据法则 + 该场景的故障子树+事实 + 输出契约)。

**关键:喂给「普通模型/聊天窗口」答,别走「能写代码的 agent」**——否则它又会写脚本绕过推理。

步骤:
1. 逐个场景:把 `<场景>/prompt.txt` 的全文发给 MiniMax(纯聊天 / 或 API 单轮,不带工具),
   让它**只输出 rca.json 那段 JSON**;把它的输出**原样存成** `<场景>/rca.json`。
   (30 个场景 = 30 次问答;也可用 API 写个循环,但循环里也只是"发题面、收 JSON",不让模型写代码。)
2. 全部收齐后打分:
   python ../scripts/score_accuracy.py --suite . -o <你要的输出路径>/accuracy_report.txt
3. 报告里的命中/档位/无假阳/诚实 + 按规模分档,才是 MiniMax 真实的 RCA 推理表现。

注意:`_answers/` 是标准答案,**答题时绝不能给模型看**;它只给打分器用。
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="为每个场景生成自包含题面 prompt")
    ap.add_argument("--suite", default=str(DEFAULT_SUITE))
    args = ap.parse_args()
    suite = Path(args.suite)
    scns = sorted(d for d in suite.iterdir() if d.is_dir() and not d.name.startswith("_")
                  and (d / "rca_input.json").exists())
    if not scns:
        print(f"未找到场景(先跑 inject_on_tree.py):{suite}", file=sys.stderr)
        sys.exit(2)
    for d in scns:
        rca_input = (d / "rca_input.json").read_text(encoding="utf-8")
        prompt = (RULES + "\n【本场景输入(rca_input.json)】:\n" + rca_input
                  + "\n\n【现在请只输出该场景的 rca.json(纯 JSON)】")
        (d / "prompt.txt").write_text(prompt, encoding="utf-8")
    (suite / "_HOWTO_prompts.md").write_text(HOWTO, encoding="utf-8")
    print(f"已为 {len(scns)} 个场景生成 prompt.txt(+ _HOWTO_prompts.md)。")
    print("把每个 <场景>/prompt.txt 发给【普通模型/聊天,别用能写代码的 agent】,输出存成同目录 rca.json,再打分。")


if __name__ == "__main__":
    main()
