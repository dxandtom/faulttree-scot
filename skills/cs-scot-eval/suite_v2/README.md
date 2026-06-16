# suite_v2 —— v2 端到端评测题库(手工编排)

v2 与 v1(`suite_real`)的根本区别:
- **v1**:复用真实内核,但**自己算** `rca_input`(`inject_on_tree.py`),绕过了 generate/execute 的技能壳;
- **v2**:把题库喂给**真** `excel_to_tree` + `assemble_scenario` + `runner`(见 `scripts/v2_run.py`)端到端跑,
  再评它们的产物 —— **零重写 gap**,测的就是这三个 skill 串起来到底准不准。

题库**手工精心编排**(`scripts/v2_handcrafted.py`,非脚本随机生成):一棵【因果连贯】的 SA 接入失败树
(17 节点 / 15 个带条件节点),每个节点固定一条**大白话判据**(不含系统列名,制造"语义对列"难点)+ 真实 hold/fail 取值。
**18 个**有意义的场景,每个都用真实内核校验过能算出预期 成立/不成立/数据不足:

| 目录 | 含义(在 truth.json 的 desc) | 类别 | 考点 |
|---|---|---|---|
| S01 / S10 | 弱覆盖 / 上行干扰 | confirmed | A 阈值,随机接入下两兄弟叶子 |
| S02 / S03 / S04 / S11 / S12 | AMF过载 / 无线链路 / 鉴权 / RRC拒绝拥塞 / 传输资源 | confirmed | C 占比·错误码(深浅不一) |
| S13 / S14 | 射频驻波比告警 / 小区不可用告警 | confirmed | **G 告警 exist**;祖先全 pass_through |
| S15 | 安全模式失败 | confirmed | **H 信令缺失** |
| S05 / S17 | 上下文层疑似 / NGAP层疑似 | probable | 中间层成立、叶子数据不足 → 不能确证 |
| S06 | 网络正常排除 | excluded | 全不成立 → 不报根因 |
| S07 | 数据不足 | honest_insufficient | 全无数据 → 诚实判数据不足 |
| S08 / S18 | 抗诱饵-弱覆盖断路 / 上行干扰断路 | false_positive_resist | 诱饵叶子成立但路径被父级断开 → **不许报** |
| S09 / S16 | 多根因(弱覆盖+无线链路 / AMF过载+驻波比告警) | confirmed | 同源两条 / 跨源 chr+alarm |

> **目录名【不透明】**(`S01`…`S18`,无描述后缀):场景含义放 `truth.json` 的 `desc`(做题时模型读不到)——
> 防止模型从文件名直接抄答案(如"抗诱饵-弱覆盖断路"会剧透哪个是诱饵);也顺带避开 `()` `+` 等路径敏感字符。
> 打分报告会从 `truth.json` 取 `desc` 显示成 `S08 抗诱饵-弱覆盖断路` 方便人看。

## 提交什么 / 重生什么
- **提交(手工源题库)**:`fault_tree.csv`(真 Excel 格式)、`gold_tree.json`(code→金标准绑定)、
  `scenarios/S*/data/*.csv`(数据)、`scenarios/S*/truth.json`(答案 + desc,**答题时别看**)。
- **不提交(一条命令确定性重生)**:`scot产物/<场景>/`(真 excel_to_tree+assemble 的编译产物,生产同款布局:
  `plan.json`/`scripts/*_analyzer.py`/`tree/*.md`/`manifest.json`)、`scenarios/*/rca_input.json`(真 runner 产出)、
  `binding_question.json`、题面、模型答案、报告。

join key 是 **code**(`FT-0XX`):`gold_tree`/`truth` 都按 code 索引;真 `excel_to_tree` 会重新分配 id(n1,n2…),
打分时用 `scot产物/<场景>/tree/` 里重解析出的 `tree_index` 的 code→id 映射对回来。

## 跑法
```bash
# 1) 真 excel_to_tree + assemble + runner 端到端(用金标准绑定算事实,隔离绑定误差),产出题面
python ../scripts/v2_run.py     # 编译产物落 scot产物/SA接入失败/(就是生产里那个 scot产物)
# 2) 出题面(发给普通模型/聊天,别用能写代码的 agent)
python ../scripts/make_prompts.py --v2          # 见生成的 _HOWTO_prompts.md
#    RCA:18 道 scenarios/<场景>/prompt.txt → 模型答 → 存同目录 rca.json
#    绑定:1 道 binding_prompt.txt → 模型答 → 存 binding.json
# 3) 打分(绑定 + RCA 两段)
python ../scripts/score_v2.py
python ../scripts/score_v2.py --use-reference   # 自测打分链路(金标准/参考),应 100%
```

> RCA 路用**金标准绑定**算事实,单测大树推理(判条件/导航/抗诱饵/诚实);绑定阶段(大白话→系统列)单独评。
> 绑定打分对明显同义 key(`category`=`class`、`value`=`contains`)放宽,只认真分类错(如 C 误标 E);
> 受控合成,**必要不充分**:口径写"受控合成下的诊断推理准确率",别写成"真实准确率"。
