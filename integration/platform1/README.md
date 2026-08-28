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

optimizeType 三档(平台口径,请求带 `optimizeType` 字段;excel 为增强表:
`L*_Name_CN/EN + judgement_condition_cn/en + Product_Obs/Name`,判据列取 cn):
- **NONE** = 问题不在节点:下载现行 skill → migrate `--refresh-only` 把引擎代码刷到当前代
  (树与绑定不动)→ 回传。引擎的"当前代"来自本机仓库,联调机器记得先拉新;
- **MULTI** = 部分表:只动列出的节点、**不动树结构**——迁移(zip)、清判据(双空行)、
  判据同步(判据变了绑定不动)照做,新增节点行拒绝(migrate `--map-mode multi`);
- **ALL** = 全量表:判据与**树结构都以表为准**——增节点(含纯判据结构节点)、
  表里踪影全无的节点自动删(调优表 .bak 可回退、台账 deleted_at 留痕、两道防误删保险),
  migrate `--map-mode all`。

不带 `optimizeType` 的请求(本地实验/老请求)走老行为:空壳表=现行 skill 原样回传
(「skill 真下载」实验的判定标尺),有行=迁移且不带 --map-mode(能加不删)。
`prevObjectkey=null`(首次优化)一律拒绝,退出码 4:首版 skill 线下 generate 生成后录入,
优化按钮只管迭代(分工待与平台对齐,见待确认清单)。

迁移有失败行时按**部分交付**处理(用户拍板):包里有已生效内容(本次落地或此前已迁移)
就照样打包回调——已落地节点先生效,成功/跳过/失败清单连原因写进包内
`tree/migration_report.json`;一个生效内容都没有才停下不回调(回传无变化的包只会误导
版本记录),工作目录保留供排查。

## 联调实验(按序,前一个绿了再下一个)

处理链本身已在开发环境端到端演练全绿(平台表→下载→解包→migrate→重打包→回调收一发,
scratchpad 的 test_platform_e2e.py),联调机器上只验证"真材料、真网络"这几样。

**实验 A · 链路(fake 货架,不碰网络)**:验证真实 excel + 真实 zip 能走完迁移。
建个货架目录放:各节点 zip(文件名=表里 Product_Name)+ 现行 skill zip
(拿本地场景产物目录整个压缩,文件名=request 里 prevObjectkey 的最后一段),照
request.example.json 建 request.json(callback_url 留空),跑:
```
python integration/platform1/driver.py --request request.json --fake-store <货架目录>
```
预期:打印逐条下载→迁移汇总→产物 zip 路径。

**实验 B · skill 真下载(碰网络,一件事)**:验证下载服务肯给 skill 文件。
本目录放好 `obs_headers_config.json` + token/psf 密钥(同下载脚本原来那套,**不进仓库**;
`pandas requests` + psf 环境与你跑通下载脚本时相同)。造一个**只有表头**的空壳表,
request.json 里 excel 指它、prevObjectkey 填一个真实的 skill objectkey(找开发要),
**去掉 --fake-store** 跑同一条命令——驱动只做"下载 skill→原样打包"一件事。
下载被拒(响应是 JSON 而非文件流)= 服务端权限没放行,把输出甩给平台侧。

**实验 C · 全链真下载**:实验 A 的 request 换真表,去掉 --fake-store 重跑。
产物 zip 人工抽查(判据进树、绑定换血、migrations.json 台账)。

**实验 D · 接真请求(收官)**:`pip install fastapi uvicorn python-multipart`,配
`PLATFORM1_CALLBACK_URL=http://<平台1>/workspace/faultTreeNode/optimizeCallback`,起:
```
python -m uvicorn server:app --host 0.0.0.0 --port 8080 --app-dir integration/platform1
```
把 `http://<本机IP>:8080/optimize` 给平台配上,让管理员点一次优化。
服务模式默认带模型档(PLATFORM1_WITH_MODEL=0 可关)。线上口径是**一单一轮**:
每次点优化只做一轮模型处置就交付(部分交付兜底,已修的管道副本随包带走),
没修完的下次点优化在交付包基础上续迁——不让平台一单等几轮
(本地实验同理:重跑同一条命令就是下一轮)。每单请求的全程日志在
`integration/platform1/_runs/v<versionId前8>/run.log`。终端开着服务就在;
要长期挂(关终端不掉)再用 Windows 计划任务或 nssm 包一层,联调阶段不必。
常见坑:平台服务器到你机器 8080 的网络(内网互通/防火墙)。

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
- [x] optimizeType 语义(平台口径已问清):NONE=只刷引擎代码 / MULTI=部分表不动结构 /
      ALL=全量表判据与结构以表为准——驱动与 migrate(--map-mode)已按此实现;
      增强表判据列 judgement_condition_cn/en 已适配(取 cn)
- [ ] 回调接口要不要鉴权待定——口子已留:环境变量 PLATFORM1_CB_HEADERS 塞 JSON 请求头
      (例 {"invokeToken": "..."}),不设=不带
- [ ] 回调接口的「部分成功」字段——**平台已口头同意加**(口径也对上了:部分成功也算成功);
      待平台给字段名与格式(建议直接收包内 tree/migration_report.json 的结构:
      total/landed/ok/skipped/failed 带表行号与原因),定了就在 post_callback 里随回调带上;
      在那之前清单仍写在包内,平台解包可查
- [ ] 我方接口的正式 URL(文档示例 /xxx/api/v1/fault-tree/optimize 标注"以实际为准";
      我方接口就是 server.py,现挂 POST /optimize——把 http://<本机IP>:8080/optimize
      给平台配上即可;对方要求特定路径的话改 server.py 里 @app.post 一行)
- [ ] 首次优化(prevObjectkey=null)的分工要与平台对齐(**已提问,平台暂未答复这条**):
      平台请求只有 excel(仅节点路径,无判据列)+ zip 包,凑不齐从头生成一棵技能的素材
      (专家判据文档、场景层配置),按包拼节点也只是残缺骨架跑不起来——建议口径:
      首版 skill 线下用 cs-scot-generate 生成后录入平台,优化按钮只管有 skill 之后的迭代
      (没 skill 时置灰按钮,或平台单开萃取入口再谈 bootstrap);驱动遇到 null 拒绝并说明原因
