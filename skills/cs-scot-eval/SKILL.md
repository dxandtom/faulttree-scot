---
name: cs-scot-eval
description: 用"合成故障注入"测诊断准确率——故障树+数据是亲手造的、根因已知(ground truth),跑完和标准答案比,出命中率/档位准确/无假阳/诚实四项准确率表。补 cs-scot-benchmark(无 ground truth)不覆盖的准确率维度。当用户要评测 RCA 诊断准不准、做准确率实验数据。
---

# cs-scot-eval —— 诊断准确率评测(合成故障注入)

**本文件是操作手册,已加载,不要再次加载。** 任务:对一套**自带标准答案**的合成场景,跑出诊断准确率表。

## 为什么这么测
真实故障没人标过"正确根因",所以准确率没法直接测。办法是**自己造**:亲手埋一个根因、造让它成立的数据,正确答案我自己知道 → 跑完一比就是准确率。`suite/` 里已备好 6 个场景(确证/疑似/排除/诚实数据不足/抗假阳 × A/C/D/E/H 内核),每个带 `gold.json`(金标准绑定+truth)、`reference_rca.json`(满分参考)。

**Mode A(本技能默认):用金标准绑定、跳过模型绑定 → 隔离测纯 RCA 推理**,数字最干净。

## 三步跑法

### 步骤 1:准备 RCA 输入(确定性,一条命令跑完全部场景)
```bash
python "<本目录>/scripts/eval_prep.py" --suite "<本目录>/suite"
```
它对每个场景:跑金标准绑定 → 各源逐条件事实 → 剪成局部子树 → 写 `suite/<场景>/result/rca_input.json`。

### 步骤 2:逐场景做 RCA(**这一步是模型判断,正是被打分的对象**)
对 `suite/` 下**每个**场景,用 **fault-tree-rca** 技能读它的 `result/rca_input.json` 做根因判断,把结果按该技能的契约写到**同目录** `result/rca.json`。
> 别看 truth/reference 再判——那是作弊;就当普通一次 RCA,凭 `rca_input.json` 里的事实判。

### 步骤 3:打分出表(确定性)
```bash
python "<本目录>/scripts/score_accuracy.py" --suite "<本目录>/suite" -o "<本目录>/accuracy_report.txt"
```
对每个场景的 `rca.json` 比对标准答案,出四项:
- **命中**:埋的根因有没有落在【应在的档位】(确证→`rca_result`;疑似→`probable_causes`;无因类→不报确证);
- **档位准**:没把"该疑似的"报成确证、没漏档;
- **无假阳**:没把"路径断了的干扰叶子"等 `must_not_report` 节点报成根因;
- **诚实**:复用 `validate_rca`——确证路径无数据不足/不成立、无伪造成立。
综合 = 四项全过;聚合成准确率 %。

## 自测打分链路(可选)
```bash
python "<本目录>/scripts/score_accuracy.py" --suite "<本目录>/suite" --use-reference
```
用每个场景的 `reference_rca.json`(满分参考答案)当输入,应出 **100%**——证明打分逻辑+数据链路本身没毛病(不测模型)。

## 重生成 / 扩充场景
```bash
python "<本目录>/scripts/make_injection_suite.py"      # 重新生成 suite(确定性,可反复)
```
要加场景:在 `make_injection_suite.py` 的 `SCENARIOS` 里按现有格式加一条(树+绑定+数据+truth),重跑即可。

## 注意(写进汇报必须说清)
- 这是**【受控合成故障注入】下的准确率**:证明"给干净事实,推理链对不对",是**必要不充分**——连合成的埋因都找不对则现网必不行,找对了≠现网准确率(真实故障更脏)。汇报口径写"受控合成下的诊断准确率",别写成"真实准确率"。
- 与 `cs-scot-benchmark` 互补:benchmark 测 省/稳/可复现/诚实/转换/内核回归(**无** ground truth),本技能测**准确率**(**有** ground truth)。
- Mode B(端到端:让模型自己绑定再判)留待后续:在 `suite` 基础上发分析脚本骨架让模型填 `CONDITIONS`,再跑 execute+RCA+打分,可额外测"绑定准确率"。
- Python 3.11;数据 csv;路径有空格/中文务必加引号。
