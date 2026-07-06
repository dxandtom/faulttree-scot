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
runner 跑完 script 节点 → result/rca_input.json
  ├─ 手工路径:用「fault-tree-rca」技能,模型逐节点判断 → rca.json(事实闭环,四档)
  └─ 本技能  :跑下面这条命令,辩论引擎评分       → rca.json(评分排序,恒 probable 档)
```
```bash
python "<本目录>/scripts/run_debate_rca.py" --rca-input "<OUT>/result/rca_input.json" --mechanism all
```
参数名精确是 `--rca-input`(`--input` 也认);标准产物布局下 `--tree-index`(自动在
`<OUT>/tree/` 找唯一的 `*.tree_index.json`)和 `--out-dir`(默认=rca_input 所在目录)
**不用传**;评测场景等非标准布局才需要显式给这两个参数。
- 产物:`rca.json`(默认=original 机制)+ `rca.original/softmax/ratio.json` 三份 +
  `rca_debate_full.json`(引擎原始输出,含辩论记录,审计用)。
- **版本管理**:重跑时上一轮 debate 产物自动归档到 `<out-dir>/debate_runs/<时间戳>/`,
  out-dir 里永远只有当前轮(与 runner 的 runs/ 同一哲学)——已存在的 rca*.json 一律是
  上一轮的,要新结论直接重跑即可,别把旧产物当本轮结果汇报。
- 跑完自动过与手工路径同一道闸门:`fault-tree-rca/scripts/validate_rca.py`。

## 一次性配置(**只在首次使用/换机器/换 config 时做**;做过就跳过,别每次重复)
1. 把含 API 密钥的 `config.yaml` 放进本技能 `vendor/`(gitignore 挡住不入库);
   **放好后再 install**(整目录拷贝,引擎+config 一起带走,之后固定用装好的路径)。
2. `pip install pandas numpy pyyaml openpyxl requests`。
3. 验一次:`python scripts/probe_llm.py`(exit 0=通)→ `--mock` 冒烟 → 真引擎。

## 执行手册(跑本技能的 agent 按此操作;这些约束不需要用户在提示词里重复)
1. **别把探针/冒烟当仪式**:probe_llm 与 `--mock` 只在首次配置、或真引擎行为异常
   (0 辩论、毫秒级完成、连续失败)时用于诊断;日常直接跑真引擎那一条命令。
2. **超时给足**:真引擎大树一跑几十分钟,Bash 的 timeout 必须设到**环境允许的最大值**
   (BASH_MAX_TIMEOUT_MS,通常 3600000=1 小时,这是 Claude Code 的硬上限、设更大也会被钳;
   **不要**自作聪明给 10 分钟),或后台运行。进度看 `<out-dir>/debate_progress.txt`
   (每评估一个节点刷新一行"已评估 N/总数"),长跑时每隔几分钟读一次即可向用户播报进度。
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
  实测结果与串行逐位一致,小样 4.4×提速;并发调大易压垮网关吃 502。
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
| `scripts/result_adapter.py` | 引擎 top_k×3 → 四档 rca.json。全进 probable_causes(评分≠事实闭环,rca_result 诚实置空);judgment 用中性词,不冒用「成立/数据不足」保留字。 |
| `scripts/run_debate_rca.py` | 编排 + 引擎装载(`_load_processor` 是唯一接口耦合点)+ 配置展平/--set 覆盖 + LLM 加固 + 降级检测。 |
| `scripts/mock_vendor.py` | 引擎的确定性假实现(形状对、算法不复刻),支撑无 LLM 自测。 |
| `scripts/probe_llm.py` | LLM 连通性探针(脱敏输出)。 |
| `scripts/ab_test_mechanisms.py` | 三种得分机制的一键 A/B/C 评测(吃 cs-scot-eval 冻结场景)。 |

## 三种得分机制(original / softmax / ratio)怎么选
需实测定夺。用本仓评测当裁判:对 `cs-scot-eval` 的带答案场景逐个跑本引擎
(`--mechanism all`),用**排名指标**(hit@1/hit@3/MRR)对比,赢者当默认——一键工具与
指标口径、original 机制的层级偏置等注意事项,见 `references/engine_ops.md`。
成绩标注 `engine=debate`,别与"模型手工 RCA"的成绩混表。
