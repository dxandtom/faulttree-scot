# 平台1 本地联调工装

把平台1 的"优化按钮"请求翻成技能的本地输入,跑完迁移后回调。**这一层是工装,不进技能**:
技能(cs-scot-migrate / cs-scot-generate)只认本地已摆好的文件;下载、回调、状态这些平台概念
都停在这层。上平台2 时本目录整体由开发的集成代码替换,技能原样上。

## 链路

```
平台1 点优化 ──POST──▶ server.py(受理即回 success)
                          │ 后台
                          ▼
                      driver.py
   ① 解析平台 excel(L*_Name_CN/EN + Product_Obs + Product_Name,层级动态)
   ② downloader.py:按 Product_Obs 下载各节点 zip;按 obskey 下载上一版 skill
   ③ 解包 skill,定位产物目录(技能形态认 assets/)
   ④ 调 cs-scot-migrate 对应表批量迁移(平台表直接喂 --map,技能已认这格式)
   ⑤ 重打包新版 skill zip
   ⑥ 回调平台 update 接口(version_id + skill 产物文件)
```

- skill obskey 为空 = 平台侧还没有 skill → 生成场景:要走 cs-scot-generate 的模型会话,
  驱动不代跑,打印指引后退出码 4。
- 节点选择(单个/所有/0节点):按"平台导出哪些行就迁哪些节点"理解,驱动不二次筛选;
  若联调发现平台是全量导出另带选择参数,再在 driver 里按参数裁表。
- 迁移退出码非 0(3=有对不上号的行):不打包、不回调,工作目录保留供排查——
  失败该怎么回给平台(报错回调?状态挂着?)在待确认清单里。

## 三步联调

1. **纯本地跑通(不碰网络)**:把平台给的样例(excel、各节点 zip、上一版 skill zip)放进一个
   目录当假货架,跑:
   ```
   python integration/platform1/driver.py --request request.json --fake-store <样例目录>
   ```
   fake 模式按文件名取:zip 按 Product_Name,skill 按 obskey 的文件名部分。
2. **接通 obs**:平台的下载脚本放到本目录(建议名 `platform_download.py`),在
   `downloader._platform_fetch()` 里按它的真实用法接线;**按 obskey 下 skill 的能力平台脚本
   还没有,要在它基础上改造出来**(照它的 obs 客户端初始化方式补一个按 key 取对象)。
   接完去掉 `--fake-store` 重跑同一条命令。
3. **接真请求**:`pip install fastapi uvicorn`,配 `PLATFORM1_CALLBACK_URL`,起 server.py,
   平台点按钮打过来。请求/回调的字段名等平台样例到了,在 server.py 顶部与 driver.py 顶部的
   常量处照实改(各集中一处)。

## 等平台1 的材料

- [ ] 请求与回调的报文样例(定字段名;当前占位:excel_file / skill_obskey / version_id;
      回调 id / file)
- [ ] 下载脚本源码 + 用法(接线 + 改造出按 obskey 下 skill)
- [ ] obs 访问条件:本机网络通不通、账号/密钥怎么给
- [ ] 样例一套:导出 excel、节点 zip 包、上一版 skill 文件

## 待与平台1 确认

- [ ] "0节点"的语义(不选节点点优化是全量还是仅刷新?)
- [ ] 节点选择是否就是"excel 只导出选中行"(驱动按此理解)
- [ ] 回调里 skill 文件是文件流还是先传 obs 回 objectkey(skill_version.objectkey 谁来填)
- [ ] 迁移失败怎么回(报错回调?status 一直 optimizing?)
- [ ] 回调接口的鉴权方式
