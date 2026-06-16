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
**键名必须照抄上面**(Root_cause / Fault_tree_path / evidence_path / node_id / node / condition / actual / judgment),
evidence_path 必须是逐节点的**列表**;**别自创** node_name/path/evidence 这类别的键(否则打分器对不上)。
"""

BINDING_RULES = """你是"绑定器"(generate 第 2.5 步)。给你一棵故障子树的若干【判据(大白话)】+ 各数据源的【列概览】
(系统列名 + 样例值)。请为**每条判据**产出一条绑定 CONDITIONS:把大白话判据**映射到系统列**、选对内核类别、给参数。
**严禁写脚本**;靠你自己读判据语义 + 看列名/样例值对上。

内核类别(选一个):A 阈值比较 / B 差值(尝试-成功>0 这类) / C 字段·错误码匹配 / E 占比TopN / G 告警·日志匹配 / H 信令会话缺失。
每条绑定写成一个对象(node_id 照抄判据的):
  A:   {"node_id":"…","class":"A","source":"<源>","column":"<系统列裸名>","op":"<或>等","threshold":数,"scope":"aggregate","agg":"mean"}
  B:   {"node_id":"…","class":"B","source":"<源>","minuend":["列"],"subtrahend":["列"],"op":">","threshold":0}
  C/G: {"node_id":"…","class":"C或G","source":"<源>","column":"<列>","contains":["要匹配的取值"]}
  E:   {"node_id":"…","class":"E","source":"<源>","group_by":"<列>","op":">","threshold":0.5}
  H:   {"node_id":"…","class":"H","source":"<源>","key":"<会话键列>","msg_column":"<消息列>","lacks":["缺的消息名"]}
**数据里没有该判据要的列** → 标 {"node_id":"…","decision":"insufficient_data"},别硬绑别的列。

只输出 CONDITIONS 的 **JSON 列表**(每条判据一个对象),不要任何额外文字。
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

## 另一套:绑定阶段(测 generate 第 2.5 步)
每个 `<场景>/binding_prompt.txt` 是一道绑定题(大白话判据 + 各源列概览 + `data/*.csv`)。
同样**发给普通模型**,让它输出 CONDITIONS 的 JSON 列表,**原样存成** `<场景>/binding.json`;全部收齐后:
   python ../scripts/score_binding.py --suite . -o <输出路径>/binding_report.txt
报告里的 **选列命中率** 最关键——反映模型把"大白话判据"对到"系统列"的能力(generate 最易错的活)。

注意:`_answers/` 是标准答案,**答题时绝不能给模型看**;它只给打分器用。
"""

HOWTO_V2 = """# v2 题面:怎么用(端到端 —— 评的是真 generate/execute/rca 的输出)

v2 与 v1 的区别:v1 复用真实内核但自算 rca_input(绕过技能壳);**v2 真的跑 excel_to_tree+assemble+runner**
(见 `v2_run.py`),再评它们的产物 → 零重写 gap。题库是**手工精心编排**(非脚本随机生成)的一棵连贯树 + 9 个场景。

前置(本地先跑一次,产出题面 —— 确定性):
```bash
python ../scripts/v2_run.py        # 真 excel_to_tree+assemble+runner → 各场景 rca_input.json + binding_question.json
python ../scripts/make_prompts.py --v2
```

## 一、RCA(9 道,逐场景)
每个 `scenarios/<场景>/prompt.txt` = 判据法则 + 该场景 `rca_input.json`(真 runner 用**金标准绑定**算出的事实+剪枝树)。
**发给普通模型/聊天(别用能写代码的 agent)**,只输出 rca.json 那段 JSON,**原样存成** `scenarios/<场景>/rca.json`。

## 二、绑定(1 道,suite 级)
`binding_prompt.txt`(由 suite 级 `binding_question.json` 生成:15 条大白话判据 + 各源列概览)。
同样发给普通模型,输出 CONDITIONS 的 JSON 列表,**原样存成** `binding.json`(放 suite_v2/ 根)。

## 三、打分
```bash
python ../scripts/score_v2.py            # 同时出 绑定 + RCA 两段
python ../scripts/score_v2.py --use-reference   # 自测打分链路(金标准/参考),应 100%
```
RCA 用**金标准绑定**隔离绑定误差(单测大树推理);绑定阶段单独评(大白话→系统列选列命中率最关键)。
注意:`scenarios/*/truth.json` 是答案,**答题时别给模型看**,只给打分器。
"""


def make_v2(suite: Path) -> None:
    """v2 题面:RCA 逐场景(scenarios/<场景>/prompt.txt)+ 绑定 suite 级一道(binding_prompt.txt)。"""
    scns = sorted(d for d in (suite / "scenarios").iterdir() if d.is_dir()) if (suite / "scenarios").exists() else []
    n = 0
    for d in scns:
        ri = d / "rca_input.json"
        if not ri.exists():
            continue
        (d / "prompt.txt").write_text(
            RULES + "\n【本场景输入(rca_input.json)】:\n" + ri.read_text(encoding="utf-8")
            + "\n\n【现在请只输出该场景的 rca.json(纯 JSON)】", encoding="utf-8")
        n += 1
    bq = suite / "binding_question.json"
    if bq.exists():
        (suite / "binding_prompt.txt").write_text(
            BINDING_RULES + "\n【要绑的判据 + 各源列概览(binding_question.json)】:\n"
            + bq.read_text(encoding="utf-8")
            + "\n\n【现在请只输出 CONDITIONS 的 JSON 列表(纯 JSON)】", encoding="utf-8")
    (suite / "_HOWTO_prompts.md").write_text(HOWTO_V2, encoding="utf-8")
    if not n:
        print(f"未找到 RCA 题面(先跑 v2_run.py 产 rca_input.json):{suite}", file=sys.stderr)
        sys.exit(2)
    print(f"v2 已生成 {n} 道 RCA 题面(scenarios/<场景>/prompt.txt)+ 1 道绑定题面(binding_prompt.txt)+ _HOWTO_prompts.md。")
    print("RCA:发各 prompt.txt → 存同目录 rca.json;绑定:发 binding_prompt.txt → 存 suite_v2/binding.json;再 score_v2.py。")
    print("一律发给【普通模型/聊天,别用能写代码的 agent】。")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="为每个场景生成自包含题面 prompt")
    ap.add_argument("--suite", help="题库目录(默认:v1=suite_real;--v2 时=suite_v2)")
    ap.add_argument("--v2", action="store_true", help="v2 题库布局(scenarios/ + suite 级绑定题)")
    args = ap.parse_args()
    if args.v2:
        make_v2(Path(args.suite) if args.suite else DEFAULT_SUITE.parent / "suite_v2")
        return
    suite = Path(args.suite) if args.suite else DEFAULT_SUITE
    scns = sorted(d for d in suite.iterdir() if d.is_dir() and not d.name.startswith("_")
                  and (d / "rca_input.json").exists())
    if not scns:
        print(f"未找到场景(先跑 inject_on_tree.py):{suite}", file=sys.stderr)
        sys.exit(2)
    for d in scns:
        rca_input = (d / "rca_input.json").read_text(encoding="utf-8")
        (d / "prompt.txt").write_text(
            RULES + "\n【本场景输入(rca_input.json)】:\n" + rca_input
            + "\n\n【现在请只输出该场景的 rca.json(纯 JSON)】", encoding="utf-8")
        bq = d / "binding_question.json"
        if bq.exists():                                # 绑定题面(测 generate 绑定阶段)
            (d / "binding_prompt.txt").write_text(
                BINDING_RULES + "\n【要绑的判据 + 各源列概览(binding_question.json)】:\n"
                + bq.read_text(encoding="utf-8")
                + "\n\n【现在请只输出 CONDITIONS 的 JSON 列表(纯 JSON)】", encoding="utf-8")
    (suite / "_HOWTO_prompts.md").write_text(HOWTO, encoding="utf-8")
    print(f"已为 {len(scns)} 个场景生成 prompt.txt(RCA)+ binding_prompt.txt(绑定)+ _HOWTO_prompts.md。")
    print("RCA:发 prompt.txt → 存 rca.json → score_accuracy.py;绑定:发 binding_prompt.txt → 存 binding.json → score_binding.py。")
    print("一律发给【普通模型/聊天,别用能写代码的 agent】。")


if __name__ == "__main__":
    main()
