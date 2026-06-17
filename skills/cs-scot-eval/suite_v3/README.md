# suite_v3 —— 跨规模综合评测(v2 ⊕ v1 融合)

把两套评测融合成「一次跑完」的跨规模综合测评:

| 来源 | 树规模 | 机制 | 内容 |
|---|---|---|---|
| 精编树(= suite_v2) | 17 节点 | **真 excel_to_tree+assemble+runner 全技能壳** | realistic 大白话绑定题(15)+ 18 个因果连贯场景 |
| 多规模树(= suite_real) | 27 / 44 / 95 / 178 / 320 节点 | **真 tree_prune 剪枝**(大树真丢分支,如 320→约 19) | 6 类情形 ×5 = 30 个场景 |

合计 **1 道绑定 + 48 道 RCA**,跨 **6 个规模**。两套底座题库分别冻结在 `suite_v2/`(手工源)与 `suite_real/`(注入考题)。

## 跑法
```bash
python ../scripts/v3_run.py            # 建两套底座 + manifest.json(48 场景,按规模小→大)
# 绑定:读 suite_v2/binding_question.json → 写 suite_v2/binding.json
# RCA :照 manifest.json 的 rca_scenarios 逐条(⛔一次一个,别串场景)读 <rel>/rca_input.json → 写 <rel>/rca.json
python ../scripts/score_v3.py -o v3_report.txt
python ../scripts/score_v3.py --use-reference   # 自测打分链路,应 100%
```

## 报告维度
- **绑定**:类别 / 选列 / 参数 / 完全正确(精编树 15 条 realistic 判据)。
- **RCA**:命中 / 档位 / 无假阳 / 诚实,四项全过 = 综合通过;按**规模**(17/27/44/95/178/320)与**情形**(确证/疑似/抗诱饵/排除/数据不足)两维聚合。

## 提交什么
本目录只提交本 README;`manifest.json` / `v3_report.txt` 由 `v3_run.py` / `score_v3.py` 确定性重生(已 gitignore)。

## 口径
受控合成下的【跨规模诊断推理稳健性 + 端到端绑定】:证明流程与模型能在 27–320 节点不同规模、真剪枝下可靠执行 RCA(判条件 + 大树导航 + 抗诱饵 + 不伪造)且随规模无退化,并能把大白话判据对到系统列。**必要不充分**,非现网脏数据准确率。
