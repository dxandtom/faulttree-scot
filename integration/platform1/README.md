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
`Node_ID + L*_Name_CN/EN + Diagnosis_Method_CN/EN + Solution_CN/EN + Product_Obs/Name + Need_Optimize`,判据列取 CN):
- **NONE** = 问题不在节点:下载现行 skill → migrate `--refresh-only` 把引擎代码刷到当前代
  (树与绑定不动)→ 回传。引擎的"当前代"来自本机仓库,联调机器记得先拉新;
- **MULTI** = 部分表:只动列出的节点、**不动树结构**——迁移(zip)、清判据(双空行)、
  判据同步(判据变了绑定不动)照做,新增节点行拒绝(migrate `--map-mode multi`);
- **ALL** = 全量表:判据与**树结构都以表为准**——增节点(含纯判据结构节点)、
  表里踪影全无的节点自动删(调优表 .bak 可回退、台账 deleted_at 留痕、两道防误删保险),
  migrate `--map-mode all`。

**标记契约(平台已定:每次都给全量表,Need_Optimize 标记用户选了哪些节点)**:标记列
`Need_Optimize`(TRUE/FALSE)在场即启用——结构增删看行的出现/消失,内容动作(zip 迁移/判据同步/
双空清除/解决方案同步)只对 TRUE 行执行;FALSE 行的 zip 不下载不迁,判据不同只提示。解决方案列
(`Solution_CN/EN`,取 CN)同为以表为准,空≠删除。全部 FALSE 且表与树一致 = 只刷新标准件(等于"刷新技能");
表里少了的节点照删(三道保险)。`Node_ID`(平台的节点 UUID)整列忽略,节点定位靠路径名字。

**全量表长什么样(与老表相比多 5 列;`make_full_table.py` 可从现行 skill 直接导出)**:

| 列 | 老表 | 全量表 | 语义 |
|---|---|---|---|
| L1_Name_CN/EN … Ln_Name_CN/EN | 有 | 有 | 节点路径,定位靠中文名;层级数动态 |
| Product_Obs / Product_Name | 有 | 有 | 交付 zip 的 obs 地址 / 包名;结构行留空 |
| Node_ID | — | **新** | 平台的节点 UUID,整列忽略 |
| Diagnosis_Method_CN / _EN | — | **新** | 判据,取 CN。**空 + 无 zip = 清除该节点判据与绑定**,所以已有节点的判据必须带上 |
| Solution_CN / _EN | — | **新** | 解决方案,取 CN;空≠删除 |
| Need_Optimize | — | **新** | 标记列:TRUE 才做内容动作(zip 迁移/判据同步/方案同步);FALSE 行 zip 不下载 |

行语义(请求带 `optimizeType=ALL`):**树上每个节点一行**(含根与中间结构层);表里没出现的
节点=删除(有根失踪/超深/过半三道保险);树上没有的行=新增(有 zip 就迁移绑定,只有判据就建
结构节点);标记行的判据/方案与树不同=同步。本地不等平台样例就能跑实验:
```
python integration/platform1/make_full_table.py --product <runC2>/skill/qos-security-issues/assets \
    --out 全量表.csv --zip-store <货架目录> [--platform-excel 老平台表.xlsx] [--mark 节点甲,节点乙]
```
不改直接喂驱动(fake-store,request 加 `"optimizeType": "ALL"`)= 零改动基线,预期零动作;在表上
改判据/换包/加行/删行/清判据各一格,再跑一遍就是一次真实的树更新(脚本末尾打印了五种改法与
预期输出符号 ~ ✓ + ✂)。

不带 `optimizeType` 的请求(本地实验/老请求)走老行为:空壳表=现行 skill 原样回传
(「skill 真下载」实验的判定标尺),有行=迁移且不带 --map-mode(能加不删)。
`prevObjectkey=null`(首次优化)一律拒绝,退出码 4:首版 skill 线下 generate 生成后录入,
优化按钮只管迭代(分工待与平台对齐,见待确认清单)。

迁移有失败行时按**部分交付**处理(用户拍板):包里有已生效内容(本次落地或此前已迁移)
就照样打包回调——已落地节点先生效,成功/跳过/失败清单连原因写进包内
`tree/migration_report.json`。**整单失败也回调**(用户拍板:版本挂在"优化中"比回传一个没变的包
更糟):表错位、包下载失败、一行都没落地等,回传**未变化的上一版 skill** + 包内
`tree/migration_report.json`(status=failed、reason 带原因/迁移输出末尾),让版本记录闭环;
`PLATFORM1_CB_ON_FAIL=0` 关。拿不到上一版(首次优化、skill 本身下不来)或回调本身失败的才停,
工作目录保留供排查。平台"失败/部分成功"字段定了再改为按字段告知。
端到端测试在 `tests/test_platform1_e2e.py`(fake 货架、标准库 + openpyxl,直接 python 跑)。

## 技能装在哪都能接(平台2 只上发行包时)

驱动调技能分两层:**机械层**直接起子进程跑迁移脚本 `migrate_fault_check.py`(不经前门);
**模型层**起 Claude Code 无头会话(`claude -p`)点名一个技能处置失败项。两层都不写死路径:
- 迁移脚本定位:`SCOT_MIGRATE_SCRIPT` 显式指定 → 各技能根下找,独立技能
  `cs-scot-migrate/scripts/…` 与发行包内嵌 `cs-scot-generate-optimize/migrate/scripts/…` 都认;
  技能根按序 = `SCOT_SKILLS_ROOT`(平台装技能的目录,可多个,os.pathsep 分隔)→ 本仓库 `skills/`
  → `~/.claude/skills`;
- 模型档点名:装了独立 `cs-scot-migrate` 就点它;只装了发行包就点前门
  `cs-scot-generate-optimize`——前门按输入形态机械路由(对应表=有层级列∧有 zip 列 → 优化线的
  migrate 手册,包内路径自动换算),不需要驱动告诉它"去 migrate";`SCOT_MODEL_SKILL` 可显式指定。
平台2 集成代码照这两条接:装好 `dist/cs-scot-generate-optimize/`,设 `SCOT_SKILLS_ROOT`,其余不变。

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

**实验 B' · 回调真发(不等 skill 下载,与 B 无依赖,可先做)**:验证真平台肯收我们的回传。
回调只要三样:网络可达 + 真实 versionId + zip 文件。
① 找平台要一个**真实 versionId**(让管理员点一次优化即预生成——我们接口没挂也没关系,
版本记录已落库,让他们查出来给你;或平台同事手工造一条);
② 可选的连通性探测:照平台文档的 curl 样例、随便一个 zip、乱填 versionId 打一发,
预期 HTTP 200 + responseCode="1" + message(说明网络通、字段认识,负向也算通过);
③ 真回传:拿实验 A 的材料,request 里 versionId 换成真的、callback_url 配真地址、
excel 用空壳表(走"原样回传"最短路),--fake-store 照旧跑 driver——打印
「[回调] … responseCode:"0"」即成;④ 平台侧核对:版本记录置 COMPLETED、OBS 出文件,
下载回来与本地产物一致。responseCode="1" 时 driver 会报失败并留产物,排查后重跑即重发。

**实验 C · 全链真下载**:实验 A 的 request 换真表,去掉 --fake-store 重跑。
产物 zip 人工抽查(判据进树、绑定换血、migrations.json 台账)。

**回调地址怎么配**:它是部署配置,不是每单请求的字段(平台真请求里没有)。回调和下载走
同一个网关、同一套鉴权、同一个环境切换,所以就配在下载配置 `obs_headers_config.json`
当前环境那一节里,加一个键(平台脚本不认识这个键,不看也不报错):
```json
"beta": {
  "endpoint": "https://<网关主机>/agent/downloadFaultTreeArtifact.do",
  "authType": "invokeToken", "invokeToken": "…", "tokenEncrypted": true,
  "callback_url": "https://<网关主机>/agent/workspace/faultTreeNode/optimizeCallback"
}
```
取值顺序:request.json 的 callback_url(本地实验显式给)→ 环境变量 PLATFORM1_CALLBACK_URL
→ 该文件 [default.env 或 PLATFORM1_ENV 指定的环境].callback_url;都没有=只出产物不回调,
驱动会打印取的是哪一处。回调与下载服务同主机时鉴权自动复用下载脚本的 invokeToken(见已定
清单)。注意端口:网关走默认 443(与下载端点一样不写端口);`:8081` 是平台应用服务器直连口,
从外面打网关带 8081 会连接超时。

**实验 D · 接真请求(收官)**:`pip install fastapi uvicorn python-multipart`,回调地址按上面
配好(配置文件或 PLATFORM1_CALLBACK_URL),起:
```
python -m uvicorn server:app --host 0.0.0.0 --port 8080 --app-dir integration/platform1
```
先本地自测服务层(不碰网络):起服务前多设 `PLATFORM1_FAKE_STORE=<货架目录>`(可再加
`PLATFORM1_WITH_MODEL=0` 省模型档),用 curl.exe 照平台口径 POST 一发到
`http://127.0.0.1:8080/optimize`(multipart:versionId + excelFile + prevObjectkey),立刻回
`{"result":"success"}`,后台跑完看 `_runs/v<versionId前8>/run.log` 末尾的 [产物]/[回调] 行。
自测过了去掉这两个变量重启服务,把 `http://<本机IP>:8080/optimize` 给平台配上,让管理员点一次优化。
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
- [x] 回调响应格式(平台文档已给):JSON {responseCode:"0"成功/"1"失败, message, data}
      ——driver 已按 responseCode 判成败(HTTP 200 但 responseCode="1" 算失败,打 message
      并停;产物留在 out/,排查后重跑同一条命令即重发);响应无此字段则退回只看 HTTP 状态
- [x] 节点选择语义:NONE=空壳表 / MULTI=选中行 / ALL=全部后代行
- [x] 下载脚本源码(download_fault_tree_artifact.py);skill 下载改造 = 同接口按
      prevObjectkey 单条调用(downloader._platform_fetch)

## 剩余待确认

- [x] 下载服务对 skill 的放行——**实验 B 已实测通过**(2026-09-01,objectKey=
      skill-wheel/fault-tree/node/…zip,响应 200 文件流,产物逐字节一致);skill 与节点
      zip 同桶同接口,无需额外配置
- [x] optimizeType 语义(平台口径已问清):NONE=只刷引擎代码 / MULTI=部分表不动结构 /
      ALL=全量表判据与结构以表为准——驱动与 migrate(--map-mode)已按此实现;
      正式全量表(Node_ID/Diagnosis_Method_CN/Solution_CN/Need_Optimize)已适配
- [x] 回调接口鉴权:回调与下载服务挂同一个网关(webot),鉴权口径一致——driver 回调时
      **自动复用下载脚本的 invokeToken**(同一份 obs_headers_config.json + token 文件 + 它的加密
      封装;仅在回调主机 = 下载端点主机时自动带,并同它一样不校验证书)。环境变量:
      PLATFORM1_CB_AUTH=platform 强制复用 / none 不带;PLATFORM1_CB_HEADERS 塞 JSON 请求头
      则以它为准(别的鉴权方式兜底)
- [x] 回调接口的**完整 URL**(实验 B' 2026-09-02 实测):与下载服务同网关同端口(443,不写端口),
      路径带 `/agent/` 前缀:`https://<网关主机>/agent/workspace/faultTreeNode/optimizeCallback`;
      直打网关根下无前缀路径 LB 回 404,带 `:8081` 连接超时(那是应用服务器直连口)。
      鉴权复用下载脚本 invokeToken 已被平台接受。**平台校验版本状态**:只接受"优化中"的
      versionId,已 COMPLETED 的回 `responseCode=40040003`(平台的 responseCode 是结构化码,
      非文档所写 "0"/"1";driver 按"非 0 即失败"判,兼容)——成功回调过的版本不能重发
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
