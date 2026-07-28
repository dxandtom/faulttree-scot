# 绑定手册——写给要自己看懂/改写绑定 JSON 的人(带例子)

一条"绑定"就是机器判定一条判据的配方。所有绑定共享同一条流水线:

```
source(在哪个数据源查)→ filter(选哪些行)→ column(看哪一格)
    → extract(可选:从格子里抽出一个值)→ 判定(class 决定怎么判)
```

产出是"事实":**成立 / 不成立 / 数据不足**。查不到=数据不足,绝不冒充"不成立";机器也绝不编造值。

---

## 一、三个"看文本"的字段,分工别搞混(最常见的困惑)

| 字段 | 干什么 | 口诀 |
|---|---|---|
| `filter` | **选行**(在哪些行里干活),内部可用 equals/contains/excludes | 选行不下结论 |
| `contains`/`equals`/`regex`(判定目标) | **下结论**(命中没命中) | 下结论才用它 |
| `extract` | **从格子里抽出一个值**(kv 抓「键: 值」;regex 抓捕获组;省略=整格就是值) | 要"值"才抽,只问"有没有"不用抽 |

**回显是一大坨怎么办?**三种情形三条路:

1. "一坨里**有没有** X"(某字样/某告警名出现没出现)→ 判定问题,直接 `contains`,不用 extract;
2. "从一坨里**拿出 X 的值**来用"(当参数、两侧比对)→ `extract`;kv 格式抓不到就用 `extract:{"regex":"…(捕获组)…"}`,任意格式都能锚;
3. 乱到正则都写不出 → 老实写 `decision:"insufficient_data"`,别硬凑(硬凑=假阳)。

---

## 二、五个类别速记

**A 比数字;C/G 找文本;D 拼组合;P 比两侧;decision 认账。**

---

## 三、八个套路(每个带数据+绑定+机器怎么判)

### 套路 1:存在即证据(告警/事件字样出现=故障)

数据(cmd 表):

| CmdName | CmdResult |
|---|---|
| display alarm active | ……hwBaseSocAttackTrap…… |

```json
{"node_id":"n6","class":"G","column":"CmdResult",
 "filter":{"column":"CmdName","contains":"display alarm"},
 "contains":["hwBaseSocAttackTrap"],
 "mapping_reason":"告警名只在发生时出现,filter 选中该命令的回显行再找"}
```

机器:filter 选中 display alarm 的行 → 在 CmdResult 里找目标 → 命中≥1 行=成立,0 行=不成立。
**常见错法**:把命令名当故障信号绑在 CmdName 上——健康设备同样执行巡检命令,必假阳;命令名只配当 filter。

### 套路 2:负存在("未配置 X",缺行才是故障)

```json
{"node_id":"n18","class":"G","column":"level1",
 "contains":["stp v-stp enable"],"absent":true,
 "mapping_reason":"判据=未配置该命令;absent=命中0行才成立;列全空内核自动改判数据不足"}
```

**极性红线**:判据说"未配置/缺失/没有",必须加 `"absent":true`。不加就是方向反了("配了"反而报故障)。

### 套路 3:"配了 A 但没配 B",且要在同一接口上

```json
{"node_id":"n16","class":"D","combine":"AND","group_by":"level1","sub":[
   {"class":"G","column":"level2","contains":["mode lacp-static"]},
   {"class":"G","column":"level2","contains":["stp disable"],"absent":true}],
 "mapping_reason":"按接口(level1)切组,某接口同时满足两个子条件才算——防A、B各在不同接口的假命中"}
```

机器:按 `group_by` 列切组,每组单独跑全部 sub,存在一个组全部满足才成立。group_by 的 sub 不得跨源。

### 套路 4:回显里的计数器比阈值

数据格里是 `……CRC error: 1,234……` 这类杂糅文本:

```json
{"node_id":"n9","class":"A","column":"CmdResult",
 "filter":{"column":"CmdName","contains":"display interface"},
 "extract":{"kv":"CRC error"},
 "op":">","threshold":100,"scope":"count_rows","require":1,
 "mapping_reason":"抽出 CRC 计数逐行比阈值,至少1行越线即成立;千分位自动归一"}
```

**常见错法**:用 G 去 contains "CRC:" 这种恒在标签——每台设备都有这行,必假阳。计数器就该抽出来比数。

### 套路 5:状态字段判值(你们最常翻车的极性场景)

判据:「Causation 字段不为 "-" 则异常」。数据:回显里有 `Causation : PEERLINKDOWN`。

```json
{"node_id":"n3","class":"C","column":"CmdResult",
 "filter":{"column":"CmdName","contains":"display dfs-group 1 m-lag"},
 "extract":{"kv":"Causation"},
 "equals":["-"],"absent":true,
 "mapping_reason":"抽出 Causation 的值;equals['-']+absent=没有任何一行的值是'-'才成立(即出现了非'-'值=异常)"}
```

**常见错法**:`"contains":["-"]` 裸捞单字符——'-' 在整列文本里到处都是,必假阳。**要判"某字段的值",先 extract 抽值再 equals,别在整坨文本里捞。**

### 套路 6:两侧一致性(本端/对端)

同一张表里靠 `*` 标记分侧(如 diag 的 Object 列带 `*`=本端):

```json
{"node_id":"n25","class":"P","side_column":"Object","value_column":"DataValue",
 "filter":{"column":"DataItem","equals":"MLAG_SystemID"},
 "fault_when":"not_equal",
 "mapping_reason":"带*行=本端,比两侧 SystemID;查不到对端=数据不足,不是不成立"}
```

两侧在**不同源/不同命令**里 → `pair_from`,两侧各自"选行→取列→抽值"再比:

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

### 套路 7:先查 A 拿到值、代入 B 再查(传参链,写在 cross)

```json
{"node_id":"n4","class":"G","source":"config","column":"level2",
 "contains":["mode lacp-static"],"absent":true,
 "filter":{"column":"level1","equals":"@plink"},
 "param_from":{"plink":{"source":"cmd","column":"CmdResult",
               "filter":{"column":"CmdName","contains":"display dfs-group 1 peer-link"},
               "extract":{"kv":"Port Name"}}},
 "mapping_reason":"第1跳:回显抽 peer-link 口名;第2跳:查该接口配置块缺 mode lacp-static 即故障"}
```

机器:先解析 param_from(抽出如 Eth-Trunk0),把 `"@plink"` 整串代入主查询再执行。参数默认要求唯一值(多值=歧义→数据不足);判据要"每个 X 都查"才加 `"expand":"all"`(全部满足)或 `"any"`。参数抽不到/对端查不到=数据不足,机器自动如实报。

### 套路 8:真判不了(数据里没有这信息)

```json
{"node_id":"n19","decision":"insufficient_data",
 "mapping_reason":"判据要芯片款型枚举,本场景各源均未采集该信息"}
```

**这是诚实,不是失败**。注意:"这次样例数据里恰好没有"≠判不了——绑定写的是规则,值没出现运行时自然 matched=0(确定排除)或数据不足,别把样例观察写死进 decision。

---

## 四、字段词典(速查)

| 字段 | 属于 | 含义 |
|---|---|---|
| `source` | 通用 | 数据源名(cmd/config/diag/log…);跨源绑定顶层写最终查询落点 |
| `filter` | 通用 | 选行:`{"column":…, "equals"/"contains"/"excludes":…}` |
| `column` | 通用 | 判定/取值落在哪一列 |
| `extract` | 通用 | 格内抽值:`{"kv":"键名"}` 或 `{"regex":"带捕获组"}`;省略=整格即值 |
| `equals`/`contains`/`regex` | C/G | 判定目标,**列表=或**(同义词写一个列表);regex 编译不过内核响亮拒收 |
| `absent` | C/G | 反转极性:命中 0 行=成立(负存在判据必加);列全空自动改判数据不足 |
| `require` | C/G、A(count_rows) | 至少命中/满足几行 |
| `op`/`threshold` | A | 比较符与阈值(数字不带引号) |
| `scope` | A | `count_rows`(逐行比、数满足行数,首选)或 `aggregate`(先聚合,配 `agg`) |
| `combine`/`sub` | D | AND/OR + 子绑定列表(子绑定可各带 source=跨源) |
| `group_by` | D | 按对象列切组逐组判(不得跨源) |
| `side_column`/`peer_marker`/`value_column`/`fault_when` | P | 同表分侧比对 |
| `pair_from` | P | 跨表/跨源两侧取值比对(local/peer 各是取值子句) |
| `param_from`/`expand` | 跨源 | 传参链与逐值展开 |
| `decision` | 通用 | insufficient_data / not_applicable / cross_source(仅生成期用) |
| `mapping_reason` | 通用 | 人话写清"为什么这么绑"——给复核的人和 RCA 看 |

取值子句(param_from 参数、pair_from 两侧)**只认 source/column/filter/extract 四键**,拼错会被响亮拒收。

---

## 五、改完怎么验证(三步,秒级)

```bash
# 调优表改完 → 机械编译回产物(校验不过一个文件都不写,按行号报错)
python "<产物或技能assets>/scripts/import_tree_excel.py" --out "<产物目录或 assets>"
# 单跑受影响的源(秒级,看该判据的事实对不对)
python "<产物>/scripts/<源>_analyzer.py" "<该源数据文件>" -o out.json
# 体检绑定质量(泛词/极性/一值多挂等警告,逐条看)
python "<generate技能>/scripts/validate_conditions.py" out.json
```

完整契约见 `scripts/condition_binding_schema.md`(字段全集与极性铁律,以它为准)。
