# quality_score.json 维度对照表(平台置信度配置用)

评估反思的平台评分六维度 = 错误分类六类(`SKILL.md`「评估怎么判」一节的表),顺序即评估顺序。
平台侧配置置信度(confidence)时照此表填;我方输出文件 `quality_score.json` 的
`dimensions` 键名 = 本表 `dimension_key`,格式见 `output_files.md` 第 3 节。

计分口径:六关按序、命中即停,不做程度打分——`value_type` 一律 **BOOLEAN**
(通过 `value:true`→`score:100`;命中 `value:false`→`score:0`;前序命中导致没评到
→`value:null`/`score:null`,展示渲染 N/A)。总分 = 通过维度数 ÷ 6 × 100(取整),
语义"六关过了几关";权重等权(1/6≈0.1667,平台按需归一),`weight` 仅供平台展示配置,
我方总分不按权重加权。

| sort_order | dimension_key | dimension_name (zh_CN / en_US) | dimension_desc | value_type |
|---|---|---|---|---|
| 1 | `env_failure` | 执行环境 / Execution Environment | 诊断执行是否正常完成:任一解析/RCA 步骤失败或 run.json 缺失即命中(ENV_FAILURE,人工修环境) / Whether the diagnosis run completed: any failed step or missing run.json fails this dimension. | BOOLEAN |
| 2 | `input_gap` | 数据完整性 / Input Completeness | 诊断所需数据源是否给齐:缺源且因此漏判期望根因即命中(INPUT_GAP,使用方补数据) / Whether all required data sources were provided: a missing source that caused an expected root cause to be missed fails this dimension. | BOOLEAN |
| 3 | `binding_gap` | 绑定正确性 / Binding Integrity | 绑定层是否有毛病:孤儿绑定(树上无此节点)或与漏判相关的"有判据无绑定"即命中(BINDING_GAP,清残留/补绑定) / Whether bindings are sound: orphan bindings or missing bindings related to a missed root cause fail this dimension. | BOOLEAN |
| 4 | `knowledge_gap` | 树节点认知 / Tree Knowledge | 期望根因节点是否在故障树上:tree_index 查无此节点即命中(KNOWLEDGE_GAP,tester 增节点) / Whether the expected root-cause node exists in the fault tree. | BOOLEAN |
| 5 | `criteria_gap` | 判据正确性 / Criteria Correctness | 判据执行出的事实与期望事实是否一致:predicate 与 expected_facts 不相等即命中(CRITERIA_GAP,人拍板进调优表) / Whether evaluated facts match expected facts for the node. | BOOLEAN |
| 6 | `verdict_gap` | 裁决正确性 / Verdict Correctness | 事实全对的前提下归档是否正确:裁决≠期望(含阴性用例假阳)即命中(VERDICT_GAP,人裁决调规则) / Whether the final verdict matches the expectation given correct facts (incl. false positives on negative cases). | BOOLEAN |

`dimension_name`/`dimension_desc` 的多语言对象里 `default` = `zh_CN` 文案。

与报告的对应关系:`quality_score.json` 由 `scripts/score_report.py` 从
`evaluation_report.json` 机械转换(判定来源 = `verdict_detail.error_class` 的
命中即停链条,不读 checks 的 passed);`checks` 六键三态(true/false/null)与本表
维度一一对应。EVAL_ERROR(评估自身失败)不产评分文件。
