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
先认清**成本大头**:不是逐条判据匹配(线性,~1 次调用/条),而是**零匹配兄弟组的平方级辩论**——
引擎对组里**每个**零匹配成员都各自发起一轮"全组辩护+评委打分"(9 兄弟全零匹配 ≈ 9×(9+3) >100 次
调用/组,组内还嵌套 _quick_check_match 重复匹配兄弟判据)。数据没命中的分支越多,越慢。

1. **`--set K=V` 临时调参(盖在 config.yaml 之上,不动同事的文件)**:最实惠的是
   `--set num_judging_agents=2`(每组省 1/3 评委轮)与 `--set llm_temperature=0.2`
   (评委 JSON 更稳、少解析失败兜底);配 llm加固(见 4)时建议 `--set max_retries=1
   --set retry_delay=0.5`(内层快失败,重试交给外层退避)。引擎单请求超时 180s 写死改不了;
   `num_debate_agents` **调了没用**——引擎按兄弟组成员数起辩护 agent,该配置项不参与。
2. **`--pruned-only`(只在覆盖不全时有肉)**:只喂 `rca_input.fault_tree.pruned_nodes`
   (有事实节点+祖先)。**注意**:标准 generate→execute 流水线的覆盖闸门强制全叶绑定,
   绑定即有事实 → pruned_nodes ≈ 全树,本开关基本 no-op;它在局部绑定/覆盖率低走完整形态、
   或只想跑某数据源绑到的分支时才真缩树。评测树恒 no-op(判据本来就从 pruned_nodes 合回)。
   语义边界(实测核定):成立/不成立/数据不足的**有事实**节点全保留,被砍的只有**从未被绑定**
   (uncovered)的分支——漏诊线索仍经 tree_issues.uncovered_condition_nodes 透传,不丢。
   两个代价:① Top-K 只在局部子树内排;② **分数会膨胀**——debate/softmax/ratio 都是兄弟组
   相对量,剪掉兄弟后幸存路径分数上浮(mock 实测 softmax 0.45→0.63),confidence 又按绝对阈值
   定档 → 开/关两种模式的分数与档位**互不可比**,机制 A/B 全程固定一种模式、只用排名指标(hit@k/MRR)。
3. **别在会掐超时的环境里跑大树**:Claude Code 的 Bash 默认 10 分钟超时,前台/后台任务都吃——
   大树必被 SIGTERM(exit 143)拦腰杀掉,症状:进程消失、无 rca 产物、输出停在某个 Debate 中途。
   要么单开终端手动跑,要么在 `settings.json` 加
   `{"env": {"BASH_DEFAULT_TIMEOUT_MS": "3600000", "BASH_MAX_TIMEOUT_MS": "3600000"}}`(改完重启)。
4. **瞬时 502 不再毁全程(llm加固,默认开)**:引擎原设计"一次调用把内层重试耗尽 → 置
   connection_failed → 整程永久降级关键词匹配"。壳现在给 LLMClient.chat 套了外层重试
   (×3、退避 2s/4s,成功即撤销降级标记)——只包运输、零碰评分算法;`--no-llm-shield` 可关。
   哄骗网关的根因还得治:harness 与引擎共用同一 LLM 网关时,两边并发挤兑就容易 502,
   大树尽量在 harness 空闲时跑、或错开时间手动跑。
5. **降级要知情**:若 LLM **持续**失败,引擎仍会降级续跑(匹配语义已变)。壳的检测会打印 ⚠、
   `rca_debate_full.json` 落 `llm_degraded: true`、rca.json caveats 追加说明——
   看到就修网重跑,别拿降级结果做结论。
6. **还嫌慢?那得动算法**:每组辩一轮、组内成员共享评委打分(而不是每个零匹配成员各辩一轮)、
   并发调用——这些都动到核心算法执行结构,须与合作方商定后由其改版,壳不越界。
- **确证档缺失**:评分制输出全进 probable_causes(见 result_adapter docstring)。若业务需要
  "确证"语义,需另定升档规则(如:路径全 full-match 且逐节点能对上 rca_input 的成立事实)——
  建议拿评测数据说话后再定。

## 三‑3、疑难速查(真跑撞见的怪日志,每条都有实锤复现)
- **`LLM调用最终失败: None`(None!不是网络错)**:HTTP 200 拿到了响应,但 content 里
  **只有 thinking 块、没有 text 块**——思考型模型(MiniMax-M2 等)把 `max_tokens` 全烧在
  推理上,正文没写就被截断;引擎解析只认 `type=="text"`,取不到→重试耗尽→last_error 还是
  None→照样置 connection_failed(与断网同罪)。**对策:`--set llm_max_tokens=8192`**
  (思考型模型 2000 远远不够);时好时坏=思考长度不定,别当成网络抖动排查。
- **评委打分出现 `reason="默认分数"`**:该评委的 LLM 调用失败(或 JSON 解析失败),引擎兜底
  给 `1/组员数`。**单节点辩论组兜底分=1.0 满分**——两个评委失败+一个真评 0.75,均值能虚高到
  0.92(真跑实录)。看到"默认分数"要打折读,别把兜底当共识;失败次数多先按上一条修 max_tokens。
- **`检测到API连接失败,自动切换到fallback模式`**:出现即意味着后续所有**条件匹配**都改用
  关键词而非 LLM(辩论/评委不受影响,仍会尝试调 LLM)。本次结果结尾必带 ⚠ 降级警告与
  `llm_degraded: true`——端到端"干净跑通"的标准是全程不出现这行。

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
