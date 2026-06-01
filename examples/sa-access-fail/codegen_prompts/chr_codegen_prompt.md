# chr 解析脚本生成提示词(由元提示词为「SA场景接入失败」生成)

> 三级链中间产物示例:元提示词读了本场景子树 + chr 数据格式后,产出这份"查 chr"的提示词;再由它生成 `chr_analyzer.py`。

请生成一个解析 chr 数据的 Python 脚本,满足:

## 一、理解输入
- chr 数据为表格(首行表头),含 `Time`、`DCErrType` 等字段。用 `data_loader.load_table` 读取(支持 xlsx/csv)。
- 本场景故障子树涉及的 chr 判断条件:
  - `DCErrType 中 RrcReject 占比 > 30%`
  - `DCErrType 中 TransLink 占比 > 20%`

## 二、确定性计算
- 统计 `DCErrType` 各取值的出现次数与占比,**用代码**算出每条判断条件是否成立。

## 三、数据处理要点
- 枚举字段做分布统计;占比 = 该取值计数 / 总记录数 × 100%。
- 空 `DCErrType` 行不计入分布。

## 四、输出 JSON
- `summary`(记录数)、`dcerrtype_distribution`(取值→次数)、`conditions`(每条:`expr`/`values`/`hit`)。

## 五、工程要求
- Python 3.11;参数:数据文件、`-o/--output`;复用 data_loader;中文注释;可直接运行。
