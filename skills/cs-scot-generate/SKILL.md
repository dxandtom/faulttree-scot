---
name: cs-scot-generate
description: 把故障树 Excel 与各指标的数据编译成场景诊断技能：为每个指标生成逐条件分析脚本、生成 SCoT 执行计划 plan.json 和故障树 Markdown，并打包成可上传的场景技能。当用户给出故障树表格并要求生成某个场景（如某接入失败场景）的诊断技能或诊断产物、要求把判据绑定到数据列、或提到 plan.json、analyzer 脚本时使用。指标种类由用户数据决定，不固定。
---

# cs-scot-generate：故障诊断技能生成

## 任务

输入一棵故障树（Excel）和若干指标的数据，输出该场景的诊断技能。技能目录里的产物有三样：

1. 各指标的逐条件分析脚本 `scripts/<指标>_analyzer.py`（同一个通用引擎的副本，只是填写的条件不同）；
2. SCoT 执行计划 `plan.json`；
3. 故障树 Markdown `tree/<场景>.md` 与权威拓扑 `tree/<场景>.tree_index.json`。

生成分 6 步：步骤 1 建树并搭好技能目录 → 步骤 2 组装产物目录 → 步骤 2.5 把判据绑定到数据列 → 步骤 3 用真实数据验证 → 步骤 4 清理生成期的临时文件 → 步骤 5 仅在步骤 1 没能直接生成技能目录时单独打包。步骤 1 默认直接生成技能目录（`scot技能/<英文短名>/`，产物在它的 `assets/` 下），清理完技能即成品，把技能目录的完整路径报给用户。

生成的产物是数据和资产，交给执行技能 cs-scot-execute 去跑，本身不是可执行程序。

## 术语

| 术语 | 含义 |
|---|---|
| 故障树 | 业务专家维护的 Excel 表，每一行是一个故障节点，带层级、判断条件、数据来源 |
| 场景 | 故障树里的一棵子树，通常是 L2 层的一个节点及其全部下级；一个场景生成一个技能 |
| 判据 | 故障树节点上的判断条件原文，如"接口状态为 Down" |
| 指标、数据源 | 一种输入数据，如 KPI 表、配置表、日志表；每种指标一个分析脚本。两个词在本文里同义 |
| 列 | 数据表里的一列；判据要落到具体的列上才能由脚本判断 |
| 绑定 | 把一条判据对应到某个数据源的某一列和某种判断类别的过程与结果 |
| 判断类别 | 六类：A 阈值比较、C 字段匹配、G 日志匹配、D 多条件组合、J 人工确认、P 成对比较；另有 S 类，指由 cs-scot-migrate 收入的节点判断脚本 |
| `CONDITIONS` | 分析脚本里存放全部绑定的列表，步骤 2.5 填写 |
| `node_id` | 树节点的短编号（`n1`、`n2`……），在 `tree_index.json` 里定义，绑定和根因分析都靠它对应节点 |
| 产物、`<OUT>` | 生成出来的全部文件所在的目录；技能形态下就是技能目录的 `assets/` |
| 场景技能 | 可以上传使用的技能目录：执行指引 SKILL.md、执行脚本、`assets/` 下的产物 |
| 概览 | `analyzer.py` 对一份数据统计出的列名、取值范围、类别取值，供填写绑定时参考 |
| 任务说明 | `make_dispatch.py` 为每个子代理生成的文件，装着一批判据、该数据源的概览和绑定规约 |
| 子代理 | 主会话派出去并行做绑定的代理，一份任务说明派一个 |
| 覆盖度 | 树上应该绑定的叶子节点里，已经被某个数据源绑定的比例 |
| 根因分析 | 执行阶段最后一步，由 fault-tree-rca 根据各脚本输出的事实判定根因 |

## 执行规矩

这些规矩来自实际运行中最费时的错误：命令失败后自行寻找替代做法。

- 全部动作只有两种：运行本技能 `scripts/` 下的脚本，读产物文件。不要用 `python -c` 临时解析 JSON 或 CSV（Windows 上中文路径经 shell 传递会乱码），要看数据一律用概览工具 `analyzer.py`。
- 同一条命令失败，原样重试至多 1 次；再失败按脚本打印的修法处理；脚本没给修法就把报错原文报给用户并停下。不要换写法自行绕过，不要绕开脚本另写替代方案。
- 所有中间文件都放在产物目录 `<OUT>` 之内，不要用 /tmp（Windows 上 /tmp 在工作区之外，读写会被拒绝或出现编码错误）。
- 每一步以该步脚本末尾的打印为完成标志，完成即进入下一步，不重复已完成的步骤。
- 下文命令里的 `scripts/xxx.py`、`references/xxx.md` 都指本文件同目录下的文件（通常在 `~/.claude/skills/cs-scot-generate/` 下），用绝对路径调用。
- 输入路径可能带空格、括号、中文（如 `EnhancedExcelDataExport (7).xlsx`），命令里整条路径必须加引号。

## 原理

故障树的判断条件按上表六类模板处理；另有三个通用能力：`filter` 筛选行、`extract` 从单元格里截取一段、`regex` 正则匹配。统计逻辑已经写成经过测试的内核库 `scripts/condition_kernels.py`。生成时要做的，是按真实数据为每个指标填写一份分析脚本：读表 → 逐条件统计 → 输出事实。

- 默认直接套用模板（`evaluate_binding`：选类别、指定列、填参数；条件名前缀 `Common:` 会自动去掉）；
- 只有数据格式特殊、模板不够用时，才为个别条件写 `custom` 函数。步骤 3 会用真实数据运行它，写错会当场暴露。

这样生成阶段产出"读表加统计"的代码，执行阶段只按顺序跑脚本（快），最后做一次根因分析（慢）。条件是否成立、路径回溯、跨源组合，交给 fault-tree-rca。类别、字段、参数的精确定义见 `references/condition_binding_schema.md`。

## 输入

- 故障树 `.xlsx`（绝对路径）。
- 场景名（作为 `--start-node` 截取子树，并给产物命名）。
- 每种指标一份数据，真实数据或样例都可以，用于验证。

### 输入是原始日志文件（txt/log）时，先转换成表

特征：输入是 zip 包，或目录树里任意深度存在 `CONFIG`、`CMD`（或 `DIAG`）、`LOG` 目录，里面是 `.txt`/`.log` 的命令回显。嵌套几层都算（`xxx/xxx/网元/CMD`、`网元/CMD`、根目录下直接 `CMD`，类型目录里再按日期分子目录也可以），转换脚本会递归查找，不要因为多包了一层就判定为不认识。

概览和绑定只接受带表头的表（csv/xlsx）。txt 不是表，直接当数据喂进去，全部判据都会报"找不到列"。先用 zip2csv 转成表再进入流程：

```bash
python "<zip2csv.py 路径>" <zip或目录> -o <输出目录>
```

- zip2csv 的查找次序：先找本技能目录内的 `zip2csv/scripts/zip2csv.py`（发行包形态内嵌），没有再用同一 skills 目录下独立安装的 `zip2csv/scripts/zip2csv.py`。zip 和目录都可以直接作为输入，LOG 目录下 `.txt` 和 `.log` 都能识别。
- 散乱的原始文件（零散的 log/txt/rar、混杂的 zip，连分类目录都没有）也直接喂给 zip2csv：它会先调用内置的 `vendor/ifilter.py` 归类成 `formated_result/<网元>/<CMD|CONFIG|LOG>/`，再转表。ifilter 依赖第三方库，装不上时它会明确报错并给出替代做法，把报错原样给用户，不要强行继续。
- 转换结果在 `csv/<网元>/LOG|CONFIG|DIAG|CMD/*.csv`，按子目录名对应指标（log、config、cmd；DIAG 与 cmd 是同一类的新旧两种名字）。多个网元的同类 csv 合并成一张再作为该指标的数据：保留第一份的表头，其余文件逐个追加。
- 四类目录之外的自由格式 txt 转不了表，这类内容走 S 类节点判断脚本（由 cs-scot-migrate 从节点交付包收入）或 `custom` 解析。
- 原始目录本身要保留：执行时还要用 `--data raw=<原始目录>` 传给 runner，S 类脚本直接读原始文件。

## 产物目录 `<OUT>`

所有中间过程和最终产物都写进同一个目录 `<OUT>`，默认在故障树 xlsx 旁边：

```
<OUT> = <故障树 xlsx 所在目录>/scot技能/<英文短名>/assets/   ← 默认：步骤 1 直接搭好技能目录，产物在其 assets/ 下
<OUT> = <故障树 xlsx 所在目录>/scot产物/<场景名>/            ← 退化情形：取不到英文短名，或本机没有打包所需的技能（步骤 1 会明确提示）
```

- 步骤 1 的建树脚本会自动算出并打印 `<OUT>`（直接生成技能目录时还会打印技能目录路径）。后续步骤 2、2.5、3 全部使用同一个 `<OUT>`，不要另建临时目录。
- 要改位置：给步骤 1 或步骤 2 传 `--out-base "<根目录>"`，或给步骤 2 的 assemble 传 `--out "<目录>"` 直接指定。

打包所需的技能是 cs-scot-execute、fault-tree-rca 和 zip2csv（可选）。查找次序：先找本技能目录内与 SKILL.md 同层的同名目录（发行包形态把它们收在技能内），再找同一 skills 目录下的独立技能。

## 步骤 1：Excel → 场景子树（并确定 `<OUT>`）

```bash
python "<本目录>/scripts/excel_to_tree.py" "<故障树xlsx>" --start-node "<场景名>"
```

- 不带 `-o` 时，子树写到 `<OUT>/tree/<场景名>.md`，并打印"本场景产物目录：`<OUT>`"。记住这个 `<OUT>`，后续步骤都用它。
- 同时产出 `<OUT>/tree/<场景名>.tree_index.json`：权威拓扑，用 `Ln Code` 作稳定的 `node_id`，带父子关系、条件、AND/OR 提示和数据源。后面填 `node_id`、根因分析压缩上下文都依赖它。
- 高层节点不参与判定：L1、L2 带判据的节点和场景根节点自动标记 `pass_through`，不阻断路径、不参与绑定，判据保留供人阅读。原因是高层判据多为"场景分类、选路、参考下级节点"之类的说明，不是故障条件，绑成判定会把整棵树的确证卡住。确实要在高层判定时加 `--bind-top-levels`。
- 场景默认在 L2（`--start-level` 默认 2）。用户明确要求以 L3、L4 等更深层为起点时，加 `--start-level N`，从那一层查找 `--start-node` 并截取更小的子树；用户没提层级就用默认。`--exclude-pattern` 默认排除噪声节点 `萃取新增节点-.*`（只有老脚本有这个参数，v2 没有）。输出通常是几十 KB 的单场景子树。

### 两种 Excel 格式，选对脚本

- 老格式：单页签，层级列写成 `L1(Chinese)`，判断条件挤在一列 `Judgment Condition(Chinese)`。用上面的 `excel_to_tree.py`。
- 新格式：多页签，层级列是 `L1`…`L6`，判断条件按数据源分列（`CHR判断方法`、`X2 cause`、`RB日志判断方法`、`配置数据判断方法`、`DBG日志判断方法`、`信令判断方法`、`话统辅助判断方法` 等）。用 `excel_to_tree_v2.py`：

  ```bash
  python "<本目录>/scripts/excel_to_tree_v2.py" "<故障树xlsx>" --list                                   # 先列出可选的场景名（L2）及其所在页签；非树页、带 -old 的旧页自动跳过，要保留旧页加 --include-old
  python "<本目录>/scripts/excel_to_tree_v2.py" "<故障树xlsx>" --start-node "<场景名>" [--sheet "<页签名>"]   # 跨页签合并出该场景子树；--sheet 限定只用某一页
  ```

  v2 的输出与老脚本同形，差别有三点。一，文件和目录名用英文短名：自动取场景所在层的英文列，转小写并用连字符连接（如"三层业务问题"→ `layer-3-service-issues.md`），英文列缺失或未翻译时沿用中文名并提示，可用 `--slug <english-name>` 手工指定；中文场景名保留在 index 的 `scenario` 字段，后续步骤的 `--scenario` 仍传中文名。二，判断条件按 CHR、X2、RB、配置、DBG、信令、话统这些数据源标签写入节点的 `data_source`，供步骤 2.5 预先分派；没有按源分列的判据（中英文混排的老表里 `Judgment Condition` 单列）归"通用"标签，通用标签没有预分派，绑定时由模型读判据原文决定归哪个源。三，表里有 `Recommend Solution(Chinese/English)` 列时另产出 `tree/solutions.json`（节点到推荐解决方案的对照），它不进树、不进判据，只在根因分析报告根因时查出来附上。

- 怎么判断用哪个：先跑 v2 的 `--list`。能列出场景就是新格式，继续用 v2；老脚本报 `未找到 Ln(Chinese) 层级列`，说明把新格式喂给了老脚本，换 v2。`--sheet` 和 `--list` 只有 v2 有，老脚本传了会报 `unrecognized arguments`。
- 起始层级：`--start-node` 默认把场景名当 L2 查找。用户明确要求更深层时加 `--start-level N`，`--list` 会改为列出那一层的场景名，`--start-node` 也从那一层查找：

  ```bash
  python "<本目录>/scripts/excel_to_tree_v2.py" "<故障树xlsx>" --start-level 3 --list                 # 列出所有 L3 场景名，核对用户要的名字在不在
  python "<本目录>/scripts/excel_to_tree_v2.py" "<故障树xlsx>" --start-node "<L3场景名>" --start-level 3
  ```

  不加 `--start-level` 时，名字在 L2 找不到会自动到更深层匹配，所以直接给 L3 名字多数也能通。但名字可能跨层重复（L2、L3 都有同名节点），这时必须显式给 `--start-level`。用户没提层级就用 L2，不要自行下钻。

## 步骤 2：组装产物目录

```bash
python "<本目录>/scripts/assemble_scenario.py" \
  --scenario "<场景名>" --indicators <ind1>,<ind2> --out "<OUT>"
```

这一步做三件事：把公共脚本复制进 `<OUT>/scripts/`（内核库 `condition_kernels.py`、配置适配器 `mo_config.py`、概览工具 `analyzer.py`、绑定格式说明文档；这几个文件每次重跑都会刷新）；为每个指标写一份待填写的分析脚本 `<指标>_analyzer.py`（`CONDITIONS` 留空，步骤 2.5 填）；生成 `plan.json`（各 `<指标>_analyzer` 脚本步骤 → 根因分析步骤）。`--out` 用步骤 1 打印的 `<OUT>`。计划格式见 `references/plan_schema.md`。

**外挂知识分两级。** 知识条目回答"去哪查、怎么查、什么算正常"，每条一段，用 `### 标题` 开头，可选 `阶段:绑定/诊断` 和 `适用:标签`，是判读规则而不是事实：

- 技能级 `<本目录>/knowledge.md`：出厂知识，跨场景积累。首次组装时复制进产物 `tree/knowledge.md`，从第一次绑定就生效；技能级文件不存在时写一份空白模板。
- 产物级 `<OUT>/tree/knowledge.md`：交付后业务同事往这里追加；重跑 assemble 不会覆盖它。

步骤 2.5 的绑定任务说明和执行时的根因分析输入，都按"适用"标签与节点名、判据文本匹配后自动载入；标 `阶段:绑定` 的只进绑定任务说明，标 `阶段:诊断` 的只随根因分析下发，不标的两边都不载。改了产物级知识，诊断立即生效；要让绑定重新受益，重跑步骤 2.5。

- assemble 有两道检查，不通过退出码 1：`tree/` 下找不到步骤 1 的 `*.tree_index.json`，或 index 里的场景名、短名与 `--scenario` 对不上（步骤 1 没跑、名字不一致或目录错位；新旧两代产物并存时报歧义，退出码 2）；树上没有任何判据。报错里带排查指引，拦的是"步骤 1 没做好就往下绑"，不要绕过。
- 重跑等于重新编译：已存在的 `<指标>_analyzer.py` 默认会被当前版本的空白脚本覆盖（旧的先备份为 `.py.bak`），所以更新技能后直接重跑即可生效，不用手动删旧脚本，模型随后在步骤 2.5 重新填写。确实要保留上次填好的脚本，加 `--keep`。

## 步骤 2.5：把判据绑定到数据列（关键一步）

目标：为每个 `<指标>_analyzer.py` 填好 `CONDITIONS`。做法是把判据分成小批，交给多个子代理并行绑定，脚本负责生成任务说明和合并结果。六个动作按顺序做：

1. **看数据的列**，每个指标一份精简概览：

   ```bash
   python "<OUT>/scripts/analyzer.py" "<该指标数据>" --lean -o "<OUT>/overview/<ind>_overview.json"
   ```

   `--lean` 只留列名、最小值、最大值、均值和类别取值，够定阈值和写匹配值。多页签 xlsx 先不带 `--sheet` 看页签清单，再用 `--sheet <名> --lean` 看某一页。

2. **分派**，先决定每条判据进哪个数据源的批次，避免每条判据都发给所有数据源：

   ```bash
   python "<本目录>/scripts/make_dispatch.py" --out "<OUT>" --route
   ```

   派一个子代理按生成的分派任务说明判断每条判据可能归哪些源，结果写 `dispatch/route_batch<N>_result.json`。这一步不能跳过；确实要跳过时显式加 `--no-route`。

3. **生成绑定任务说明**，每个数据源按批切分（默认每批 12 条，`--batch-size` 可调）：

   ```bash
   python "<本目录>/scripts/make_dispatch.py" --out "<OUT>"
   ```

4. **派发**：一份任务说明派一个子代理，每次最多派 8 个，收完一批再派下一批。派发词一句话："你的全部任务和材料在 <任务说明路径>，用 Read 读完它，照其中规约执行，结果写到说明里指定的结果文件，最终回复只报'共 N 条，已写 <路径>'"。

5. **合并结果**，收到几批就合几批：

   ```bash
   python "<本目录>/scripts/merge_conditions.py" --out "<OUT>"
   ```

   脚本验收、去重，并把绑定写进各 `<指标>_analyzer.py`。缺批、坏批按脚本提示用同一份任务说明重派一次。

6. **跨源补绑**：合并脚本打印"跨源判据候选 N 条"时，跑 `make_dispatch.py --out "<OUT>" --cross` 生成补绑任务说明，派发和合并同上，结果写进 `cross_analyzer.py`。这一小步做完再进步骤 3。

派发的规矩、失败怎么处理、跨源补绑的细节，见 `references/binding_dispatch.md`。怎样才算把一条判据绑对（逐条判断而不是脚本批量生成、按树遍历全覆盖、多源多子条件都要绑、不用替代指标、先确认数据里的写法），见 `references/binding_rules.md`。填写 `CONDITIONS` 前先读这两份。

## 步骤 3：用真实数据验证每个生成的脚本

```bash
# 事实文件放 <OUT>/overview/ 下（生成期中间文件，步骤 4 一并清理）
python "<OUT>/scripts/<ind>_analyzer.py" "<该指标数据>" -o "<OUT>/overview/<ind>_facts.json"
python "<本目录>/scripts/validate_conditions.py" "<OUT>/overview/<ind>_facts.json"
# 叶子覆盖度检查（按树遍历，与数据取值无关）：列出没被任何绑定认领的叶子；漏绑（条件提到已提供的列却没绑）退出码 1
python "<本目录>/scripts/check_tree_coverage.py" "<OUT>/tree/<短名>.tree_index.json" \
  --analyzer "<OUT>/scripts/<ind>_analyzer.py" [--analyzer ...] [--facts "<OUT>/overview/<ind>_facts.json" ...]
# --columns 可省：缺省按 --analyzer 自动读 <OUT>/overview/<ind>_overview.json 的列名；显式给了以给的为准
```

三条命令分别回答三个问题：脚本能不能正常跑出逐条件事实（`indicator/source/summary/conditions`）；每条绑定有没有明显错误（没解析到列却给了判断、数据不足没写理由、用替代指标充当）；树上该绑的叶子是不是都绑了。用过跨源通道时，把 `scripts/cross_analyzer.py` 也传进 `--analyzer`，各源的 `--facts` 都传上。

通过标准：校验器报"不合格"的条目和 `❌ 漏绑` 都要处理完；改了哪个指标只重跑该指标，全部修完后三条命令全量再跑一遍。警告分"必须处理"和"记录即可"两级，怎么分、怎么处理见 `references/binding_rules.md` 第 8 节。

### 步骤 3.5（可选）：假阳性检验

有一份同格式的健康设备数据时，把各 analyzer 对它再跑一遍。健康数据上也成立的故障判据高度可疑，逐条复核；分支或开关类判据除外。细则见 `references/binding_rules.md` 第 9 节。没有健康数据就跳过。

## 步骤 4：清理生成期的临时文件

步骤 3 全部通过、产物定稿后，删掉只在绑定期有用的中间文件再交付或归档。执行侧从头到尾不用它们，而且概览和枚举里带着真实数据的统计与取值样本，不要随产物流出：

```bash
python -c "import shutil,glob,os,sys; O=sys.argv[1]; shutil.rmtree(O+'/overview', ignore_errors=True); shutil.rmtree(O+'/dispatch', ignore_errors=True); [os.remove(f) for p in ('/tree/conditions_all.json','/tree/*_conditions*.py','/scripts/*_analyzer.py.bak') for f in glob.glob(O+p)]" "<OUT>"
```

删的是：`overview/`（各源概览和枚举）、`dispatch/`（任务说明）、`tree/conditions_all.json`（判据清单）、`tree/*_conditions*.py`（子代理的结果文件）、`scripts/*.py.bak`（脚本备份）。还在迭代绑定就先不要删；删了也无妨，`analyzer.py` 还在 `scripts/` 里，要看概览一条命令就能重新生成。

## 输出目录（就是 `<OUT>`）

```
<OUT>/                                       # 默认 = <故障树目录>/scot技能/<短名>/assets/（退化情形 = scot产物/<场景名>/）
├── plan.json                              # SCoT 计划（产物①）
├── manifest.json                          # 产物标识：产物格式版本与生成时间（执行与评估启动时比对，旧产物只告警）
├── scripts/<ind>_analyzer.py              # 逐条件分析脚本（模型填 CONDITIONS，产物②）
├── scripts/condition_kernels.py           # 类别模板库（分析脚本 import 它）
├── scripts/mo_config.py                   # 配置拓扑适配器（MO 文本转表，以及 custom 原语）
├── scripts/analyzer.py                    # 概览工具（步骤 2.5 看列用）
├── overview/                              # 各源概览和枚举，仅绑定期用，含真实数据样本，步骤 4 删
├── dispatch/                              # 绑定任务说明（make_dispatch.py 生成），仅绑定期用，步骤 4 删
├── scripts/condition_binding_schema.md    # 绑定格式说明副本
├── tree/<短名>.md                          # 故障树 Markdown（产物③；短名 = 英文 slug，取不到英文时 = 中文场景名）
├── tree/<短名>.tree_index.json            # 权威拓扑：node_id、父子、判据、数据源（绑定与根因分析都依赖它）
└── tree/solutions.json                    # （可选）节点到推荐解决方案的对照：根因分析报根因时附上，不进裁决上下文
```

运行产物：`python "<cs-scot-execute 目录>/scripts/runner.py" --plan "<OUT>/plan.json" --data <ind>=<数据文件> ...`

## 步骤 5（仅退化情形）：单独打包成场景技能

步骤 1 已直接生成技能目录时跳过本步，清理完技能即成品，把技能目录报给用户。只有步骤 1 明确提示"只生成了产物目录"（取不到英文短名，或本机没有打包所需的技能）、或者要打包旧产物、在另一台机器上重新打包时才跑：

```bash
python "<本目录>/scripts/package_scenario.py" --out "<OUT>"    # 产出 <根目录>/scot技能/<slug>/ 技能目录（与 scot产物 平级；--dest 可改）
```

产出 = cs-scot-execute 的执行指引（SKILL.md 自动改名并按场景写 description）+ `assets/`（本产物 + fault-tree-rca 规则副本），自动排除 overview、dispatch、各轮 result-*、index.jsonl、__pycache__ 等临时文件。产物不包含辩论引擎：辩论复核是平台工具链层的能力（fault-tree-rca-debate 技能加执行侧 SCOT_DEBATE 开关），交付的场景技能只有标准根因分析。上传后用户一句 `/<slug> 数据在 <zip>，帮我诊断` 即可运行全程（默认约定见 cs-scot-execute SKILL.md 的"场景技能形态"一节）。产物没有英文短名时用 `--name` 指定。

## 步骤 6（人工调优）：调优表是树和绑定的可编辑事实源

打包时自动产出 `tree/<短名>.tuning.xlsx`：单页签，一行一个节点，含层级、判据、各源绑定 JSON、解决方案；绑定列的写法和类别模板见产物自带的 `scripts/condition_binding_schema.md`，既是机器读取的格式约定，人也读得懂，不另设第二份文档。人改完表后一条命令编译回产物，零模型参与：

```bash
python "<产物>/scripts/import_tree_excel.py" --out "<产物目录或场景技能的 assets/>"
```

自动更新 tree_index（`node_id` 只读不改，新行留空自动分配，历史编号不复用）、故障树 Markdown、solutions.json、各 `<指标>_analyzer.py` 的 CONDITIONS、plan.json（新增 `绑定:<新源>` 列会复制一份 analyzer 并接进计划）。先全量校验再写盘：JSON 损坏、编号重复、上级不符都按 Excel 行号报错，一个文件不动；成功后打印变更摘要。导出调优表之后，树与绑定的一切修改只在调优表上做，不要再手改脚本或 tree_index，否则会被下次导入覆盖；改完重新打包分发。重新导出：`python "<产物>/scripts/export_tree_excel.py" --out "<产物>"`。

## 完整例子

故障树 `D:\树\接入失败.xlsx`（新格式），场景"接入失败示例"，两种指标：话统表 `D:\数据\kpi.csv`、日志表 `D:\数据\log.csv`。

| 步骤 | 命令 | 看什么 |
|---|---|---|
| 1 | `excel_to_tree_v2.py "D:\树\接入失败.xlsx" --list` | 场景名"接入失败示例"在列表里 |
| 1 | `excel_to_tree_v2.py "D:\树\接入失败.xlsx" --start-node "接入失败示例"` | 打印技能目录 `D:\树\scot技能\access-failure-demo\` 和产物目录 `<OUT>`（其 `assets/`）；"带判据节点 N" 大于 0 |
| 2 | `assemble_scenario.py --scenario "接入失败示例" --indicators kpi,log --out "<OUT>"` | `<OUT>/scripts/` 下出现 `kpi_analyzer.py`、`log_analyzer.py`、`plan.json` |
| 2.5 | `analyzer.py "D:\数据\kpi.csv" --lean -o "<OUT>/overview/kpi_overview.json"`，log 同理 | 两份概览文件 |
| 2.5 | `make_dispatch.py --out "<OUT>" --route` → 派 1 个子代理 → `make_dispatch.py --out "<OUT>"` | `dispatch/` 下出现 `kpi_batch1.txt`、`log_batch1.txt` …… |
| 2.5 | 每份任务说明派一个子代理 → `merge_conditions.py --out "<OUT>"` | 打印每批合并条数；有"跨源判据候选"就再跑 `--cross` 一轮 |
| 3 | 两个 analyzer 各跑一遍 → `validate_conditions.py` → `check_tree_coverage.py` | 校验无"不合格"，覆盖度检查无 `❌ 漏绑`，退出码 0 |
| 4 | 清理命令 | `overview/`、`dispatch/` 消失 |
| 汇报 | 技能目录路径 + 覆盖度输出行 + 警告处理结果 | |

## 最终汇报

- 必带技能目录的完整路径，加一句"上传该目录即可用 `/<slug>` 一句话诊断"。
- 汇报里的数字必须当场查文件，不要凭记忆：覆盖度抄 `check_tree_coverage.py` 的输出行，分派统计抄 `make_dispatch.py` 的打印；校验警告的处理结果（修了哪些、保留了哪些、为什么）也要列出。长流程跑完后记忆常常是错的，数字对不上以文件为准，重数一遍。

## 注意

- 产物用中文；通用引擎要求 Python 3.11，自包含，代码精简、忠实于数据。
- 数据支持 xlsx 和 csv（读 xlsx 不依赖第三方库，内置 xlsx_reader；只有导出或修改调优表写 xlsx 才需要 openpyxl）。
- 路径有空格、括号、中文时，命令里务必加引号。
