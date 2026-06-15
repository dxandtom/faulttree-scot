---
name: cs-scot-eval
description: 用"合成故障注入"测诊断准确率——在脱敏后的真实大树(178 节点)上埋已知根因(ground truth)、撒诱饵+噪声,跑完和标准答案比,出命中率/档位准确/无假阳/诚实四项准确率表。补 cs-scot-benchmark(无 ground truth)不覆盖的准确率维度。当用户要评测 RCA 诊断准不准、做准确率实验数据。
---

# cs-scot-eval —— 诊断准确率评测(合成故障注入)

**本文件是操作手册,已加载,不要再次加载。** 任务:对一套**自带标准答案**的合成场景,跑出诊断准确率表。

## 为什么这么测
真实故障没人标过"正确根因",所以准确率没法直接测。办法是**自己造**:亲手埋一个根因、造让它成立的数据,正确答案我自己知道 → 跑完一比就是准确率。

两套场景,**以真实大树为主**:
- **`suite_real/`(主,跑这个)**:在**脱敏后的真实 178 节点大树**(`real_trees/`)上注入根因——埋在 L4~L6 深处,周围撒"事实成立但路径断了"的诱饵 + 一堆不成立/数据不足噪声。剪枝后局部子树 15~25 节点,模型必须**在大树里追出唯一闭合路径**。这才是"大树会不会乱"的真考题(toy 树人一眼看穿、证明不了任何事)。
- **`suite/`(辅,6 个 toy)**:确证/疑似/排除/诚实/抗假阳 五类的**最小可读样例**,只用来**自测打分逻辑**、给人对照语义,不当准确率结论。

**Mode A(默认):跳过模型绑定 → 隔离测纯 RCA 推理/导航**,数字最干净。条件已脱敏为 null,所以真实大树这套测的是 **RCA 在大树上的导航/结构推理**(闭合路径、AND/OR、档位、抗假阳、诚实),不是"条件判数据"。

## 三步跑法(主:真实大树 `suite_real`)

### 步骤 1:在真实大树上注入(确定性,一条命令造出全部场景的 `rca_input.json`)
```bash
python "<本目录>/scripts/inject_on_tree.py"      # 读 real_trees/ 真实树 → 写 suite_real/<场景>/result/rca_input.json + gold.json
```

### 步骤 2:逐场景做 RCA(**这一步是模型判断,正是被打分的对象**)
对 `suite_real/` 下**每个**场景,用 **fault-tree-rca** 技能读它的 `result/rca_input.json` 做根因判断,把结果按该技能的契约写到**同目录** `result/rca.json`。
> 别看 truth/reference 再判——那是作弊;就当普通一次 RCA,凭 `rca_input.json` 里的事实(局部子树)判。

### 步骤 3:打分出表(确定性)
```bash
python "<本目录>/scripts/score_accuracy.py" --suite "<本目录>/suite_real" -o "<本目录>/accuracy_report.txt"
```
对每个场景的 `rca.json` 比对标准答案,出四项:
- **命中**:埋的根因有没有落在【应在的档位】(确证→`rca_result`;疑似→`probable_causes`;无因类→不报确证);
- **档位准**:没把"该疑似的"报成确证、没漏档;
- **无假阳**:没把"路径断了的干扰叶子"等 `must_not_report` 节点报成根因;
- **诚实**:复用 `validate_rca`——确证路径无数据不足/不成立、无伪造成立。
综合 = 四项全过;聚合成准确率 %。

## 自测打分链路(可选,不测模型)
```bash
python "<本目录>/scripts/score_accuracy.py" --suite "<本目录>/suite_real" --use-reference
```
用每个场景的 `reference_rca.json`(满分参考)当输入,应出 **100%**——证明注入+剪枝+打分链路本身没毛病。

## 换树 / 扩充场景
- **换真实树**:把另一棵脱敏 `*.tree_index.json` 丢进 `real_trees/`,`inject_on_tree.py --tree <它>` 即可(只需拓扑,条件可为 null)。
- **加注入场景**:在 `inject_on_tree.py` 的 `SPECS` 加一条(`target` 埋因叶子 / `decoys` 诱饵 / 噪声),重跑;埋更深、加更多诱饵 = 加难度。
- **toy 自测套件**:`make_injection_suite.py` 重生成 `suite/`;它走金标准绑定,需先 `eval_prep.py --suite suite` 备 `rca_input` 再 RCA、再 `score_accuracy.py --suite suite`。

## 注意(写进汇报必须说清)
- 这是**【受控合成故障注入】下的准确率**:证明"给干净事实,推理链/导航对不对",是**必要不充分**——连埋的因都找不对则现网必不行,找对了≠现网准确率(真实故障更脏)。汇报口径写"受控合成下的诊断准确率",别写成"真实准确率"。
- 真实大树这套(条件已脱敏)测的是 **RCA 大树导航**(在 178 节点里追闭合路径、抗诱饵、守诚实);**不**测 data→事实的绑定准不准(那要金标准绑定,专家未定)。
- 与 `cs-scot-benchmark` 互补:benchmark 测 省/稳/可复现/诚实/转换/内核回归(**无** ground truth),本技能测**准确率**(**有** ground truth)。
- Python 3.11;路径有空格/中文务必加引号。
