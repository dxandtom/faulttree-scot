# kpi 解析脚本生成提示词(由元提示词为「SA场景接入失败」生成)

> 这是三级链的中间产物示例:元提示词读了本场景子树 + kpi 数据格式后,产出了下面这份"查 kpi"的提示词;再由它生成 `kpi_analyzer.py`。

请写一个解析 kpi 数据的 Python 脚本,满足:

## 〇、最高铁律:只查数据,绝不下判断
- 脚本唯一职责:把计数器的**原始取值**忠实取出、把字段统计出来。
- **绝不**做 `N.RRC.SetupAtt.Sum - N.RRC.SetupSucc.Sum`、**绝不**和 `> 0` 比、**绝不**输出 `hit`/`judgment`/"成立"。减法、比较、是否成立 —— **全部交给 fault-tree-rca**。
- 缺失的计数器如实标 `found:false`,不要按 0 顶替后去判断。

## 一、理解输入
- kpi 数据为 kpiTree 格式:每行一段 JSON,含 `name`(计数器名,形如 `N.xxx`)与 `value`(取值);真实数据里每行可能还有 `parentId`/`formula`/`absExceptionThreshold` 等更多键。
- 脚本要**自包含**:内联一个读取函数(每行整行 `json.loads`,因 JSON 内含逗号不能按 CSV 拆列;支持 csv/xlsx + 编码检测),**不要 import 任何本仓自定义模块**。
- 本场景故障子树引用到的计数器(只需"取出原始值"):`N.RRC.SetupAtt.Sum`、`N.RRC.SetupSucc.Sum`、`N.RRC.SetupFail.NoRes`、`N.NG.Setup.Fail.NoRsp`。

## 二、数据驱动地遍历 + 统计(别写死字段)
- **遍历每行出现的所有键**做统计,不要把键清单写死:数值键 → count/min/max/mean;其余键 → 取值分布(top-N)。真实 kpiTree 键很杂,要能照样遍历。

## 三、定向提取故障树引用到的东西(只取值,不判断)
- 把上述每个计数器的**原始取值**取出来,给出 `value` + 所在位置(`where`)。
- 计数器名可能带 HTML 污染(末尾 `<span`、实体 `&gt;` 等),**清洗后再匹配**;清洗属于忠实取数,把原始名作为 `raw_name` 摆出来供核对。
- 取不到的计数器 → 标 `found:false`(不要按 0 顶替)。**不要**做减法、不要比 `> 0`、不要给 `hit`。

## 四、输出 JSON(只有事实,没有判断)
- 顶层:`indicator`、`summary`(节点/计数器数)、`field_stats`(每个键的分布/数值摘要)、`referenced`(子树引用计数器的原始取值)。
- `referenced` 每项形如:`{"found":true,"where":"kpiTree(name=该计数器 的 value)","value":<原始数>}`;被清洗过的再带 `raw_name`。
- **通篇不得出现** `conditions` / `hit` / `judgment` / "成立/不成立/根因" 等字样。

## 五、工程要求
- Python 3.11;参数:数据文件、`-o/--output`;**自包含**(内联读取,不 import 本仓模块);中文注释;路径加引号;可直接运行。
