# 接入类「配置拓扑」场景适配(NSA 等)—— 怎么把 MO 配置接进来、怎么写 custom

> **⚠ 本文示例写于 v1 内核集**:其中 F 类(配置检查)已封存 `archive/kernels-v1/`——
> 现役内核集下「参数=期望值」改绑 **C `equals`**(`{"class":"C","column":"<参数列>","equals":["<期望值>"],"match":"exact"}`),
> 语义等价;其余 A/C/custom 示例照旧可用。要原样复活 F 类见 archive 的 README。

接入类故障(NSA/SA 的 S1/X2 链路、路由、EPGroup、多运营商)很多判据要查**配置**,而配置常是这种**文本**(不是表格):

```
GNBCUS1:MOName=GNBCUS1_1,UpEpGroupId=EPGROUP_1,CellId=CELL_1;
NRCellAlgoSwitch:MOName=SW_1,NsaDcSwitch=OFF,CellId=CELL_1;
EPGROUP:MOName=EPGROUP_1,epgroupid=EPGROUP_1,userplanepeerrefs={uppeerid=UPPEER_1,userlabel=SITE_A};
USERPLANEPEER:MOName=UPPEER_1,uppeerid=UPPEER_1,peeripv4=192.168.100.1;
IPRT:MOName=IPRT_1,dstip=192.168.100.0,dstmask=255.255.255.0,nexthop=10.200.0.1;
```

`mo_config.py`(随产物一起拷到 `scripts/`)把这事打通了,分两层:

## 一、配置当数据源:`.txt` 直接喂,MO 类型当"页签"

- 用 `--data <配置源名>=<配置.txt>` 喂进来,`load_table` 自动解析。
- **每个 MO 类型 = 一个"页签"**,条件里 `source` 写 MO 类型名即可只取那类(避免不同 MO 的同名列互相污染)。
- 于是"配置参数检查"这类判据,**不用 custom**,直接套现成模板:

```python
# NsaDcSwitch 被关 → 触发(F 类:某参数=期望值)
{"id":"L5-dcswitch","node":"DC开关关闭","node_id":"nX","condition_text":"NsaDcSwitch=OFF",
 "class":"F","source":"NRCellAlgoSwitch","param":"NsaDcSwitch","equals":"OFF"}

# 接入用户数上限配置过小(A 类:阈值比较)
{"id":"L5-maxuser","node":"接入用户上限过小","node_id":"nY","condition_text":"MaxRrcConnUserNum ≤ 10",
 "class":"A","source":"NRCELLRES","column":"MaxRrcConnUserNum","op":"<=","threshold":10,"scope":"aggregate","agg":"min"}
```

## 二、模板搞不定的,写 custom + 用 mo_config 的原语

在生成的 `<源>_analyzer.py` 顶部 `from mo_config import ...`,在 `CUSTOM` 里按条件 id 写函数。
custom 收 `table=(header,rows)`(该条 source 的表),返回与内核同形的 fact(`fact_ok`/`fact_insufficient` 帮你拼)。

可用原语:`group_rows`(扁平配置表→按MO分组)、`mo_get`(按字段取值)、`extract_kv`(从`{k=v,..}`串取值)、
`ipv4_in_subnet`/`mask_to_cidr`、`split_multi`(分号多值)、`find_header_row`(动态表头)。

### 例1:跨 MO 追溯 + IP 路由匹配(纯配置源,source 指整份配置)
```python
from mo_config import group_rows, mo_get, extract_kv, ipv4_in_subnet, fact_ok, fact_insufficient

def _s1_link_unreachable(table):
    g = group_rows(table)                                            # {MO类型: [实例,...]}
    # EPGROUP_1 →(取 refs)→ uppeerid →(查对端IP)
    refs = mo_get(g, "EPGROUP", "epgroupid", "EPGROUP_1", "userplanepeerrefs")
    uppeers = [u for blob in refs for u in extract_kv(blob, "uppeerid")]
    peer_ips = [ip for uid in uppeers for ip in mo_get(g, "USERPLANEPEER", "uppeerid", uid, "peeripv4")]
    if not peer_ips:
        return fact_insufficient("追溯不到对端IP(EPGROUP/USERPLANEPEER 配置缺)")
    # 对端IP 是否落在任一条路由网段内 → 不在=链路不通
    routes = g.get("IPRT", [])
    reachable = any(ipv4_in_subnet(ip, r.get("dstip"), r.get("dstmask")) for ip in peer_ips for r in routes)
    return fact_ok(satisfied=not reachable, peer_ips=peer_ips, reachable=reachable)

CUSTOM = {"L5-s1link": _s1_link_unreachable}
# 对应条件:{"id":"L5-s1link","node":"S1链路不通","node_id":"nZ","condition_text":"...","mode":"custom","source":"<配置源名>"}
```

### 例2:跨源(CHR 的 EPGroupId + 配置追溯)—— custom 收第二个参数 `load`
一个 custom 函数若写成 `def f(table, load)`,框架会把"取别的源/页签"的函数 `load` 也传进来:
```python
def _chr_epgroup_link(table, load):                 # table=该条 source;load("X")=另取源/MO类型
    chr_table = load("chr")                          # 另一份源(同一分析器内的别的页签)
    # …从 chr_table 拿出失败的 EPGroupId,再用配置追溯它的对端IP/路由…
    return fact_ok(satisfied=...)
```
> 跨**文件**(CHR 文件 + 配置文件是两份)仍按老办法:拆成两条子绑定、用 **D 类** AND/OR 组合(各自 source 到各自文件),或留给 RCA 合。

### 例3:分号多值列(EPGroupId 含 `A;B;C`)
```python
from mo_config import split_multi
# CHR 的 EPGroupId 多值里是否包含某条链路的 EPGROUP(精确成员,不是子串)
def _epgroup_member(table):
    header, rows = table
    hit = any("EPGROUP_1" in split_multi(r.get("EPGroupId")) for r in rows)
    return fact_ok(satisfied=hit)
```

### 例4:核查表"表头不在首行"(NSA 参数核查 Excel)
```python
from mo_config import find_header_row, fact_ok
def _link_check(table):
    header, rows = table
    hi, real_header = find_header_row(rows, must_have=["S1链路核查"])   # 找到真表头那行
    if hi is None:
        return fact_ok(satisfied=False, reason="未找到 S1链路核查 列")
    col = real_header.index([c for c in real_header if "S1链路核查" in c][0])
    data = list(rows[hi+1:])
    bad = sum(1 for r in data if list(r.values())[col].strip() not in ("OK", "——", ""))
    return fact_ok(satisfied=bad > 0, bad_rows=bad)
```

## 三、对不上就老实数据不足
铁律不变:配置源没给、参数列查不到、追溯断链 → `fact_insufficient(reason)`,**别编值**。custom 跑不通会在 generate 第3步真实数据上当场暴露。
