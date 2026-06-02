# kpi 解析脚本生成提示词(由元提示词为「SA场景接入失败」生成)

> 这是三级链的中间产物示例:元提示词读了本场景子树 + kpi 数据格式后,产出了下面这份"查 kpi"的提示词;再由它生成 `kpi_analyzer.py`。

请生成一个解析 kpi 数据的 Python 脚本,满足:

## 一、理解输入
- kpi 数据为 kpiTree 格式:每行一段 JSON,含 `name`(计数器名,形如 `N.xxx`/`L.xxx`)与 `value`(取值)。用 `data_loader.load_json_column` 读取(支持 xlsx/csv)。
- 本场景故障子树涉及的 kpi 计数器与判断条件:
  - `N.RRC.SetupAtt.Sum - N.RRC.SetupSucc.Sum > 0`
  - `N.RRC.SetupFail.NoRes > 0`
  - `N.NG.Setup.Fail.NoRsp > 0`

## 二、确定性计算
- 取出上述计数器的数值,**用代码**算出每条判断条件:实际值是多少、代入后怎么比较、是否成立。

## 三、数据处理要点
- 计数器名可能带 HTML 污染(末尾 `<span`、实体 `&gt;` 等),需清洗。
- 缺失计数器按 0 处理。

## 四、输出 JSON
- `summary`(计数器数量)、`counters`(名→值)、`conditions`(数组)。
- 每条 `conditions` 元素:
  - `expr`:判断条件原文(逐字);
  - `values`:用到的实际数值(结构化);
  - `actual`:实际数值文字,如 `N.RRC.SetupFail.NoRes = 60`;
  - `judgment`:代入比较 + 结论,如 `60 > 0 → 成立`;
  - `hit`:是否成立。

## 五、工程要求
- Python 3.11;参数:数据文件、`-o/--output`;复用 data_loader;中文注释;可直接运行。
