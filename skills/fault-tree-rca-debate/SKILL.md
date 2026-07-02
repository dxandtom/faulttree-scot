---
name: fault-tree-rca-debate
description: 辩论式根因分析引擎(合作方算法)的集成壳:吃 cs-scot-execute 备好的 rca_input.json + tree_index.json,自己调 LLM 做多代理辩论评分,吐四档契约的 rca.json。作为 fault-tree-rca(模型手工判断)的可切换平替;当要用辩论引擎跑 RCA、或 A/B 对比三种得分机制时用。
---

# fault-tree-rca-debate —— 辩论式 RCA 引擎(集成壳)

**这是一个"脚本引擎",不是 prompt-only 技能**:它自己调 LLM API(见 vendor/config.yaml),
不需要 harness 里的模型来判断。核心算法来自合作方,原样放在 `vendor/`(**永不修改**,本仓不携带,
本地放入,见 `INTEGRATION.md`);本目录其余文件都是适配壳。

## 在流水线里的位置(与 fault-tree-rca 同一接缝、可互换)
```
runner 跑完 script 节点 → result/rca_input.json
  ├─ 老路径:用「fault-tree-rca」技能,模型手工判断 → rca.json(事实闭环,四档)
  └─ 本路径:跑下面这条命令,辩论引擎评分   → rca.json(评分排序,恒 probable 档)
```
```bash
python "<本目录>/scripts/run_debate_rca.py" \
  --rca-input "<OUT>/result/rca_input.json" \
  --tree-index "<OUT>/tree/<场景>.tree_index.json" \
  --out-dir "<OUT>/result" --mechanism all [--mock]
```
- 产物:`rca.json`(默认=original 机制)+ `rca.original/softmax/ratio.json` 三份 + `rca_debate_full.json`(引擎原始输出,含辩论记录)。
- `--mock`:用确定性假引擎跑通适配层(不调 LLM、不需要 vendor 文件)——自测/CI 用。
- 跑完照旧机械自检:`python skills/fault-tree-rca/scripts/validate_rca.py <rca.json> <rca_input.json>`。

## 适配层拆解(谁负责什么)
| 文件 | 职责 |
|---|---|
| `scripts/facts_to_question.py` | 证据→文本桥:把 rca_input 的确定性事实渲染成引擎的 question。**回声策略**:只有"成立"的判据原文复述(让文本匹配命中),"不成立/数据不足"只报节点名(防误匹配假阳)。 |
| `scripts/tree_to_vendor_xlsx.py` | tree_index → 引擎格式 xlsx;`Ln Code` 列=我们的 node_id(nN),让输出天然带主键。 |
| `scripts/result_adapter.py` | 引擎 top_k×3 → 四档 rca.json。v1 全进 probable_causes(评分≠事实闭环,rca_result 诚实置空);judgment 用中性词,不冒用「成立/数据不足」保留字。 |
| `scripts/run_debate_rca.py` | 编排壳 + 真/假引擎装载(`_load_processor` 是唯一接口耦合点)。 |
| `scripts/mock_vendor.py` | 按 vendor 文档接口形状做的确定性假引擎(形状对、算法不复刻)。 |

## 三种得分机制(original / softmax / ratio)怎么选
合作方要求实测。用本仓评测当裁判:对 `cs-scot-eval` 的带答案场景逐个跑本引擎
(`--mechanism all`),三份结果分别按四维打分(命中/档位/无假阳/诚实),赢者当默认。
注意:引擎属程序化被测对象,成绩标注 `engine=debate`,别与"模型手工 RCA"的成绩混表;
且它每个无匹配兄弟组都要调 LLM 辩论,先在 suite_v2(18 场景、17 节点小树)上估好耗时/成本再上大树。
