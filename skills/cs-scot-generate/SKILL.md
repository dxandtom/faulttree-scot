---
name: cs-scot-generate
description: 把故障树(Excel)+ 若干指标数据编译成场景诊断产物:各指标解析脚本、SCoT 执行计划 plan.json、故障树 md。用于为网络保障场景(如某接入失败场景)生成可执行的诊断与根因定位。指标种类由用户数据决定,不固定。
---

# cs-scot-generate —— 故障诊断产物生成器

**本文件是给你照做的操作手册,已经加载进来了——不需要、也不要再次加载或调用它。**
**"手册已加载"≠"任务完成":只有当你用 Bash / 文件工具真正跑完下面的流水线、产出 `plan.json` 等文件,任务才算完成。**
**现在立刻开始:你的下一个动作就是用 Bash 运行【步骤 1】那条命令,然后逐步往下做,每步把输出汇报出来。**

你的任务:把一棵**故障树(Excel)+ 若干指标的数据**,编译成某场景的诊断产物——① 各指标解析脚本 `scripts/<指标>_analyzer.py`(同一个通用引擎的副本);② SCoT 执行计划 `plan.json`;③ 故障树 md `tree/<场景>.md`。

> - 下文命令里的 `scripts/xxx.py` / `references/xxx.md` 指**本说明文件同目录**下的文件(通常在 `~/.claude/skills/cs-scot-generate/` 下),用绝对路径调用。
> - 输入路径可能带空格、括号、中文(如 `EnhancedExcelDataExport (7).xlsx`),命令里**整条路径务必加引号**。
> - 产物是数据/资产(交给执行器 cs-scot-execute 去跑),不是可执行程序。

## 核心:按 A–J 模板生成"逐条件分析脚本"(generate 编译、execute 只跑)
故障树条件分 **A–J 十类**(阈值/差值/字段匹配/组合/占比TopN/配置/日志/信令/特征/人工)。统计逻辑已**预制成测过的内核库** `scripts/condition_kernels.py`;generate 时**你按真实数据,为每个指标填一份"逐条件分析脚本" `<ind>_analyzer.py`**——读表 → 逐条件统计 → 输出事实。
- 默认**直接套模板**(`evaluate_binding`:选类别、对哪列、阈值/变换;组前缀 `Common:` 自动剥);
- 仅当数据格式特殊、模板不够用时,才为个别条件写 `custom` 函数(会在步骤3拿真实数据真跑、出错即暴露)。

这样**generate 产出"读表+统计"的代码;execute 只按序跑脚本(快)+ 末尾一次 RCA(慢)**。是否成立/路径回溯/跨源组合,交给 `fault-tree-rca`。绑定字段与 A–J 参数见 `references/condition_binding_schema.md`;先用 `analyzer.py` 看各源真实列(概览:列清单+数值摘要/十分位+nulls)。

## 输入
- 故障树 `.xlsx`(绝对路径)。
- 场景名(作 `--start-node` 截子树、给产物命名)。
- 每种指标一份数据(真实或样例皆可;用于跑验证)。

## 产物目录(统一、固定,别到处存)
所有中间过程 + 最终产物都写进**同一个目录** `<OUT>`,默认就在**故障树 xlsx 旁边**:
```
<OUT> = <故障树 xlsx 所在目录>/scot产物/<场景名>/
```
- 步骤 1 的 `excel_to_tree.py` 会**自动算出并打印** `<OUT>`;**后续步骤 2-4 全部用同一个 `<OUT>`**,不要另起临时目录。
- 改位置:给步骤 1 / 4 传 `--out-base "<根目录>"`(产物 = `<根目录>/scot产物/<场景名>`),或给步骤 4 传 `--out "<目录>"` 直接指定。

## 流水线

### 步骤 1:Excel → 该场景子树 md(并定位 `<OUT>`)
```bash
python "<本目录>/scripts/excel_to_tree.py" "<故障树xlsx>" --start-node "<场景名>"
```
- **不带 `-o`**:子树写到 `<OUT>/tree/<场景名>.md`,并打印「本场景产物目录: `<OUT>`」——**记住这个 `<OUT>`,步骤 2-4 都用它**。
- 同时产 **`<OUT>/tree/<场景名>.tree_index.json`**:权威拓扑(用 `Ln Code` 当稳定 **node_id** + 父子 + 条件 + AND/OR 提示 + 数据源)。后面填 `node_id`、RCA 压缩上下文都靠它。
- 场景默认在 L2(`--start-level` 默认 2);`--exclude-pattern` 默认排除噪声节点 `萃取新增节点-.*`。出来应是几十 KB 的单场景子树。

### 步骤 2:归集产物(拷模板库 + 写待填脚本骨架 + 生成 SCoT 计划)
```bash
python "<本目录>/scripts/assemble_scenario.py" \
  --scenario "<场景名>" --indicators <ind1>,<ind2> --out "<OUT>"
```
它把 A–J 模板库 `condition_kernels.py`、概览引擎 `analyzer.py`、绑定契约拷进 `<OUT>/scripts/`,为每个指标写一份**待填骨架** `<ind>_analyzer.py`,并产出 `plan.json`(`各<ind>_analyzer(script)→rca(agent_skill)`)。`--out` 用步骤 1 打印的 `<OUT>`。格式见 `references/plan_schema.md`。

### 步骤 2.5(关键的一步):为每个 `<ind>_analyzer.py` 填 `CONDITIONS`
先看各源真实列,再把故障树每条判据填进对应源脚本:
```bash
python "<OUT>/scripts/analyzer.py" "<该指标数据>" --lean -o /tmp/<ind>_overview.json   # 精简概览:列名/量纲/类别取值
```
`--lean` 砍掉十分位+样例(填 CONDITIONS 用不到),只留**列名 / min·max·mean(定阈值/量纲)/ 类别取值(C 类匹配)**——读得快、上下文小、不影响正确性。多页签 xlsx:先不带 `--sheet` 看页签清单,再 `--sheet <名> --lean` 看某页详情。
按 `references/condition_binding_schema.md`,把判据**路由到对应源**、填进该源脚本的 `CONDITIONS`(选 A–J 类、对哪列〔裸名即可〕、阈值/变换;对不上标 `decision:insufficient_data`)。**每条都填 `node_id`**(取自 `tree_index.json` 里的短 id `nN`)——RCA 靠它把事实挂回树、并把上下文压成局部子树。**默认套模板,非改不可才写 `custom`**。跨源的组合各源分别出子事实,AND/OR 留给 RCA 合。
> **⚡ 提速:并行填,但子代理只"返回"、不"写文件"**。三个源互相独立 → 每个源开一个子 agent 并行填(各自只看故障树 + 自己那份概览)。**关键:让子代理把它那份 `CONDITIONS`(Python 字面量列表)直接放在最终回复里返回,别让它自己写 `<ind>_analyzer.py`**——子代理写文件常因权限/路径失败、还拖很久(实测 1–2 小时);改由**主代理收齐后用 Edit 机械地把每份写进对应脚本**(模型只提案、落盘归主流程)。
> **⚠ 写进去后核对条数**:主代理写完,确认每个 `<ind>_analyzer.py` 的 `CONDITIONS` 条数与该源相关判据相称;**0 条或异常少 = 那份没收齐/没写对,重来**——别当成"本源无判据"、更别往下跑。
> **泛化第一(重要)**:绑定按**格式+语义**,别被这次数据带偏。**节点级"整体有没有失败"若本源有该流程同事件的成功率列 → 直接用(A 类,阈值=100/按量纲),这是重述不是代理**;但**特定失败子因的计数器不在本源列里 → `decision:insufficient_data` 或换源,绝不拿通用率代理、绝不把多条判据塞给同一列**(语义错+恒成立+丢泛化)。判别看列**度量什么(建立 vs 掉线)**、别看名字。**绑率前机械自检:① 流程词逐字对上(UECntx/RRC/NGSig/QosFlow 不互代);② 计数器不含 `.FailEst.`(含=子因,禁率)。** 一次生成要能反复执行不同数据。
> **信令 / H 类先认消息词汇**:多页签信令源,先 `analyzer.py "<数据>" --sheet <带 MessageType 的页签(如 detailInfoRight)> --lean` 看 `MessageType`/`InterfaceType`/cause 的**真实取值**;`has`/`lacks`/`where` 只能绑**数据里真出现过**的消息名,**别照故障树正文猜**(猜错 → matched 恒 0 → 该路径假阴性、整源白填)。

### 步骤 3:用真实数据验证每个生成脚本
```bash
python "<OUT>/scripts/<ind>_analyzer.py" "<该指标数据>" -o /tmp/<ind>.json
python "<本目录>/scripts/validate_conditions.py" /tmp/<ind>.json
```
脚本要正常跑出逐条件事实(`indicator/source/summary/conditions`);校验器揪"没解析到列却给判断、数据不足缺理由",并机械逮代理三类:**`[流程不匹配]`(如 UEContext 条件绑了 RRC 率)/ `[建立配掉线]` / `[子因配总率]`(`.FailEst.` 子因绑了总成功率)**。**校验器报"不合格"= 那条多半是代理 → 按提示改成 `decision:insufficient_data` 或换列/换源、重跑,别带病往下走。** custom 写崩了/形状不对,在这步也暴露。

## 输出目录(就是 `<OUT>`)
```
<OUT>/                                       # = <故障树目录>/scot产物/<场景名>/
├── plan.json                              # SCoT 计划(产物①)
├── scripts/<ind>_analyzer.py              # 逐条件分析脚本(模型填 CONDITIONS,产物②)
├── scripts/condition_kernels.py           # A–J 模板库(脚本 import 它)
├── scripts/condition_binding_schema.md    # 绑定契约 + analyzer.py 概览引擎
└── tree/<场景>.md                          # 故障树 md(产物③)
```
跑它:`python "<cs-scot-execute 目录>/scripts/runner.py" --plan "<OUT>/plan.json" --data <ind>=<数据文件> ...`

## 注意
- 产物用**中文**;通用引擎 Python 3.11、自包含、精简忠实。
- 数据 xlsx/csv 都支持(xlsx 需 `pip install openpyxl`)。
- 路径有空格/括号/中文,命令里务必加引号。
