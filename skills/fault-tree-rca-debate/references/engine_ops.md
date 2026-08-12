# 引擎运维手册(风险 / 提速 / 疑难 / 机制评测 / 升级核对)

> 本技能的评分引擎核心在 `vendor/`(算法不在本仓修改,详见 vendor/README.md)。
> 日常使用看 SKILL.md 就够;本文是深水区:真跑踩过的坑、调参依据、升级引擎时的核对方法。

## 一、已知风险(壳已按此设计,但要人知道)
- **层级偏置(引擎语义,壳无法修)**:引擎建树只给 L4+ 节点设 sibling_nodes,L2/L3 节点该键缺失
  → 这两层零匹配节点走"单节点辩论"(无竞争辩护、无兄弟匹配惩罚),original 机制下浅层落败分支
  得分系统性偏高、可能挤进 Top-K;softmax/ratio **不受影响**(其兄弟分组由 bfs_evaluate 按
  parent_code 全层级正确计算)。解读 A/B/C 机制评测时把这一偏置与机制优劣分开看。
- **深度上限 L6(引擎语义)**:引擎建树止于 6 层,更深的行被静默丢弃;壳已加硬守卫——带判据节点
  重定基深度>6 时当场报错,提示截更小的子树,绝不无声出假结果。
- **文本回声假阳**:引擎靠"判据文本 ∈ question"判匹配。壳的 question **只复述成立的判据**;
  被排除/数据不足的只报节点名。若引擎的 condition_matcher 是语义级(LLM)而非字面包含,
  这个策略依然安全(宁缺勿滥),但 full-match 率可能偏低——观察后可放宽。
- **确证档(升档口径 v3,与手工确证闸同口径)**:路径末端叶子(候选)被证据引擎实测「成立」且是带判据真叶 → 升 rca_result;**祖先状态一律不拦档**、机械落 caveats 逐类点名(数据不足/未绑定=零信息;实测不成立/冲突=层级矛盾,⚠卡点写清矛盾与复核方向;全路径实测闭环单独注明为最强成色);候选证据冲突(成立与强反证并存)不升。**升档依据是 execute 的事实,不是辩论得分**。叶子非真叶(截断)/叶子非成立 → 留在 probable(截断与实测态落在 evidence 的 judgment 上,validate 认账)。回程事实感知其余部分:叶子实证
  成立托底 medium、被证伪封顶 low+⚠、blocked_at 指名数据不足节点、**弃权垫片**(全场景无
  成立事实→候选降入 undetermined,补上"排序器无弃权"的语义缺口)。
- **成本**:每条判据 = 1 次匹配 LLM 调用;每个"零匹配兄弟组"再加(组员数辩护 + 评委数打分)次调用,
  全部串行、引擎内单次请求超时写死 180s。大树直跑动辄几十分钟,提速见下节。

## 二、提速手册(实测慢就按这个顺序调)
先认清**成本大头**:不是逐条判据匹配(线性,~1 次调用/条),而是**零匹配兄弟组的平方级辩论**——
引擎对组里**每个**零匹配成员都各自发起一轮"全组辩护+评委打分"(9 兄弟全零匹配 ≈ 9×(9+3) >100 次
调用/组,组内还嵌套 _quick_check_match 重复匹配兄弟判据)。数据没命中的分支越多,越慢。

0. **并行 + 匹配缓存(提速第一杠杆,scripts/engine_boost.py;默认开 `--parallel 4`)**:
   - 并行:把三处**彼此独立**的串行 LLM 循环(逐条件匹配、逐成员辩护、逐评委打分、逐兄弟
     quick check)换成线程池并发,全局并发由信号量封顶在 N——嵌套并行也不会超发。
   - 缓存(默认开):同一(question, 判据集)只问一次 LLM——引擎在每个零匹配成员的辩论里
     都重查全组兄弟,缓存把这些重复检查全部吃掉。
   - 语义:评分公式与聚合逻辑**逐字拷自 vendor**,带源码哈希守卫——上游发新版引擎、方法
     源码一变,补丁自动跳过退回串行并告警,绝不带着过期拷贝静默跑。实测(固定应答假 LLM):
     Top-K 与路径分和串行**逐位一致**,小样 4.4× 提速、HTTP 调用 44→32。
   - N 别贪:与 harness 共用网关时并发越高越容易 502,推荐 4;`--no-match-cache` 关缓存。
1. **`--set K=V` 临时调参(盖在 config.yaml 之上,不动配置文件)**:最实惠的是
   `--set num_judging_agents=2`(每组省 1/3 评委轮);评委 JSON 不稳可降 llm_temperature。
   内层重试不用再手调:llm加固(见 4)开启时,config 里过猛的 max_retries/retry_delay
   (如 30 次/15s——重试间隔按次数递增,光睡眠就 >100 分钟/调用)会**自动收敛**为
   快失败(2 次/1s),重试全权交给外层退避;--set 显式指定则尊重原值。
   引擎单请求超时 180s 写死改不了;`num_debate_agents` **调了没用**——引擎按兄弟组
   成员数起辩护 agent,该配置项不参与。
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
   connection_failed → 整程永久降级关键词匹配"。壳给 LLMClient.chat 套了外层重试
   (×3、退避 2s/4s,成功即撤销降级标记)——只包运输、零碰评分算法;`--no-llm-shield` 可关。
   根因还得治:harness 与引擎共用同一 LLM 网关时,两边并发挤兑就容易 502,
   大树尽量在 harness 空闲时跑、或错开时间手动跑。
5. **降级要知情**:若 LLM **持续**失败,引擎仍会降级续跑(匹配语义已变)。壳的检测会打印 ⚠、
   `rca_debate_full.json` 落 `llm_degraded: true`、rca.json caveats 追加说明——
   看到就修网重跑,别拿降级结果做结论。
6. **还嫌慢?那得动算法**:每组辩一轮、组内成员共享评委打分(而不是每个零匹配成员各辩一轮)、
   并发调用——这些都动到引擎核心的执行结构,须与算法上游商定后由其改版,本仓不越界。

## 三、疑难速查(真跑撞见的怪日志,每条都有实锤复现)
- **`LLM调用最终失败: None`(None!不是网络异常抛错)**:HTTP 200 拿到了响应,但 content 里
  **没有 text 块**——常见两因:思考型模型(MiniMax-M2 等)把 `max_tokens` 烧完、正文被截断
  (确保 config max_tokens ≥ 8192,思考型模型 2000 远远不够);或网关高负载时返回空/仅思考
  内容(瞬时,shield 外层重试会自愈,散发属正常)。引擎解析只认 `type=="text"`,取不到→
  重试耗尽→last_error 还是 None→照样置 connection_failed(与断网同罪,shield 会在下次
  成功时撤销)。跑完看 `rca_debate_full.json.llm_call_failures`:个位数散发可接受,
  偏多(如 >10%调用)建议错峰重跑。
- **评委打分出现 `reason="默认分数"`**:该评委的 LLM 调用失败(或 JSON 解析失败),引擎兜底
  给 `1/组员数`。**单节点辩论组兜底分=1.0 满分**——两个评委失败+一个真评 0.75,均值能虚高到
  0.92(真跑实录)。看到"默认分数"要打折读,别把兜底当共识;失败次数多先按上一条修 max_tokens。
- **`检测到API连接失败,自动切换到fallback模式`**:出现即意味着后续所有**条件匹配**都改用
  关键词而非 LLM(辩论/评委不受影响,仍会尝试调 LLM)。本次结果结尾必带 ⚠ 降级警告与
  `llm_degraded: true`——端到端"干净跑通"的标准是全程不出现这行。
- **开跑就成串 HTTP 500(而 harness 同网关正常)**:先排除代理(输出开头应有
  `[net] 已把 LLM 主机加入 NO_PROXY`——壳每次真跑都自动设,不依赖 shell/harness);
  然后怀疑**突发并发**:并行默认 ×4,加上 harness 自身调用同挤一个网关,路由器过载
  常以 500 表现。用 `--parallel 2`/`--parallel 1` 跑一轮对照,500 消失即坐实——
  之后错峰跑或常驻降并发。持续 5xx 重试无用(内外层重试只救瞬时抖动),按 SKILL
  执行手册第 5 条停下诊断。

## 四、三种得分机制 A/B/C——一键评测
```bash
# mock 先验管线,真引擎就绪后换 --engine real:
python skills/fault-tree-rca-debate/scripts/ab_test_mechanisms.py --engine real --k 3 \
    --suite <带答案场景目录> --trees <树索引目录> \
    [--only synth_中]   # 先小树标定成本,再放全量
```
数据集:原配的 `cs-scot-eval/suite_real` 30 个冻结场景已随 cs-scot-eval 技能从本仓移除,
现需自备带标准答案的场景,用 `--suite/--trees` 显式传入(目录结构见脚本 docstring)。
**指标口径(重要)**:机制对比用**档位无关的排名指标**(hit@1 / hit@3 / MRR / FP进榜)——
四档打分(要求确证根因落在 `rca_result` 档)对本引擎要谨慎读:引擎是纯排序器,升档凭
execute 的事实(候选自身实测成立)——四档分数量的是"排序器+事实升档"的组合效果,与三种
机制孰优是两个问题,别混在一张表里下结论。
真值="不报根因"的场景(excluded/honest)排序器结构性做不到(无弃权),工具单列为
「弃权能力缺口」——这是全盘替代要付的语义代价,与机制优劣分开看。
成绩必须标注 `engine=debate`,与模型手工 RCA 的成绩分表呈现。

## 五、切换/回退
- 用辩论引擎:runner 之后跑 `run_debate_rca.py`(见 SKILL.md)。
- 回手工路径:照旧用 `fault-tree-rca` 技能产出。两者写同一个结果目录的 `rca.json`、
  过同一个 `validate_rca`、被同一套 eval 打分——**保留手工路径当基线**(难度一 100% / 纠错 73%),
  引擎赢了评测再谈"全盘替代"。

## 六、引擎升级核对清单(vendor 换新版时跑一遍;当前版本已逐条核毕)
接口假设逐条验证;不符只改 `run_debate_rca._load_processor` 一处(或新建 vendor/bridge.py)。

| # | 假设 | 验证方法 | 不符时 |
|---|---|---|---|
| ① | `FaultTreeProcessor(EvaluationConfig)` / `load_fault_tree(path)→df` / `build_tree_structure(df)→tree` / `find_top_k_paths(tree, question, k)→(orig, softmax, ratio, debate_records)` | 直接跑一步冒烟 | 改 `_load_processor` 与调用三行 |
| ② | loader 接受壳生成的 xlsx(列:`Ln Code / Ln / Ln(Chinese)` + `Judgment Condition(Chinese)`);**带条件的中间节点单独成行**且按 Code 合并、不会被当成叶子 | `tree_to_vendor_xlsx.py` 生成后让其 load,打印树规模对比 tree_index 节点数 | 缺列→`write_xlsx` 补常量列;中间行异常→修 build_rows 的自带行布局(自带行是硬约束,没有"关掉"的逃生门——去掉会复活判据涂抹) |
| ③ | 输出路径里节点带 `code`(=node_id)、`node_score`、`match_type`,路径尾巴带三个 `*_path_score` | 看 `rca_debate_full.json` | 改 `result_adapter._split_path/_judgment` 取键 |
| ④ | 空 `judgment_conditions` 的节点(纯结构节点/根)不把路径得分打零 | 冒烟看 top 路径得分是否全 0 | 与上游确认空条件语义;必要时 xlsx 给结构节点填"无条件进入" |
| ⑤ | config.yaml 能映射到 `EvaluationConfig`(壳自动递归展平嵌套;LLM 关键字段全缺会**硬报错**) | 冒烟即验(成功打印"已映射 N 个字段") | 按报错对照改 yaml 层级,或 bridge.py 自行构造 |
| ⑥ | LLM 连通:config.yaml 的 base_url/model 本机可达 | `scripts/probe_llm.py` | 只改 config.yaml,不动代码 |

冒烟命令(先 `--mock` 验壳,再去掉换真引擎):
```bash
python skills/fault-tree-rca-debate/scripts/run_debate_rca.py \
  --rca-input <某场景>/result-<运行号>/rca_input.json \
  --tree-index <某场景>/tree/<场景>.tree_index.json \
  --out-dir <临时目录> --mechanism all --mock
```
