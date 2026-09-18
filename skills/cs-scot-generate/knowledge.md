# 出厂知识库(技能级 knowledge.md)——生成产物前,在这里预置经验

这是**跨场景积累**的知识库,和 SKILL.md 平级、随技能分发。**在这里写好知识再跑 generate**,
组装(assemble)会把本文件拷进产物的 `tree/knowledge.md`——绑定任务说明和诊断(RCA)都会按
「适用」标签自动载入匹配条目,你的经验从**第一次绑定**就生效。

一条知识 = 一段,固定结构,照下面的样子往后抄:

    ### 知识标题(一句话,名词短语)
    适用:分支/场景关键词(可多个,用、分隔;写「通用」或不写这行 = 全场景生效)
    正文(人话,写清:去哪个数据、看哪一列、怎么查、什么算正常/不算故障)

说明:
- 「适用」的关键词按**故障树节点名+判据文本**匹配——只有「适用」行参与匹配,正文不参与;
  诊断时只载入与当前分支相关的条目,不会全量灌;
- 知识是**口径不是事实**:它教系统"去哪查、怎么解读",查证结论仍以数据为准;
- 产物/打包技能里的 `tree/knowledge.md`(assets/tree/knowledge.md)是**现场增补层**:
  交付后业务同事往那份里加,改完对诊断立即生效;要让绑定也重新受益,重跑步骤 2.5;
- 本文件只在产物**首次归集**时拷入;产物里已有 knowledge.md 时重跑 assemble 不覆盖
  (现场增补不丢)。想让老产物吃到新的出厂知识,手动把条目粘过去。

### 命令回显类判据的判定位置
阶段:绑定
适用:通用
「根据 display X 查看…」类判据,命令名列(如 CmdName)一般只用来**选行**(filter),真正的判定要落在
**回显内容列**(如 CmdResult)上找故障特征——判据下方附的回显示例通常就写着故障长什么样(如
「Number of vxlan tunnel : 0」),照示例里的特征行绑。把"命令名出现过"当故障信号基本必误报:
健康设备同样执行这些巡检命令。

### 缺失/未配置类判据的极性
阶段:绑定
适用:通用
判据说「未配置X/缺失X/没有X」时,**缺行才是故障**:绑定要加 absent(命中 0 行=成立)。
直接绑「存在X」方向是反的。「配了A但没配B」= 组合:存在A + absent B;
要求发生在同一接口/对象上时再加 group_by(按接口列分组逐个判)。

### V-STP根桥桥MAC类判据的绑定套路
阶段:绑定
适用:V-STP、桥MAC、STP
"检查配置是否有X、回显是否有Y、配置的值Z是否等于回显里的W"这类多要件判据,绑成
D AND [前置条件…, D OR [absent 缺失分支, P pair_from 值比对分支]],属跨源绑定(写进 cross_analyzer)。
可直接套用的完整例子(V-STP 根桥未配置桥MAC;正则要锚在字段名上防误抓):
{"node_id":"<nN>","class":"D","source":"config","combine":"AND","sub":[
   {"class":"G","source":"config","column":"level1","contains":["stp v-stp enable"]},
   {"class":"G","source":"cmd","column":"CmdResult",
    "filter":{"column":"CmdName","contains":"display stp"},"contains":["This bridge is the root"]},
   {"class":"D","combine":"OR","source":"config","sub":[
      {"class":"G","source":"config","column":"level1","contains":["stp bridge-address"],"absent":true},
      {"class":"P","source":"config","fault_when":"not_equal","pair_from":{
         "local":{"source":"config","column":"level1",
                  "filter":{"column":"level1","contains":"stp bridge-address"},
                  "extract":{"regex":"stp bridge-address ([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})"}},
         "peer":{"source":"cmd","column":"CmdResult",
                 "filter":{"column":"CmdName","contains":"display stp v-stp"},
                 "extract":{"regex":"Bridge Mac\\s*:\\s*Config=[0-9a-fA-F-]+\\s*/\\s*Active=([0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4})"}}}}]}]}

### M-LAG端口信息(上游传参)的本地查法
阶段:绑定
适用:M-LAG、端口、LACP、单归
平台脚本里的 mlagPort/PortStateL/PortNameL/MlagIDL 等「上游传参」,本地**不需要任何上游**:
- cmd 源:display dfs-group 1 m-lag / display dfs-group node 的回显里有端口名、端口状态(Port State)
  等字段——filter 选中对应 CmdName 的回显行,再从内容里抠字段;
- diag 源(EAV 窄表):DataItem 列的 MLAG_PortState/MLAG_ID/MLAG_NodeId 等指标(**以本产物
  diag 枚举清单里真实出现的名字为准**),本端对端按「M-LAG场景对端查询方法」的口径区分。
两端比对/接力链写 param_from/pair_from(跨源,进 cross_analyzer);别以「需上游传参/需平台JSON」
为由写 insufficient_data。

### M-LAG场景对端查询方法
适用:M-LAG
在 diag 数据中,筛选「DataItem」列为「MLAG_NodeId」的数据,当结果为两条时即可查询到本端与对端的
数据,少于两条即无法查询对端。其中「Object」列里的内容带*号的为本端,不带*的为对端。其余设备具体
信息通常从「ObjContent」列里获取。

### M-LAG场景本端对端信息使用方法
阶段:诊断
适用:M-LAG
通常使用本端对端信息的名字,即「NENAME」列,在其他数据源中查询有关该网元的相关信息。
