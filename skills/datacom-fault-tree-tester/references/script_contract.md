# 判断脚本的十条格式要求

生成 `fault_check_<节点名>.py` 时必须全部满足。`scripts/check_contract.py --strict` 就是这十条的机械化检查（格式检查），FAIL 行自带修法提示。

1. **入口与输入参数**。入口函数必须命名 `fault_check(data_dir)`，与文件名前缀同名；`check_ne` 之类的内部帮手函数不算入口，下游不会按它们调用。`data_dir` 按字符串对待，用 pathlib 先 `Path(data_dir)` 显式包装，不假设入参已是 Path。额外参数一律做成入口函数的具名参数并给字面量默认值；命令行运行时可经 `sys.argv` 传入，但 `sys.argv` 和 `print` 只允许出现在 `if __name__ == '__main__'` 分支。原因：脚本会被下游以库方式调用，那时 `sys.argv` 是宿主进程的命令行，在函数体里读它会把宿主的参数当成自己的。
2. **目录遍历**。遍历网元目录下所有子目录，每个子目录是一个网元。找数据文件时 `.txt` 与 `.log` 两种后缀都要收、大小写不敏感（现场常见 `log.log`，只识别 `.txt` 会整目录漏掉）。脚本自动识别两种结构：多网元（数据目录下有网元子目录）和单网元（数据目录下直接是 CONFIG、CMD、LOG，网元名取数据目录名）。
3. **输出格式**。返回下面这种 JSON：

   ```json
   {
     "matched": "true" | "false" | "undetermined" | "insufficient_data",
     "evidence": [
       {
         "type": "ne|interface|port|tunnel",
         "id": "网元名|Tunnel0/0/1|...",
         "matched": "true",
         "data": {
           "source_files": [
             {"file": "CMD/display bfd session all verbose.txt", "line_range": "1-50", "content": "命中上下文回显块（不超过 20 行，不许整段日志塞入）..."}
           ],
           "matched_items": [
             {
               "name": "TELSPToAcc1",
               "state": "Down",
               "bind_interface": "Tunnel0/0/1",
               "_source_file": "CMD/display bfd session all verbose.txt",
               "_source_line": "23-28"
             }
           ]
         }
       }
     ],
     "details": {
       "reason": "判不了的原因（matched 为 undetermined 或 insufficient_data 时必填）",
       "searched_files": ["LOG/log.log", "…（matched 为 false 时建议填：查过哪些文件）"]
     }
   }
   ```

   - `matched` 四态：`"true"` 是数据在、查了、确定命中；`"false"` 是数据在、查了、确定没有；`"undetermined"` 是数据在但判不出来（解析失败、格式不认识、入参不符、脚本出错自保），reason 写为什么判不动；`"insufficient_data"` 是没数据可判（命令没采集、文件缺失），reason 精确到缺哪条命令或哪个文件，下游据此生成补采清单。
   - `type` 是实体类型（ne、interface、port、tunnel 等），`id` 是实体标识（网元名、接口名、隧道名等）。
   - `data.source_files` 是原始证据块：文件路径、行号范围、命中上下文（不超过 20 行，整段日志会撑爆下游上下文）。
   - `data.matched_items` 是命中的具体项：关键字段加 `_source_file`、`_source_line` 指向原始文件与位置。报告的证据栏、用户追问细节，都从这里逐字引用。
   - `details.reason` 在 matched 为 undetermined 或 insufficient_data 时必填。
   - `details.searched_files` 在 matched 为 false 时建议填，"查了、确实没有"有文件清单佐证，下游的排除留痕有凭据。
4. **文件位置**。生成与验证阶段放在本技能 `scripts/` 下（与共享解析库同目录，`from search_xxx import …` 才成立）；打包交付后从 `scripts/` 移入交付包，技能目录不留生成产物。`scripts/` 只常驻 `search_*.py` 与 `check_contract.py`。
5. **复用共享解析库**。配置解析必须用 `scripts/search_config.py` 的 `parse_config_file()`、`find_config_item()`、`get_group_items()`，禁止自行实现（原因见 `判断方法_规范.md` 附录：它已处理编码自动检测、`#` 分隔的配置块、大小写不敏感搜索）。
6. **全局形态，不是按网元的字典**。顶层就是一个 `matched` 加 `evidence` 数组；禁止返回 `{"网元名": {...}}` 形式，网元、接口、隧道等实体信息写进 evidence 条目的 `type` 与 `id`。
7. **判不了必须如实自报**。数据缺失（命令没采集、文件缺失）返回 `insufficient_data`；数据在但判不动（解析失败等）返回 `undetermined`；两者 reason 必填。禁止用 `matched:"false"` 加 `details.error` 冒充"未检测到"；禁止解析失败后换一种方式硬判。原因：脚本结论会被下游根因分析当事实用，把"没数据"当"排除"会带偏根因，把坏数据判成"成立"更危险。
8. **数据来源表列全**。判断方法文档的"数据来源"表必须把脚本实际用到的每条命令都列上，下游按它核对采集覆盖。
9. **依赖限制**。只允许 import Python 标准库和 `scripts/` 内的共享模块（`search_*.py`）；禁止任何第三方库。运行平台严格管控第三方件，迁移后一跑就 ImportError。
10. **生成后必须过格式检查**。`python scripts/check_contract.py <脚本路径> --strict`，逐项修到全绿。
