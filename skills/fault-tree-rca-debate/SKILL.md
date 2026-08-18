---
name: fault-tree-rca-debate
description: 辩论式多代理 RCA 技能:吃 cs-scot-execute 备好的 rca_input.json + tree_index.json,自行调 LLM 做条件匹配+多代理辩论评分,产出四档契约的 rca.json(original/softmax/ratio 三种得分机制并出)。与 fault-tree-rca(模型手工判断)同一接缝、可互换;要用辩论引擎跑 RCA、或 A/B 对比三种得分机制时用。
---

# fault-tree-rca-debate —— 辩论式 RCA 技能

**这是一个"脚本引擎"技能,不是 prompt-only 技能**:它自己调 LLM API(配置见
`vendor/config.yaml`),不需要 harness 里的模型来判断。对上游零依赖:业务 Excel
的读取在 cs-scot-generate,本技能只吃归一化的 `tree_index.json` + `rca_input.json`。

## 在流水线里的位置(与 fault-tree-rca 同一接缝、可互换)
```
runner 跑完 script 节点 → 本轮结果目录 result-<运行号>/rca_input.json
  ├─ 手工路径:用「fault-tree-rca」技能,模型逐节点判断 → rca.json(事实闭环,四档)
  └─ 本技能  :跑下面这条命令,辩论引擎评分       → rca.json(评分排序;候选(路径末端叶子)
     被证据引擎实测成立且路径无实测「不成立/冲突」上级的升确证档 rca_result——与手工
     确证闸**同口径 v4**:未绑定/数据不足上级默认放行(caveats 提示"此处无判断");
     层级矛盾降疑似带卡点;升档凭 execute 的事实,不凭辩论得分)
```
```bash
python "<本目录>/scripts/run_debate_rca.py" --rca-input "<结果目录>/rca_input.json" --mechanism all
```
参数名精确是 `--rca-input`(`--input` 也认);标准产物布局下 `--tree-index`(自动在
`<OUT>/tree/` 找唯一的 `*.tree_index.json`)和 `--out-dir`(默认=rca_input 所在目录)
**不用传**;评测场景等非标准布局才需要显式给这两个参数。

- **事实短路筛+辩题分诊(默认开,`--no-screen` 关)**:跑引擎前先借同门 draft_rca 机械分类,
  按候选情况四路分诊(辩题三类=冲突/竞争/带伤,判据全机械、零模型参与)——
  ①**唯一干净候选**(自身成立+路径无实测不成立/冲突,且无其他候选)→ 引擎**整场跳过**
  (0 次 LLM 调用),finalize 直接机械合成 rca.json,**不写 rca_debate_full.json**(评估侧靠它
  分引擎,机械结果不冒充辩论结果),审计落 `debate_screen.json`;
  ②**多候选竞争**(候选总数≥2 **且含干净候选**)→ 全部候选进辩题,引擎辩其所在分支
  (祖先链+子树求并,与 --pruned-only 求交)**分主次/并发/连锁与排序**——确证仍凭机械事实
  (与手工闸同口径),辩论得分只管排序;引擎 top-k 漏掉的干净候选由机械合成**垫尾**补位
  (不占辩论排序,并从疑似档剔重防同一节点占两档);角色卷宗开头带【辩题聚焦】注记
  (竞争款:不辩真伪只辩排序,背景不得翻案);
  ③**纯争议候选**(自身冲突/层级矛盾,一个或多个)→ 引擎**只辩争议候选所在分支**(多个
  求并),真伪凭辩论质证+人工复核——冲突候选真伪未定,不套竞争款"不辩真伪"的说法,
  聚焦注记用争议款(点名争点;实测排除的不得翻案,未验证/数据不足的如实标注);
  ④无成立候选/md 形态/同门缺失 → 自动退回全树辩论。
  这就是"1 根因 vs 100 节点"的解:100 节点里通常只剩几个真要辩,唯一明确根因时 0 轮辩论。
- 产物:`rca.json`(默认=original 机制)+ `rca.original/softmax/ratio.json` 三份 +
  `rca_debate_full.json`(引擎原始输出,含辩论记录,审计用)。每份含 Top-K 条根因路径,
  `--k` 调(默认 10;纯切片零成本,config 的 top_k 与引擎默认均被它覆盖)。
  注意排名靠后的条目分数低、confidence 多为 low——它们是"候选清单的尾巴",不是并列结论。
- **版本管理**:重跑时上一轮 debate 产物自动归档到 `<out-dir>/debate_runs/<时间戳>/`,
  out-dir 里永远只有当前轮——已存在的 rca*.json 一律是
  上一轮的,要新结论直接重跑即可,别把旧产物当本轮结果汇报。
- 跑完自动过与手工路径同一道闸门:`fault-tree-rca/scripts/validate_rca.py`。

## 一次性配置(**只在首次使用/换机器/换 config 时做**;做过就跳过,别每次重复)
1. 把含 API 密钥的 `config.yaml` 放进本技能 `vendor/`(gitignore 挡住不入库);
   **放好后再 install**(整目录拷贝,引擎+config 一起带走,之后固定用装好的路径)。
2. `pip install pandas numpy scipy pyyaml openpyxl requests`(scipy 是引擎 V2 置信区间计算的硬依赖)。
3. 验一次:`python scripts/probe_llm.py`(exit 0=通)→ `--mock` 冒烟 → 真引擎。

## 执行手册(跑本技能的 agent 按此操作;这些约束不需要用户在提示词里重复)
1. **别把探针/冒烟当仪式**:probe_llm 与 `--mock` 只在首次配置、或真引擎行为异常
   (0 辩论、毫秒级完成、连续失败)时用于诊断;日常直接跑真引擎那一条命令。
2. **超时给足**:真引擎大树一跑几十分钟,Bash 的 timeout 必须设到**环境允许的最大值**
   (BASH_MAX_TIMEOUT_MS,通常 3600000=1 小时,这是 Claude Code 的硬上限、设更大也会被钳;
   **不要**自作聪明给 10 分钟),或后台运行。进度看 `<out-dir>/debate_progress.txt`
   (日志式追加,每评估一个节点一行"HH:MM:SS 已评估 N/总数;节点名(得分,类型)",可 tail 跟踪;
   每轮运行有分隔行),长跑时每隔几分钟读末尾几行即可向用户播报进度。分不清"还在跑"还是
   "挂了"看 `<out-dir>/debate_heartbeat.txt`(每次 LLM 调用完成刷新;超过 ~20 分钟不动
   才值得怀疑真挂——180s 超时×重试的最坏栈就这么长)。
   若仍被超时杀(exit 143):**不要原地重试**——把完整命令写进 `<OUT>\run_debate.bat`,
   请用户在自己的终端跑,等确认完成再继续。
3. **别加没让加的开关、别改配置**:默认值已按最优配好(并行×4、匹配缓存、LLM 加固、
   重试收敛,开跑头几行会打印这些状态)。`--pruned-only` 在标准全绑定流水线上是 no-op,
   不要主动加;任何情况下不要修改 vendor/config.yaml。
4. **结果合格线**:输出出现「检测到API连接失败,自动切换到fallback模式」或结尾 ⚠ 降级警告
   → 本次结果**作废**,如实报告、别拿它下结论;`rca_debate_full.json` 的 `llm_call_failures`
   占总调用比例偏高(>10%)也要报告并建议错峰重跑。零星几次属正常(外层加固已自愈)。
5. **LLM 连续 5xx/超时的处置**:开跑就成串地报 500/502/超时 → **停下来**(别硬跑到降级出废结果),
   跑一次 `python <本目录>/scripts/probe_llm.py` 定位:探针也 5xx = 网关侧故障/过载,报告用户、
   建议错峰(harness 与引擎共用网关,harness 干重活时尤其容易挤兑);探针通 = 瞬时抖动,直接重跑
   (壳会自动归档上一轮产物)。这是探针的正确用途——诊断,不是每次跑前的仪式。
6. **汇报口径**:validate_rca 结果 + `rca.original/softmax/ratio.json` 三份的 Top-K 根因
   路径**并排对比**(三种机制的分数横向不可比,只比各自的排序;详见 references/engine_ops.md)。

## 常用开关(细节见 references/engine_ops.md;默认值已按"开箱即最优"配好,通常一个开关都不用带)
- 并行**默认开**(`--parallel 4`;`--parallel 1` 退回纯串行):匹配/辩护/评委等独立 LLM
  循环并发执行 + 匹配记忆化缓存(默认开,`--no-match-cache` 关)。评分公式逐字保留、
  实测结果与串行逐位一致,小样 4.4×提速。并发闸自适应:网关回 429/503 自动减半、恢复后逐档回升,不用手动降。
- **角色卷宗**(默认开,`--no-agent-question` 关):辩论侧各角色(辩护/攻击/回应/一致性/评委)
  收到的 question 换成分层卷宗——**判据原文全员必读**(含被证伪/缺数节点的,附实测与反证,
  攻击者拿数据说话、评委按"实测优先于辩词"裁决),任务框架按角色、信息全员对称不裁剪;
  **机械匹配器不受影响**,仍吃回声安全版 question(防误配红线不变)。引擎升级改了类名/签名
  会逐项安全跳过退回原 question。
- **匹配预播种**(默认开,`--no-seed-match` 关):execute 已判定的节点结论直接写进匹配
  缓存,匹配层不再调 LLM 重新发现已知答案(30 场景实测与理想 LLM 匹配零分歧,还消除
  采样随机性);**辩护记忆化**(默认开,`--no-debate-cache` 关):同(节点,组,question)
  的辩护只生成一次(引擎原样会重复采样 m 次)。
- **回程事实感知**:rca.json 的 evidence_path.actual 回填证据引擎实测;叶子实证成立
  托底 medium、被数据证伪封顶 low 并挂 ⚠ 冲突旗标;blocked_at 指名"数据不足"节点;
  全场景无任何成立事实时(该弃权的场景)候选降入 undetermined 而非硬报 probable。
- `--set K=V`:临时覆盖引擎参数(盖在 config.yaml 上),如 `--set num_judging_agents=2`(提速)、
  `--set llm_max_tokens=16384`(思考型模型要给足,防"LLM调用最终失败: None")。
- LLM 传输加固默认开(瞬时 502 不再让引擎永久降级;`--no-llm-shield` 关);加固接管重试后,
  config 里过猛的内层重试(如 30 次/15s)会**自动收敛**为快失败(--set 显式指定的不动)。
- `--pruned-only`:只喂剪枝局部子树——仅在绑定覆盖不全时才真缩树。
- `--mock`:确定性假引擎跑通适配层(不调 LLM、不需要 config)——自测/CI 用。
- 耗时大头是"零匹配兄弟组的平方级辩论",全树串行直跑动辄几十分钟,别在 10 分钟超时的
  harness Bash 里跑——先读 `references/engine_ops.md`「提速手册」。

## 内部构造(谁负责什么)
`vendor/` 是评分引擎核心(算法约定**不在本仓修改**,上游发新版整文件替换,详见
vendor/README.md);`scripts/` 是本技能的 I/O 层:

| 文件 | 职责 |
|---|---|
| `scripts/facts_to_question.py` | 证据→文本桥:把 rca_input 的确定性事实渲染成引擎的 question。**回声策略**:只有"成立"的判据原文复述(让文本匹配命中),"不成立/数据不足"只报节点名(防误匹配假阳)。 |
| `scripts/tree_to_vendor_xlsx.py` | tree_index → 引擎格式 xlsx;`Ln Code` 列=我们的 node_id(nN),让输出天然带主键;带 L6 深度守卫与成本预估。 |
| `scripts/result_adapter.py` | 引擎 top_k×3 → 四档 rca.json。默认进 probable_causes;**事实升档(口径 v4)**:候选(路径末端真叶)被证据引擎实测成立、且路径无实测「不成立/冲突」上级 → 进 rca_result(升档凭事实、不凭得分);未绑定/数据不足上级默认放行(提示"此处无判断");层级矛盾降疑似带卡点。未升档条目 judgment 用中性词,不冒用「成立/数据不足」保留字。 |
| `scripts/run_debate_rca.py` | 编排 + 引擎装载(`_load_processor` 是唯一接口耦合点)+ 配置展平/--set 覆盖 + LLM 加固 + 降级检测。 |
| `scripts/mock_vendor.py` | 引擎的确定性假实现(形状对、算法不复刻),支撑无 LLM 自测。 |
| `scripts/probe_llm.py` | LLM 连通性探针(脱敏输出)。 |
| `scripts/ab_test_mechanisms.py` | 三种得分机制的一键 A/B/C 评测。原配的 cs-scot-eval 冻结场景已随该技能从本仓移除,需自备带答案场景并用 `--suite/--trees` 显式传入。 |

## 三种得分机制(original / softmax / ratio)怎么选
需实测定夺(原内置评测集 cs-scot-eval 已从本仓移除,需自备带标准答案的场景):
逐场景跑本引擎(`--mechanism all`),用**排名指标**(hit@1/hit@3/MRR)对比,
赢者当默认——一键工具与指标口径、original 机制的层级偏置等注意事项,
见 `references/engine_ops.md`。成绩标注 `engine=debate`,别与"模型手工 RCA"的成绩混表。
