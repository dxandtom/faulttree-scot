# 本地组装与核对清单(把合作方引擎接进来)

> 背景:合作方交付 = `fault_tree_evaluator_bfs_advanced.py` + `config.yaml` 两个文件,
> **不入本仓**(含密钥/内部代码)。本仓携带的是全部适配壳 + 假引擎自测。
> 壳已在 mock 模式下端到端验证(xlsx 生成 → question 渲染 → 引擎 → rca.json → validate_rca 通过);
> 真引擎侧的接口假设**全部来自其文档**,以下清单帮你在本地半小时内核完。

## 一、组装步骤(5 分钟)
1. 把两个文件原样放进 `skills/fault-tree-rca-debate/vendor/`(目录已备好,gitignore 会挡住它们)。
2. `pip install pyyaml openpyxl numpy`(引擎用 numpy;壳用前两个)。
3. 冒烟(不调 LLM,先验壳):
   ```bash
   python skills/fault-tree-rca-debate/scripts/run_debate_rca.py \
     --rca-input <某场景>/result/rca_input.json \
     --tree-index <某场景>/tree/<场景>.tree_index.json \
     --out-dir /tmp/debate_test --mechanism all --mock
   python skills/fault-tree-rca/scripts/validate_rca.py /tmp/debate_test/rca.json <...>/rca_input.json
   ```
4. 去掉 `--mock` 换真引擎,重复第 3 步。

## 二、真引擎核对清单(接口假设逐条验证;不符只改 `run_debate_rca._load_processor` 一处)
| # | 假设(来自其文档) | 验证方法 | 不符时 |
|---|---|---|---|
| ① | `FaultTreeProcessor(EvaluationConfig)` / `load_fault_tree(path)→df` / `build_tree_structure(df)→tree` / `find_top_k_paths(tree, question, k)→(orig, softmax, ratio, debate_records)` | 直接跑一步冒烟 | 改 `_load_processor` 与调用三行 |
| ② | 其 loader 接受我们生成的 xlsx(列:`Ln Code / Ln / Ln(Chinese)` + `Judgment Condition(Chinese)`;**无 Module Id/Product 列**);**带条件的中间节点单独成行**且按 Code 合并、不会被当成叶子 | 用 `tree_to_vendor_xlsx.py` 生成后让其 load,打印树规模对比 tree_index 节点数 | 缺列→在 `write_xlsx` 补常量列;中间行异常→加 `--leaf-only` 并把中间条件并到子行文本 |
| ③ | `find_top_k_paths` 输出路径里节点带 `code`(=我们的 node_id)、`node_score`、`match_type`,路径尾巴带三个 `*_path_score` | 看 `rca_debate_full.json` | 改 `result_adapter._split_path/_judgment` 的取键 |
| ④ | 空 `judgment_conditions` 的节点(纯结构节点/根)不把路径得分打零 | 冒烟看 top 路径得分是否全为 0 | 与合作方确认其空条件语义;必要时 xlsx 里给结构节点填"无条件进入" |
| ⑤ | `EvaluationConfig(**config.yaml 的键)` 能构造(壳只传 dataclass 认识的键,多余键丢弃并告警) | 冒烟即验 | 对 yaml 键名做映射 |
| ⑥ | LLM 连通:config.yaml 的 base_url/model 在你环境可达(公司路由/MiniMax) | 单跑一个小场景 | 只改 config.yaml,不动代码 |

## 三、已知风险(壳已按此设计,但要人知道)
- **文本回声假阳**:引擎靠"判据文本 ∈ question"判匹配。壳的 question **只复述成立的判据**;
  被排除/数据不足的只报节点名。若合作方的 condition_matcher 是语义级(LLM)而非字面包含,
  这个策略依然安全(宁缺勿滥),但 full-match 率可能偏低——观察后可放宽。
- **成本**:每个"无匹配兄弟组"都触发 debate(N 辩论 agent + M 评委 × LLM 调用)。400 节点大树
  别直接上;先 suite_v2 小树标定单场景成本,再决定大树是否只喂剪枝后的局部子树
  (`rca_input.fault_tree.pruned_nodes` 里的节点集,壳可加 `--pruned-only`,目前未实现)。
- **确证档缺失**:评分制输出全进 probable_causes(见 result_adapter docstring)。若业务需要
  "确证"语义,需另定升档规则(如:路径全 full-match 且逐节点能对上 rca_input 的成立事实)——
  建议拿评测数据说话后再定。

## 四、三种得分机制 A/B/C(合作方要求的实测)
```bash
# 对 suite_v2 的每个场景(18 个,17 节点小树):
python skills/fault-tree-rca-debate/scripts/run_debate_rca.py \
  --rca-input skills/cs-scot-eval/suite_v2/scenarios/S01/rca_input.json \
  --tree-index <suite_v2 的 tree_index> --out-dir /tmp/ab/S01 --mechanism all
# 三份结果分别打分(打分器容忍 probable-only 输出):
python skills/cs-scot-eval/scripts/score_v2.py ... /tmp/ab/S01/rca.original.json   # 同理 softmax/ratio
```
汇总四维(命中/档位/无假阳/诚实)出对比表 → 赢者写进 run_debate_rca 的默认 `--mechanism`。
注:eval 的"禁止脚本批量产 rca.json"红线是防**考模型**作弊;这里被测对象就是程序引擎,属正当用法,
但成绩必须标注 `engine=debate`,与模型手工 RCA 的成绩分表呈现。

## 五、切换/回退
- 用辩论引擎:runner 之后跑 `run_debate_rca.py`(见 SKILL.md)。
- 回老路径:照旧用 `fault-tree-rca` 技能手工产出。两者写同一个 `result/rca.json`、
  过同一个 `validate_rca`、被同一套 eval 打分——**保留老路径当基线**(难度一 100% / 纠错 73%),
  引擎赢了评测再谈"全盘替代"。
