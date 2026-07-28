# 绑定手册——写给要自己看懂/改写绑定 JSON 的人

一条"绑定"就是机器判定一条判据的配方,JSON 写成。所有绑定共享同一条流水线:

```
source(在哪个数据源查)→ filter(选哪些行)→ column(看哪一格)
    → extract(可选:从格子里抽出一个值)→ 判定(class 决定怎么判)
```

产出是"事实":**成立 / 不成立 / 数据不足**。查不到=数据不足,绝不冒充"不成立";机器也绝不编造值。

全文结构:**第一节逐个介绍类别**(是什么、能用什么字段、最小例子)→ 第二节公共字段 → **第三节跨源绑定专章(去哪写、怎么写)** → 第四节常用套路(应用)→ 第五节速查表 → 第六节验证。

---

## 一、五个类别,逐个介绍

### A 类:数值阈值(比数字)

**什么时候用**:判据在比数——"超过 X / 出现 N 次以上 / 低于阈值"。

**字段**:`column` + `op`(< <= > >= == !=)+ `threshold`(数字,不带引号)+ `scope` 二选一:

- `"count_rows"`:**逐行**比,数满足的行数,可配 `require`(至少几行才算成立,默认 1)。判据是"存在越线/出现 N 次"就用它,**首选**;
- `"aggregate"`:全列**先聚合再比**,配 `agg`(mean/min/max/sum/first/last)。只在判"整体水平"时用;注意 mean 无加权。

可选:`filter`(选行)、`extract`(格内抽数,千分位如 `1,234` 自动归一)、`transforms`。

```json
{"node_id":"n9","class":"A","column":"CmdResult",
 "filter":{"column":"CmdName","contains":"display interface"},
 "extract":{"kv":"CRC error"},
 "op":">","threshold":100,"scope":"count_rows","require":1,
 "mapping_reason":"每行抽出 CRC 计数比阈值,至少1行越线即成立"}
```

**事实里能看到**:value/count_satisfied、op、threshold——人能复算。

### C / G 类:文本匹配(找文本)

**什么时候用**:判据在找字样或对取值——"出现某告警名 / 状态为 X / 配置了 Y / 未配置 Y"。
C 和 G 是**同一个内核的两个标签**(习惯:C=在枚举/取值列上对值,G=在大段文本里捞特征),字段完全一样,写哪个都行。

**字段**:`column` + 判定目标**三选一**(都是列表,**列表=或**,同义词写进一个列表):

- `equals`:整格(或 extract 抽出的值)等于其中之一;
- `contains`:格内包含其中之一;
- `regex`:正则命中其中之一(编译不过内核当场拒收)。

可选:`absent`(**反转极性:命中 0 行=成立**——"未配置/缺失"类判据必加;列全空时自动改判数据不足,不会误报)、`require`(至少命中几行)、`filter`、`extract`、`case_insensitive`。

```json
{"node_id":"n6","class":"G","column":"CmdResult",
 "filter":{"column":"CmdName","contains":"display alarm"},
 "contains":["hwBaseSocAttackTrap"],
 "mapping_reason":"告警名只在发生时出现,filter 选中该命令的回显行再找"}
```

```json
{"node_id":"n15","class":"C","column":"level1",
 "contains":["stp v-stp enable"],"absent":true,
 "mapping_reason":"判据=未配置该命令;absent=命中0行才成立(缺行即证据)"}
```

**事实里能看到**:matched/total(命中几行/共几行)、hits(命中原文片段)。

### D 类:组合(拼逻辑)

**什么时候用**:一条判据有多个要件——"配了 A **且**没配 B"、"X **或** Y"。

**字段**:`combine`("AND"/"OR")+ `sub`(子绑定列表——**每个 sub 自身就是一条完整绑定**,可以各带自己的 `source`,这就是跨源组合的写法)。

可选:`group_by`(按对象列切组,**每组单独跑全部 sub,存在一个组全部满足才成立**——防"整列里 A 也有 B 也有,但不在同一个接口上"的假命中;group_by 的 sub 不得跨源)。

```json
{"node_id":"n16","class":"D","combine":"AND","group_by":"level1","sub":[
   {"class":"G","column":"level2","contains":["mode lacp-static"]},
   {"class":"G","column":"level2","contains":["stp disable"],"absent":true}],
 "mapping_reason":"按接口(level1)切组:某接口同时'配了lacp-static'且'没配stp disable'才算"}
```

**事实里能看到**:sub_facts(每个子判定各自留痕),组合怎么得出的一目了然。

### P 类:成对比较(比两侧)

**什么时候用**:本端/对端(或两处配置)一致性——"两侧 SystemID 是否一致"。

**形态一(同一张表里分侧)**:`side_column`(分侧列,带 `*` 标记的=本端,标记可用 `peer_marker` 改)+ `value_column`(取值列)+ `fault_when`("not_equal"=不一致即故障)。

```json
{"node_id":"n25","class":"P","side_column":"Object","value_column":"DataValue",
 "filter":{"column":"DataItem","equals":"MLAG_SystemID"},
 "fault_when":"not_equal",
 "mapping_reason":"带*行=本端,比两侧 SystemID"}
```

**形态二(两侧在不同命令/不同源)**:`pair_from`——local/peer 各是一个"取值子句"(见第二节),各自选行抽值后再比:

```json
{"node_id":"n17","class":"P","source":"config","fault_when":"not_equal","pair_from":{
   "local":{"source":"config","column":"level1",
            "filter":{"column":"level1","contains":"stp bridge-address"},
            "extract":{"regex":"stp bridge-address ([0-9a-fA-F-]+)"}},
   "peer":{"source":"cmd","column":"CmdResult",
           "filter":{"column":"CmdName","contains":"display stp v-stp"},
           "extract":{"regex":"Active=([0-9a-fA-F-]+)"}}},
 "mapping_reason":"config 配的桥MAC 必须等于回显里的 Active 值,不等=故障"}
```

**诚实规则**:查不到对端=**数据不足**,不是"不成立"。事实里能看到 local/peer 两侧的值。

### J 类与 decision:不判定的两种诚实姿势

- **J**:纯人工检查项,只带 `note`(机器不判,提醒人看);
- **decision**(不带 class):
  - `"insufficient_data"`:**确属本源该答、但答不了**(数据里没这信息),必须配 `mapping_reason` 写清缺什么;
  - `"not_applicable"`:该判据不适用本场景;
  - `"cross_source"`:生成期专用标记——"单靠任何一个源都答不完",会汇入跨源补绑环节,不进本源脚本。

```json
{"node_id":"n19","decision":"insufficient_data",
 "mapping_reason":"判据要芯片款型枚举,本场景各源均未采集该信息"}
```

**红线**:"这次样例数据里恰好没出现"≠答不了——绑定写的是**规则**,值没出现运行时自然 matched=0,别把样例观察写死进 decision。

---

## 二、公共字段:所有类别通用的四件套

| 字段 | 干什么 | 要点 |
|---|---|---|
| `source` | 在哪个数据源查 | 单源脚本里可省(自动=本源);跨源绑定必写**真实源名**(没有叫 cross 的源) |
| `filter` | **选行** | `{"column":…, "equals"/"contains"/"excludes":…}`;选行不下结论 |
| `column` | 判定/取值落在哪一列 | 裸列名即可,大小写/近似自动解析 |
| `extract` | **从格子里抽出一个值** | `{"kv":"键名"}` 抓「键: 值/键=值」;`{"regex":"带捕获组"}` 抓任意格式;省略=整格就是值 |

**三个"看文本"的字段别搞混**:filter=选行(内部可用 contains);判定目标 contains/equals/regex=下结论;extract=抽值。

**"回显是一大坨"的三种情形**:

1. 问"一坨里**有没有** X" → 判定问题,直接 `contains`,不用 extract;
2. 要"从一坨里**拿出 X 的值**" → `extract`(kv 抓不到就 regex,任意格式都能锚);
3. 乱到正则都写不出 → `decision:"insufficient_data"`,别硬凑(硬凑=假阳)。

**取值子句**(pair_from 的 local/peer、param_from 的参数)只认 `source/column/filter/extract` 四个键,拼错会被响亮拒收;它的任务是"拿到值",所以没有 contains——要判"有没有"回到 C/G。

`mapping_reason`:每条绑定都写一句人话"为什么这么绑"——给复核的人和 RCA 看,极性有歧义时尤其要写明。

---

## 三、跨源绑定专章(难点:去哪写、怎么写)

### 什么算跨源

**单靠任何一个源都答不完**的判据:要么"先查 A 源拿到值、再拿这个值去 B 源查"(接力),要么"两个源各取一个值比对"(一致性)。注意边界:"这信息在别的源、本源答不了"**不算跨源**——那是让对的源自己去绑,别标跨源。

### 去哪写

跨源绑定统一放在 **`scripts/cross_analyzer.py` 的 `CONDITIONS` 列表**里——**不在调优表里**(调优表只有各单源的绑定列;打包时会打印"以下节点另有跨源绑定,工程侧维护"提醒你哪些节点要去那儿看)。它有两条产生途径:

- **生成期自动**:单源绑定发现跨源判据 → 写 `decision:"cross_source"` 标记 → merge 汇总候选 → `make_dispatch --cross` 生成补绑简报 → 子代理写好 → merge 合并进 cross_analyzer;
- **人工直改**:直接编辑 cross_analyzer.py 的 CONDITIONS(条目格式与本手册完全相同),改完单跑验证——**要把用到的所有源都喂上**:

```bash
python "<产物>/scripts/cross_analyzer.py" -o out.json --data cmd="<cmd文件>" --data config="<config文件>" --data diag="<diag文件>"
```

### 怎么写——三条铁规

1. **顶层必带 `"source": "<最终查询落点的真实源名>"`**(如 config)——**没有叫 cross 的数据源**,写 `"source":"cross"` 运行时直接判数据不足并报错纠正;
2. 内部**每个取值子句/每个 sub 各写各的 source**;
3. 运行时 runner 把全部数据源都喂给 cross_analyzer,内核按各处 source 从对应的表取数,把链条串起来——你只声明"去哪拿什么",串接是内核的事。

### 三种形态(可嵌套混用)

**形态 1|param_from 接力**("先查 A 拿值,代入 B 再查",完整例子见套路 7):先按取值子句抽参数,`"@参数名"` 整串代入主查询(filter 值、contains/equals/regex 目标里都可用)再执行。参数按声明顺序解析、后面的可引用前面的=多跳;默认要求唯一值(多值=歧义→数据不足);"每个 X 都查"加 `"expand":"all"/"any"`。

**形态 2|pair_from 两侧比对**(两侧在不同命令/不同源,完整例子见第一节 P 类形态二):local/peer 各自"选行→取列→抽值",再按 `fault_when` 比。

**形态 3|D 跨源组合**(多要件分散在多个源):sub 各带 source,AND/OR 由内核合;还能把前两种形态嵌进 sub。判据"①配置有 v-stp ②回显含根桥标记 ③配置有桥MAC 且 ④等于回显 Active 值"(故障=①②成立且(③缺失 或 ③≠④))的完整写法:

```json
{"node_id":"n17","class":"D","source":"config","combine":"AND","sub":[
   {"class":"G","source":"config","column":"level1","contains":["stp v-stp enable"]},
   {"class":"G","source":"cmd","column":"CmdResult",
    "filter":{"column":"CmdName","contains":"display stp"},"contains":["This bridge is the root"]},
   {"class":"D","combine":"OR","source":"config","sub":[
      {"class":"G","source":"config","column":"level1","contains":["stp bridge-address"],"absent":true},
      {"class":"P","source":"config","fault_when":"not_equal","pair_from":{
         "local":{"source":"config","column":"level1",
                  "filter":{"column":"level1","contains":"stp bridge-address"},
                  "extract":{"regex":"stp bridge-address ([0-9a-fA-F-]+)"}},
         "peer":{"source":"cmd","column":"CmdResult",
                 "filter":{"column":"CmdName","contains":"display stp v-stp"},
                 "extract":{"regex":"Active=([0-9a-fA-F-]+)"}}}}]}],
 "mapping_reason":"四要件AND:config有v-stp + 回显含根桥标记 +(config无bridge-address【absent】或 config桥MAC≠回显Active【P跨源比对】)"}
```

**限制与诚实行为**:`group_by` 的 D 不得跨源(不同源的行没有对齐关系);参数抽不到/歧义、对端查不到、某个源没喂 → 一律**数据不足**并写明卡在哪一跳,不会硬判也不会装死。

---

## 四、常用套路(应用篇)

前两节是"是什么",这节是"怎么组合着用"。

**套路 1|存在即证据**:G + filter 选命令行 + contains 告警名(第一节 C/G 例一)。
常见错法:把命令名当故障信号绑在 CmdName——健康设备同样执行巡检命令,必假阳;命令名只配当 filter。

**套路 2|负存在("未配置 X")**:C/G + `absent:true`(第一节 C/G 例二)。极性红线:判据说"未配置/缺失/没有",必加 absent,否则方向反了。

**套路 3|"配了 A 没配 B"且同一接口**:D AND + [存在子句, absent 子句] + `group_by` 接口列(第一节 D 例)。

**套路 4|回显计数器比阈值**:A + filter + extract 抽数 + op/threshold(第一节 A 例)。
常见错法:用 G 去 contains "CRC:" 这种恒在标签——每台设备都有这行,必假阳;计数器就该抽出来比数。

**套路 5|状态字段判值(最常翻车的极性场景)**:判据「Causation 字段不为 "-" 则异常」——

```json
{"node_id":"n3","class":"C","column":"CmdResult",
 "filter":{"column":"CmdName","contains":"display dfs-group 1 m-lag"},
 "extract":{"kv":"Causation"},
 "equals":["-"],"absent":true,
 "mapping_reason":"抽出 Causation 的值;equals['-']+absent=没有任何一行值为'-'才成立(出现非'-'值=异常)"}
```

常见错法:`"contains":["-"]` 在整坨文本里裸捞单字符——'-' 到处都是,必假阳。**判"某字段的值"要先 extract 抽值再 equals。**

**套路 6|两侧一致**:P(同表用 side_column,跨命令/跨源用 pair_from,第一节 P 两例)。

**套路 7|先查 A 拿值、代入 B 再查(传参链,写在 cross_analyzer)**:

```json
{"node_id":"n4","class":"G","source":"config","column":"level2",
 "contains":["mode lacp-static"],"absent":true,
 "filter":{"column":"level1","equals":"@plink"},
 "param_from":{"plink":{"source":"cmd","column":"CmdResult",
               "filter":{"column":"CmdName","contains":"display dfs-group 1 peer-link"},
               "extract":{"kv":"Port Name"}}},
 "mapping_reason":"第1跳:回显抽 peer-link 口名;第2跳:该接口配置块缺 mode lacp-static 即故障"}
```

机制:先按取值子句解析参数(如抽出 Eth-Trunk0),把 `"@plink"` **整串代入**主查询(filter 值/判定目标里都可用)再执行。参数按声明顺序解析、后面的可引用前面的=多跳;默认要求**唯一值**(抽出多个=歧义→数据不足);判据要"**每个** X 都查"才加 `"expand":"all"`(全部满足)或 `"any"`(任一满足)。参数抽不到/对端查不到=数据不足,机器自动如实报。

**套路 8|真判不了**:decision:insufficient_data + mapping_reason 写清缺什么(第一节 decision 例)。这是诚实,不是失败。

---

## 五、字段词典速查

| 字段 | 属于 | 含义 |
|---|---|---|
| `source` | 通用 | 数据源名;跨源绑定顶层写最终查询落点的真实源名 |
| `filter` | 通用 | 选行:equals/contains/excludes |
| `column` | 通用 | 判定/取值列 |
| `extract` | 通用 | 格内抽值:kv / regex(捕获组);省略=整格 |
| `equals`/`contains`/`regex` | C/G | 判定目标,列表=或 |
| `absent` | C/G | 命中 0 行=成立(负存在);列全空自动改判数据不足 |
| `require` | C/G、A(count_rows) | 至少命中/满足几行 |
| `case_insensitive` | C/G | 忽略大小写 |
| `op` / `threshold` | A | 比较符与阈值 |
| `scope` / `agg` | A | count_rows(逐行,首选)/ aggregate(聚合,配 agg) |
| `transforms` | A | 数值变换(少用) |
| `combine` / `sub` | D | AND/OR + 子绑定列表(sub 可各带 source=跨源) |
| `group_by` | D | 按对象列切组逐组判;不得跨源 |
| `side_column` / `peer_marker` / `value_column` / `fault_when` | P | 同表分侧比对 |
| `pair_from` | P | 跨表/跨源两侧取值比对 |
| `param_from` / `expand` | 跨源 | 传参链与逐值展开 |
| `note` | J | 人工检查项说明 |
| `decision` | 通用 | insufficient_data / not_applicable / cross_source(生成期) |
| `mapping_reason` | 通用 | 人话写清"为什么这么绑" |

---

## 六、改完怎么验证(三步,秒级)

```bash
# 调优表改完 → 机械编译回产物(校验不过一个文件都不写,按行号报错)
python "<产物或技能assets>/scripts/import_tree_excel.py" --out "<产物目录或 assets>"
# 单跑受影响的源(秒级,看该判据的事实对不对)
python "<产物>/scripts/<源>_analyzer.py" "<该源数据文件>" -o out.json
# 体检绑定质量(泛词/极性/一值多挂等警告,逐条看)
python "<generate技能>/scripts/validate_conditions.py" out.json
```

完整契约见 `scripts/condition_binding_schema.md`(字段全集与极性铁律,以它为准)。
