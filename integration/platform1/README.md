# 平台1 本地联调工装

把平台1 的"优化按钮"请求翻成技能的本地输入,跑完迁移后回调。**这一层是工装,不进技能**:
技能(cs-scot-migrate / cs-scot-generate)只认本地已摆好的文件;下载、回调、状态这些平台概念
都停在这层。上平台2 时本目录整体由开发的集成代码替换,技能原样上。

## 链路(字段名与平台接口文档 callDongXuanOptimizeApi 一致)

```
平台1 点优化 ──POST multipart(versionId + excelFile + prevObjectkey)──▶ server.py
                          │ 受理即回 success,后台:
                          ▼
                      driver.py
   ① 解析平台 excel(L*_Name_CN/EN + Product_Obs + Product_Name,层级动态)
   ② downloader.py:按 Product_Obs 逐条下载节点 zip;按 prevObjectkey 下载上一版 skill
      (同一个平台下载服务,docId 即 obs 地址——"下载 skill 能力"的改造就在这)
   ③ 解包 skill,定位产物目录(技能形态认 assets/)
   ④ 调 cs-scot-migrate 对应表批量迁移(平台表直接喂 --map,技能已认这格式)
   ⑤ 重打包新版 skill zip
   ⑥ 回调 POST /workspace/faultTreeNode/optimizeCallback(versionId + skillFile 文件流)
      平台收到后自己上传 OBS、把版本记录置 COMPLETED
```

三种优化场景(节点选择=平台按选择导出 excel 行):
- **MULTI / ALL**(表里有技能行 + 有上一版 skill)→ 主路,全自动;
- 表里有行 + `prevObjectkey=null`(首次优化)→ 生成场景:走 cs-scot-generate 的模型会话,
  驱动不代跑,退出码 4;
- **NONE**(空壳表,只有表头)+ 有上一版 → 无迁移可做,期望语义待和平台确认,退出码 5,不瞎动。

迁移退出码非 0(3=有对不上号的行):不打包、不回调,工作目录保留供排查。

## 三步联调

1. **纯本地跑通(不碰网络)**:把样例(excel、各节点 zip、上一版 skill zip)放进一个目录当
   假货架,跑:
   ```
   python integration/platform1/driver.py --request request.json --fake-store <样例目录>
   ```
   fake 模式按文件名取:zip 按 Product_Name,skill 按 prevObjectkey 的文件名部分。
2. **接通真下载**:平台下载脚本已在本目录(`download_fault_tree_artifact.py`,原版未改)。
   联调机器上装 `pandas requests` + psf(内部库),把 `obs_headers_config.json`
   (含 endpoint/invokeToken/psf 密钥路径,**不进仓库**)和 token/密钥文件放到本目录,
   去掉 `--fake-store` 重跑同一条命令。环境切换用 `PLATFORM1_ENV=dev|beta`
   (缺省用配置文件 default.env)。
3. **接真请求**:`pip install fastapi uvicorn python-multipart`,配
   `PLATFORM1_CALLBACK_URL=http://<平台1>/workspace/faultTreeNode/optimizeCallback`,
   起 server.py,平台点按钮打过来。

## 已定(平台材料齐了)

- [x] 请求字段:versionId(UUID)/ excelFile(文件流)/ prevObjectkey(首次为 null)
- [x] 回调字段:versionId + skillFile(zip 文件流);OBS 上传与版本记录更新由平台做
- [x] 节点选择语义:NONE=空壳表 / MULTI=选中行 / ALL=全部后代行
- [x] 下载脚本源码(download_fault_tree_artifact.py);skill 下载改造 = 同接口按
      prevObjectkey 单条调用(downloader._platform_fetch)

## 剩余待确认

- [ ] 下载服务对 skill 所在 bucket(如 netcare-skill)是否放行——代码已就绪(skill 与 zip
      同接口同格式,都带 file_name),若被拒属平台侧配置;联调第 2 步第一件事拿真
      prevObjectkey 试一次单条下载
- [x] NONE(空壳表):无迁移可做,不处理(用户拍板);注意驱动此时不回调,平台侧该记录会
      一直挂 OPTIMIZING(状态机拒绝重复提交)——建议知会平台这种请求别发或自行收尾
- [ ] 回调接口要不要鉴权待定——口子已留:环境变量 PLATFORM1_CB_HEADERS 塞 JSON 请求头
      (例 {"invokeToken": "..."}),不设=不带
- [ ] 我方接口的正式 URL(文档示例 /xxx/api/v1/fault-tree/optimize 标注"以实际为准";
      我方接口就是 server.py,现挂 POST /optimize——把 http://<本机IP>:8080/optimize
      给平台配上即可;对方要求特定路径的话改 server.py 里 @app.post 一行)
