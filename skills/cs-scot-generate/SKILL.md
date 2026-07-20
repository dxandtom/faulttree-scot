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
- 步骤 1 的 `excel_to_tree.py` 会**自动算出并打印** `<OUT>`;**后续步骤(2/2.5/3)全部用同一个 `<OUT>`**,不要另起临时目录。
- 改位置:给步骤 1 / 2 传 `--out-base "<根目录>"`(产物 = `<根目录>/scot产物/<场景名>`),或给步骤 2 的 assemble 传 `--out "<目录>"` 直接指定。

## 流水线

### 步骤 1:Excel → 该场景子树 md(并定位 `<OUT>`)
```bash
python "<本目录>/scripts/excel_to_tree.py" "<故障树xlsx>" --start-node "<场景名>"
```
- **不带 `-o`**:子树写到 `<OUT>/tree/<场景名>.md`,并打印「本场景产物目录: `<OUT>`」——**记住这个 `<OUT>`,后续步骤都用它**。
- 同时产 **`<OUT>/tree/<场景名>.tree_index.json`**:权威拓扑(用 `Ln Code` 当稳定 **node_id** + 父子 + 条件 + AND/OR 提示 + 数据源)。后面填 `node_id`、RCA 压缩上下文都靠它。
- 场景默认在 L2(`--start-level` 默认 2);**用户提示词明确要以 L3/L4 更深层为起点时**,加 `--start-level N`(老/新脚本同参),从那层锚定 `--start-node`、截更小子树;用户没提层级就默认 L2。`--exclude-pattern` 默认排除噪声节点 `萃取新增节点-.*`(**仅老脚本有此参**;v2 无、不排噪声节点)。出来应是几十 KB 的单场景子树。

> **⚠ 两种 Excel 格式,务必选对脚本(选错会报错):**
> - **老格式**(单页签、层级列写成 `L1(Chinese)`、判断挤在一列 `Judgment Condition(Chinese)`)→ 上面的 `excel_to_tree.py`。
> - **新格式**(**多页签**、层级列是纯 `L1`…`L6`、判断**按源分列** `CHR判断方法 / X2 cause / RB日志判断方法 / 配置数据判断方法 / DBG日志判断方法 / 信令判断方法 / 话统辅助判断方法 …`)→ 用 **`excel_to_tree_v2.py`**:
>   ```bash
>   python "<本目录>/scripts/excel_to_tree_v2.py" "<故障树xlsx>" --list                              # 先探:列出可输入场景名(L2)+ 各自出处页签(非树页/旧页 -old 自动跳过;要保留旧页加 --include-old)
>   python "<本目录>/scripts/excel_to_tree_v2.py" "<故障树xlsx>" --start-node "<场景名>" [--sheet "<页签名>"]   # 跨页签合并出该场景子树;--sheet 限定只用某一页
>   ```
>   输出契约与老脚本同形,但 **v2 的文件/目录名用英文短名(slug)**:自动取场景所在层的英文列转小写连字符(如「三层业务问题」→ `layer-3-service-issues.md`,平台显示友好),英文列缺失/未翻译则沿用中文名并提示(可 `--slug <english-name>` 手工指定);中文场景名保留在 index 的 `scenario`,后续步骤 `--scenario` 仍输中文名。判断按 `CHR/X2/RB/配置/DBG/信令/话统` **源标签**落进节点 `data_source`、给步骤 2.5 绑定**预路由**;判据没按源分列的(混血表的 `Judgment Condition` 单列)归 `通用` 标签——**通用无预路由**,路由靠绑定时模型读判据原文。表里有 `Recommend Solution(Chinese/English)` 列时另产 **`tree/solutions.json`**(节点→推荐解决方案;不进树不进判据,RCA 报根因时点查输出)。后续步骤照旧。
> - **怎么判断用哪个**:**先跑 v2 的 `--list`**——能列出场景=新格式、继续用 v2;若老脚本报 `未找到 Ln(Chinese) 层级列`,就是把新格式喂给了老脚本,换 v2。`--sheet`/`--list` 是 v2 独有,老脚本没有(传了会报 `unrecognized arguments`)。
> - **起始层级(默认 L2,可选 L3/L4…)**:`--start-node` 默认把场景名当 **L2** 找。**用户提示词里明确要以 L3/L4 等更深层级为起点时**,加 `--start-level N`——`--list` 会改列**那一层**的场景名,`--start-node` 也从那层锚定,截出更小的子树(v2 老脚本同参)。用法:
>   ```bash
>   python "<本目录>/scripts/excel_to_tree_v2.py" "<故障树xlsx>" --start-level 3 --list                 # 列出所有 L3 场景名(核对用户要的名字在不在)
>   python "<本目录>/scripts/excel_to_tree_v2.py" "<故障树xlsx>" --start-node "<L3场景名>" --start-level 3
>   ```
>   注:不加 `--start-level` 时,若给的名字在 L2 找不到,脚本会**自动在更深层兜底匹配**从该节点起截——所以"直接给 L3 名字"多数也能通;但名字可能跨层重名(L2、L3 都有同名节点)时,**务必显式 `--start-level`** 消歧,别靠兜底。用户没提层级 = 默认 L2,不要自作主张下钻。

### 步骤 2:归集产物(拷模板库 + 写待填脚本骨架 + 生成 SCoT 计划)
```bash
python "<本目录>/scripts/assemble_scenario.py" \
  --scenario "<场景名>" --indicators <ind1>,<ind2> --out "<OUT>"
```
它把 A–J 模板库 `condition_kernels.py`、配置适配器 `mo_config.py`、概览引擎 `analyzer.py`、绑定契约拷进 `<OUT>/scripts/`(**这几个库每次重跑都刷新**),为每个指标写一份**待填骨架** `<ind>_analyzer.py`,并产出 `plan.json`(`各<ind>_analyzer(script)→rca(agent_skill)`)。`--out` 用步骤 1 打印的 `<OUT>`。格式见 `references/plan_schema.md`。
> **assemble 有两道硬闸门(退出码 1)**:①`tree/` 下找不到步骤 1 产物 `*.tree_index.json`,或 index 里的场景名/短名与 `--scenario` 对不上(步骤 1 没跑/名字不一致/目录错位;新旧两代产物并存时报歧义、退出码 2);②树 0 判据。报错里带排查指引——拦的是"步骤 1 没跑好就往下绑"的病根,别绕。
> **重跑 = 重新编译**:已存在的 `<ind>_analyzer.py` 默认**会被当前版本的空骨架覆盖**(旧的先备份成 `.py.bak`),所以**更新 skill 后直接重跑就能生效,不用手删旧脚本**;模型随后在步骤 2.5 重新填。确需保留上次填好的脚本 → 加 `--keep`。

### 步骤 2.5(关键的一步):为每个 `<ind>_analyzer.py` 填 `CONDITIONS`
先看各源真实列,再把故障树每条判据填进对应源脚本:
```bash
python "<OUT>/scripts/analyzer.py" "<该指标数据>" --lean -o "<OUT>/overview/<ind>_overview.json"   # 精简概览:列名/量纲/类别取值(目录会自动建)
```
`--lean` 砍掉十分位+样例(填 CONDITIONS 用不到),只留**列名 / min·max·mean(定阈值/量纲)/ 类别取值(C 类匹配)**——读得快、上下文小、不影响正确性。多页签 xlsx:先不带 `--sheet` 看页签清单,再 `--sheet <名> --lean` 看某页详情。
按 `references/condition_binding_schema.md`,把判据**路由到对应源**、填进该源脚本的 `CONDITIONS`(选 A–J 类、对哪列〔裸名即可〕、阈值/变换;对不上标 `decision:insufficient_data`)。**每条都填 `node_id`**(取自 `tree_index.json` 里的短 id `nN`)——RCA 靠它把事实挂回树、并把上下文压成局部子树。**默认套模板,非改不可才写 `custom`**。跨源的组合各源分别出子事实,AND/OR 留给 RCA 合。
> **⚡ 提速:并行绑定,派发准备交给脚本,模型只管"派"和"收"**。先跑
> `python "<本目录>/scripts/make_dispatch.py" --out "<OUT>"` ——机械生成每个子代理的**自包含简报** `<OUT>/dispatch/<ind>_batch<N>.txt`:判据自动切批(≤50 条/批,防单次生成过长——回复截断和 600s 流挂是同一病的两副面孔)、该源概览/枚举**原文**自动嵌入(零转述零删减)、公共规约与 C/G·EAV 铁规固定文本;**EAV 源缺枚举会响亮拒产**并给出抽枚举命令,先补再跑。派发提示词一句话:「你的全部任务和材料在 <简报路径>,用 Read 读完它(可分段),照其中规约执行——结果写到简报指定的结果文件,最终回复只报『共 N 条,已写 <路径>』」(子代理**各写各的** `<本批>_result.py`,零竞态;绑定内容从此不过任何模型的上下文——实测走"回复→主代理转录→Edit 重打"三过上下文,撑爆主会话、写入代理流挂)——**一份简报派一个子代理**;简报多就**分波派**(一波 4~8 个,收完一波再下一波),**不许**为省事把多份简报合并给一个子代理(合并=拆批白做,截断/流挂回归),也别转述简报内容;子代理读不了文件的环境(权限没配)→ 主代理把简报全文读入后**原样**贴进派发词(简报已拼装好,纯复制)。简报/概览都住 `<OUT>` 圈内,严禁 `/tmp`(Windows 上=用户 Temp=圈外,后台子代理读写被拒)。
> **收货=跑合并脚本(幂等、先到先合,不必等全齐)**:`python "<本目录>/scripts/merge_conditions.py" --out "<OUT>"` ——机械完成验收(逐批 ast 可解析、无 U+FFFD 乱码,点名「C/G 缺 contains/equals」的野格式与缺 `node_id` 的条目)、按源合并去重(实测子代理会把同段重复两遍)、用 ast 定位骨架块把 CONDITIONS **直接写进** `<ind>_analyzer.py` 并做落盘后语法自检——模型不再手工 Edit,也不许自写替换脚本(实测孤儿 `]` 五连炸)。收到几批就能跑几批,**"谁先回来先写谁"天然成立**;脚本输出里的**缺批/坏批照单同简报重派一次**(流挂/截断多为偶发)→ 再失败,主会话自己按简报绑、把结果写成该批 `_result.py` 再跑合并。**明令禁止"取不到就编造占位"**(循环生成 `insufficient_data` 占位=踩量产红线+每个节点都被"认领"、覆盖度闸门对真漏绑集体失明,实测过);禁止转包单代理吞全量;子代理只写自己批次的结果文件,**不许**直改 `<ind>_analyzer.py`(多代理同改一个文件必竞态,合并必须走脚本串行)。
> **⛔ 绑定是逐条模型判断,禁止"写脚本量产绑定"**:每条绑定都必须是"读判据原文 → 语义映射到源/列/类 → 写实 `mapping_reason`"的判断产物。**不许**写关键词分类/正则匹配脚本把判据批量翻译成 CONDITIONS——那会绕过语义映射(代理/假阳没人把关)、`mapping_reason` 全是模板话不可审计;子代理失败也**不是**改走脚本的理由(直接主代理逐条填)。同理,**建树后先核对步骤1输出的「带判据节点 N」:N=0 → 停下报告**(几乎肯定是判据列没被识别的格式问题,assemble 也会硬拦),别自己读 Excel 造绑定。
> **⚠ 树驱动、要全覆盖(欠绑定是头号坑)**:**遍历 `tree_index` 里本源能答的每个叶子,逐个绑**——按树条件要的**列+值**绑(C 类 `contains` 那个码),**不管这次数据里出没出现**;别"翻数据里出现了哪些码、看到一个挂一个"(数据驱动 → 漏整支 + 过拟合本次数据)。值这次没出现 → 照绑、`matched=0`(确定排除),**不是** `insufficient_data`。`mapping_reason` 别只写"出现 N 次"(那是运行时结果,不是绑定依据)。
> **⚠ 多源都能答就都绑、不许谦让;多子条件要绑全**:各源子代理**互相看不见**——"这个大概日志侧会绑"的谦让=两边都没绑;本源能答就绑,多源同绑同一节点是**期望行为**(各出各的事实,主次让 RCA 合)。一个节点的判据常是**多个子条件都要满足**(换行/且/①② 分隔):把本源能答的**每个子条件**都绑上(单源多子条件用 D 组合;答不了的子条件显式 `decision:insufficient_data`),**不是碰到一条就算认领了这个节点**。步骤 3 的闸门会输出跨源认领对账,专逮这两种。**反过来也别灌水:明显不归本源答的判据整条不用写**(不写=不认领,让覆盖度闸门/对账把真漏绑显出来),`decision` 占位只给「确属本源该答、这次却答不了」的——对全树 158 条逐条表态两次实测都是灾难:撑爆回复被截断丢数据、每个节点都被"认领"把闸门糊瞎、"数据不足"口径失真。
> **⚠ 写进去后核对覆盖度(别只数条数)**:主代理写完,跑步骤3的 **`check_tree_coverage.py`** 机械核对——**`❌ 漏绑`>0(本源能答的叶子没绑)= 没写全,回去补,别往下跑**;**0 条或异常少**几乎一定是数据驱动/没收齐造成的欠绑定。
> **泛化第一(重要)**:绑定按**格式+语义**,别被这次数据带偏。**节点级"整体有没有失败"若本源有该流程同事件的成功率列 → 直接用(A 类,阈值=100/按量纲),这是重述不是代理**;但**特定失败子因的计数器不在本源列里 → `decision:insufficient_data` 或换源,绝不拿通用率代理、绝不把多条判据塞给同一列**(语义错+恒成立+丢泛化)。判别看列**度量什么(建立 vs 掉线)**、别看名字。**绑率前机械自检:① 流程词逐字对上(UECntx/RRC/NGSig/QosFlow 不互代);② 计数器不含 `.FailEst.`(含=子因,禁率)。** 一次生成要能反复执行不同数据。
> **信令 / H 类先认消息词汇**:多页签信令源,先 `analyzer.py "<数据>" --sheet <带 MessageType 的页签(如 detailInfoRight)> --lean -o "<OUT>/overview/<页签>_overview.json"` 看 `MessageType`/`InterfaceType`/cause 的**真实取值**;`has`/`lacks`/`where` 只能绑**数据里真出现过**的消息名,**别照故障树正文猜**(`has` 猜错内核守卫会改判数据不足并附真实词汇样例;`lacks`/`where` 猜错则**静默**退化——恒真/恒 0,该路径假阴性、整源白填)。

### 步骤 3:用真实数据验证每个生成脚本
```bash
python "<OUT>/scripts/<ind>_analyzer.py" "<该指标数据>" -o /tmp/<ind>.json
python "<本目录>/scripts/validate_conditions.py" /tmp/<ind>.json
# 叶子覆盖度闸门(树驱动,与数据取值无关):列出"没被任何绑定认领的叶子";漏绑(条件提到已喂列却没绑)→ 退出码 1
python "<本目录>/scripts/check_tree_coverage.py" "<OUT>/tree/<短名>.tree_index.json" \
  --facts /tmp/<ind>.json [--facts /tmp/<其他源>.json ...] --columns "<本源列名,逗号分隔(从概览取)>"
```
脚本要正常跑出逐条件事实(`indicator/source/summary/conditions`);校验器揪"没解析到列却给判断、数据不足缺理由",并机械逮代理三类:**`[流程不匹配]`(如 UEContext 条件绑了 RRC 率)/ `[建立配掉线]` / `[子因配总率]`(`.FailEst.` 子因绑了总成功率)**。**校验器报"不合格"= 那条多半是代理 → 按提示改成 `decision:insufficient_data` 或换列/换源、重跑,别带病往下走。** custom 写崩了/形状不对,在这步也暴露。**`validate_conditions` 的『覆盖度告警』是数据值级**(绑了的列里哪些取值没被认领)。
> **`check_tree_coverage.py` 是树驱动的叶子覆盖度闸门**——它从 `tree_index` 枚举每个该绑的叶子,把**没被任何源绑定认领的叶子全列出来**,即便该叶子的码这次数据里没出现(这是值级告警**逮不到**的盲区:没绑就没列、没列就隐形)。**`❌ 漏绑`(叶子条件正文提到本次已喂列、本源能答却没绑)= 退出码 1,必须回去补绑(没出现就让它 `matched=0`)或显式 `decision:insufficient_data`**;`整支未触达/缺源` 逐支确认是真没喂对应数据(信令跟踪/配置文件没传)还是漏判一整支,后者归"非典型/其他"兜底或补绑。**跨源时把各源 `--facts` 都传上,合并判覆盖**——此时还会输出**跨源认领对账**(软提示):各源认领分布、"判据像多个子条件却只有 1 条绑定"、"告警类判据多源在场却单源认领",逐个过一眼,别让"碰了一条就算绑完"和"互相谦让"漏网。

> **步骤 3.5(可选,有健康数据包时做——假阳的实锤检验)**:把各 analyzer 对一份**同格式的健康设备数据**再跑一遍。健康数据上 `status:ok` 且 `predicate_satisfied:true` 的绑定 = 正常样本也成立 = **高度可疑,逐条复核**(契约"通用尺子"的可执行版):故障语义的判据在健康包上成立基本就是假阳,改绑;但"是否启用某特性/某配置"这类**分支/开关判据**健康设备上本可成立,不算假阳。这是机械筛查,比任何软告警都硬。没有健康包就跳过。

### 步骤 4(交付前清场):删掉只在生成期有用的中间件
步骤 3 全绿、产物定稿后,把**绑定期中间件**删掉再交付/归档——执行侧(runner/RCA)从头到尾不碰它们,而且**概览/枚举里带着真实数据的统计与取值样本**(交付脱敏隐患),别让它们跟着产物流出去:
```bash
python -c "import shutil,glob,os,sys; O=sys.argv[1]; shutil.rmtree(O+'/overview', ignore_errors=True); shutil.rmtree(O+'/dispatch', ignore_errors=True); [os.remove(f) for p in ('/tree/conditions_all.json','/tree/*_conditions*.py','/scripts/*_analyzer.py.bak') for f in glob.glob(O+p)]" "<OUT>"
```
删的是:`overview/`(各源概览+枚举)、`dispatch/`(派发简报)、`tree/conditions_all.json`(判据清单)、`tree/*_conditions*.py`(子代理交货文件)、`scripts/*.py.bak`(骨架备份)。**还在迭代绑定就别删**;删了也不心疼——`analyzer.py` 引擎在 `scripts/` 里,想再看概览一条命令重生成。

## 输出目录(就是 `<OUT>`)
```
<OUT>/                                       # = <故障树目录>/scot产物/<短名>/(短名=英文 slug,取不到英文时=中文场景名)
├── plan.json                              # SCoT 计划(产物①)
├── manifest.json                          # 产物铭牌:格式契约版本+生成时间(execute/benchmark 启动比对,旧产物只告警)
├── scripts/<ind>_analyzer.py              # 逐条件分析脚本(模型填 CONDITIONS,产物②)
├── scripts/condition_kernels.py           # A–J 模板库(脚本 import 它)
├── scripts/mo_config.py                   # 配置拓扑适配器(MO 文本→表 + custom 原语)
├── scripts/analyzer.py                    # 概览引擎(步骤 2.5 模型看列用)
├── overview/                              # 各源概览/枚举——仅绑定期用,含真实数据样本,步骤 4 交付前删
├── dispatch/                              # 派发简报(make_dispatch.py 生成)——仅绑定期用,步骤 4 交付前删
├── scripts/condition_binding_schema.md    # 绑定契约副本
├── tree/<短名>.md                          # 故障树 md(产物③;短名=英文 slug,取不到英文时=中文场景名)
├── tree/<短名>.tree_index.json            # 权威拓扑:node_id/父子/判据/数据源(绑定与 RCA 压缩都靠它)
└── tree/solutions.json                    # (可选)节点→推荐解决方案:RCA 报根因时点查,不进裁决上下文
```
跑它:`python "<cs-scot-execute 目录>/scripts/runner.py" --plan "<OUT>/plan.json" --data <ind>=<数据文件> ...`

### 步骤 5(可选):打包成"一句话可用"的云端场景技能
要把该场景发布成独立技能(每场景一个,技能名=英文短名)时,一条命令出货:
```bash
python "<本目录>/scripts/package_scenario.py" --out "<OUT>"    # 产出 <根目录>/scot技能/<slug>/ 技能目录(与 scot产物 平级;--dest 可改)
```
产出 = execute 执行骨架(SKILL.md 自动改名+按场景写 description)+ `assets/`(本产物 + fault-tree-rca 规则副本),自动排除 overview/dispatch/runs/result/__pycache__ 等临时物。上传后用户一句 `/<slug> 数据在 <zip>,帮我诊断` 即可跑全程(默认约定见 execute SKILL.md「场景打包形态」)。产物没有英文短名时用 `--name` 指定。

### 步骤 6(人工调优循环):调优表——树+绑定的人可编辑事实源
打包时自动产出 `tree/<短名>.tuning.xlsx`(单页签,一行一节点:层级/判据/各源绑定 JSON/解决方案;
业务同事的动手说明在 `tree/tuning-guide.md`)。人改完表后一条命令**机械编译回产物**(零模型参与):
```bash
python "<产物>/scripts/import_tree_excel.py" --out "<产物目录或打包技能的 assets/>"
```
自动:tree_index(**node_id 只认不改**,新行留空自动分配、历史 id 不复用)/ tree md / solutions.json /
各 `<指标>_analyzer.py` 的 CONDITIONS / plan.json(新增 `绑定:<新源>` 列会克隆 analyzer 并接进计划)。
先全量校验后写盘(JSON 坏/id 重复/上级不符 → 按 Excel 行号人话报错、一个文件不动),成功打印变更摘要。
**导出调优表之后,树与绑定的一切修改只在调优表上做**(别再手改脚本/tree_index,会被下次导入覆盖);
改完重打包分发。重新导出:`python "<产物>/scripts/export_tree_excel.py" --out "<产物>"`。

## 注意
- 产物用**中文**;通用引擎 Python 3.11、自包含、精简忠实。
- 数据 xlsx/csv 都支持(xlsx 需 `pip install openpyxl`)。
- 路径有空格/括号/中文,命令里务必加引号。
