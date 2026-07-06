# 本地组装与核对清单(把合作方引擎接进来)

> 背景:合作方交付 = `fault_tree_evaluator_bfs_advanced.py` + `config.yaml` 两个文件。
> 引擎 .py **已原样入库**(vendor/,不含密钥);`config.yaml` 含 API 密钥,**不入库**、须本地放入。
> 下方核对清单 ①-⑤ 已按真实引擎源码逐条核毕(含真引擎离线冒烟:xlsx 生成 → question 渲染 →
> 真引擎 fallback 模式 → 三机制 rca.json → validate_rca 通过);只剩 ⑥(LLM 连通)依赖你的本地网络。

## 一、组装步骤(1 分钟)
1. 把合作方的 `config.yaml` 原样放进 `skills/fault-tree-rca-debate/vendor/`(引擎 .py 已在,gitignore 只挡 config)。
   **放好后再跑 install**(install 是整目录拷贝,会把引擎+config 一起带到 `~/.claude/skills/`)——
   之后固定用装好的路径跑本技能脚本,仓库目录搬家/改名也不受影响;仓库更新后重放 config 再重装。
2. `pip install pandas numpy pyyaml openpyxl requests`(引擎用 pandas/numpy/requests;壳用 yaml/openpyxl)。
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
| ⑤ | config.yaml 能映射到 `EvaluationConfig`(壳会自动**展平嵌套结构**如 `llm:{api_key}`→`llm_api_key`;LLM 关键字段一个都映射不到会**硬报错**,绝不让引擎拿空配置静默降级) | 冒烟即验(成功会打印"已映射 N 个字段") | 报错信息里有顶层键与已映射键对照,按它改 yaml 层级,或在 bridge.py 自行构造 |
| ⑥ | LLM 连通:config.yaml 的 base_url/model 在你环境可达(公司路由/MiniMax) | 单跑一个小场景 | 只改 config.yaml,不动代码 |

## 三、已知风险(壳已按此设计,但要人知道)
- **层级偏置(引擎语义,壳无法修)**:引擎建树只给 L4+ 节点设 sibling_nodes,L2/L3 节点该键缺失
  → 这两层零匹配节点走"单节点辩论"(无竞争辩护、无兄弟匹配惩罚),original 机制下浅层落败分支
  得分系统性偏高、可能挤进 Top-K;softmax/ratio **不受影响**(其兄弟分组由 bfs_evaluate 按
  parent_code 全层级正确计算)。解读 A/B/C 机制评测时把这一偏置与机制优劣分开看。
- **深度上限 L6(引擎语义)**:引擎建树止于 6 层,更深的行被静默丢弃;壳已加硬守卫——带判据节点
  重定基深度>6 时当场报错,提示截更小的子树,绝不无声出假结果。
- **文本回声假阳**:引擎靠"判据文本 ∈ question"判匹配。壳的 question **只复述成立的判据**;
  被排除/数据不足的只报节点名。若合作方的 condition_matcher 是语义级(LLM)而非字面包含,
  这个策略依然安全(宁缺勿滥),但 full-match 率可能偏低——观察后可放宽。
- **成本**:每条判据 = 1 次匹配 LLM 调用;每个"零匹配兄弟组"再加(组员数辩护 + 评委数打分)次调用,
  全部串行、引擎内单次请求超时写死 180s。大树直跑动辄几十分钟,提速见下节「提速手册」。

## 三‑2、提速手册(实测慢就按这个顺序调)
1. **`--pruned-only`(最大杠杆,壳已实现)**:只喂 `rca_input.fault_tree.pruned_nodes`
   (runner 剪枝定下的"有事实节点+祖先")的局部子树。只对**生产树**(判据全量自带)真正缩树;
   评测树判据本来就从 pruned_nodes 合回,开不开都一样(实测 30 场景 no-op)。
   rca_input 是完整形态(无 pruned_nodes)时自动回退全树、只提示不报错。
   语义边界(实测核定):成立/不成立/数据不足的**有事实**节点全保留,被砍的只有**从未被绑定**
   (uncovered)的分支——漏诊线索仍经 tree_issues.uncovered_condition_nodes 透传,不丢。
   两个代价要知道:① Top-K 只在局部子树内排;② **分数会膨胀**——debate/softmax/ratio 都是
   兄弟组相对量,剪掉未绑定兄弟后幸存路径分数上浮(mock 实测同一路径 softmax 0.45→0.63),
   confidence 档位又按绝对阈值定 → 开/关两种模式的分数与档位**互不可比**,机制 A/B 评测
   全程固定一种模式,且只用排名指标(hit@k/MRR)对比。
2. **config.yaml 调参(不改代码)**:`num_judging_agents: 3→2`(每个辩论组省 1/3 评委调用);
   `max_retries: 3→2`、`retry_delay: 1.0→0.5`(引擎单请求超时 180s 写死,retries=3 时一次
   网络卡死最多白等 ~9 分钟)。注意 `num_debate_agents` **调了没用**——引擎实际按兄弟组成员数
   起辩护 agent,该配置项不参与。
3. **别在会掐超时的环境里跑大树**:Claude Code 的 Bash 默认 10 分钟超时,大树必被拦腰杀掉
   (症状:进程消失、无 rca 产物、输出停在某个 Debate 中途)。要么单开终端手动跑,要么在
   `settings.json` 里加 `{"env": {"BASH_DEFAULT_TIMEOUT_MS": "3600000", "BASH_MAX_TIMEOUT_MS": "3600000"}}`。
4. **降级要知情**:LLM 连接彻底失败一次后,引擎会**永久**切关键词匹配 fallback 静默续跑
   (匹配语义变了、结果照出)。壳已加检测:打印 ⚠ 警告、`rca_debate_full.json` 落
   `llm_degraded: true`、rca.json 的 caveats 追加降级说明——看到就该修网重跑,别拿降级结果做结论。
- **确证档缺失**:评分制输出全进 probable_causes(见 result_adapter docstring)。若业务需要
  "确证"语义,需另定升档规则(如:路径全 full-match 且逐节点能对上 rca_input 的成立事实)——
  建议拿评测数据说话后再定。

## 四、三种得分机制 A/B/C(合作方要求的实测)——一键工具
```bash
# mock 先验管线(已在仓库内跑通 30 场景),真引擎就绪后换 --engine real:
python skills/fault-tree-rca-debate/scripts/ab_test_mechanisms.py --engine real --k 3 \
    [--only synth_中]   # 先小树标定成本,再放全量
```
数据集 = `cs-scot-eval/suite_real` 的 30 个冻结场景(rca_input/树/答案全部已入库,开箱即测)。
**指标口径(重要)**:机制对比用**档位无关的排名指标**(hit@1 / hit@3 / MRR / FP进榜)——
eval 的四档打分要求确证根因落在 `rca_result` 档,而本引擎是纯排序器、适配层诚实全放 probable 档,
四档分数量的是"排序器 vs 事实闭环"的语义差,不是三种机制谁好。真值="不报根因"的场景(excluded/honest)
排序器结构性做不到(无弃权),工具单列为「弃权能力缺口」——这是全盘替代要付的语义代价,与机制优劣分开看。
注:eval 的"禁止脚本批量产 rca.json"红线是防**考模型**作弊;这里被测对象就是程序引擎,属正当用法,
但成绩必须标注 `engine=debate`,与模型手工 RCA 的成绩分表呈现。

## 五、切换/回退
- 用辩论引擎:runner 之后跑 `run_debate_rca.py`(见 SKILL.md)。
- 回老路径:照旧用 `fault-tree-rca` 技能手工产出。两者写同一个 `result/rca.json`、
  过同一个 `validate_rca`、被同一套 eval 打分——**保留老路径当基线**(难度一 100% / 纠错 73%),
  引擎赢了评测再谈"全盘替代"。
