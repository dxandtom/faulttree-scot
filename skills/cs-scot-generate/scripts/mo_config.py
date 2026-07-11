#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NSA/接入类「配置拓扑」场景适配器:把 MO 配置文本解析成系统能用的表,并提供 custom 用的几个原语。

为什么要它:本仓的 load_table 只认 csv/xlsx(行列表格)。但接入类故障的配置往往是这种【文本】:
    GNBCUS1:MOName=GNBCUS1_1,UpEpGroupId=EPGROUP_1,CellId=CELL_1;
    USERPLANEPEER:MOName=UPPEER_1,uppeerid=UPPEER_1,peeripv4=192.168.100.1;
    EPGROUP:MOName=EPGROUP_1,userplanepeerrefs={uppeerid=UPPEER_1,userlabel=SITE_A};   # 值里可带花括号
不翻成表,F 类(配置检查)、跨 MO 追溯、IP 路由这些判据连数据都拿不到。本模块只做"把它读进来 + 几个原语",
判断逻辑仍由各条件的 custom 函数组合(返回与内核同形的 fact)。纯标准库,无第三方依赖。

谁用:
  · load_table 读 .txt 配置 → parse_mo_text(返回 (header, rows),与 csv/xlsx 同形,F/C/A 类直接能用);
  · custom 函数里:parse_mo_grouped(按 MO 分组)+ ipv4_in_subnet / split_multi / extract_kv / mo_get /
    extract_metric 这些原语(跨 MO 追溯 = mo_get + extract_kv 链式拼,见文末自测)。
"""
from __future__ import annotations
import re
from pathlib import Path


# ============ 一、解析 MO 配置文本 ============
def _split_top(s: str, sep: str) -> list:
    """按 sep 切,但【不切】花括号/方括号里的内容(值里可能是 {k=v,k=v} 这种)。"""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def parse_mo_lines(text: str) -> list:
    """文本 → [(MO类型, {键:值, ...}), ...]。每条配置项一行,以 ; 结尾;行内 key=val 用逗号分隔。"""
    items = []
    # 配置项之间用 ; 或换行分隔(同样不切花括号内的分号)
    chunks = []
    for line in text.splitlines():
        chunks += [c for c in _split_top(line, ";")]
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or chunk.startswith("#") or ":" not in chunk:
            continue
        motype, rest = chunk.split(":", 1)
        motype = motype.strip()
        if not motype or "=" not in rest:
            continue
        kv = {}
        for pair in _split_top(rest, ","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                kv[k.strip()] = v.strip()
        items.append((motype, kv))
    return items


def parse_mo_text(text_or_path, sheet=None) -> tuple:
    """→ (header, rows[list[dict]]):每个 MO 实例一行,列 = 'MO' + 出现过的键。与 load_table 同形。
    sheet 给某 MO 类型(如 'NRCellAlgoSwitch')→ 只返回该类的行(像 xlsx 选页签),避免不同 MO 的同名列互相污染。"""
    items = parse_mo_lines(_read(text_or_path))
    if sheet is not None:
        items = [(m, kv) for m, kv in items if m == sheet]
    header, seen = ["MO"], {"MO"}
    rows = []
    for motype, kv in items:
        row = {"MO": motype}
        for k, v in kv.items():
            row[k] = v
            if k not in seen:
                seen.add(k); header.append(k)
        rows.append(row)
    # 补全缺列(每行都有全部列,空串占位),与 csv 表一致
    rows = [{c: r.get(c, "") for c in header} for r in rows]
    return header, rows


def mo_sheets(text_or_path) -> list:
    """配置里出现的 MO 类型清单(给 list_sheets 用,让条件能 source=<MO类型> 指到某类)。"""
    seen = []
    for m, _ in parse_mo_lines(_read(text_or_path)):
        if m not in seen:
            seen.append(m)
    return seen


def parse_mo_grouped(text_or_path) -> dict:
    """→ {MO类型: [实例dict, ...]}:给 custom 做跨 MO 追溯用。"""
    g = {}
    for motype, kv in parse_mo_lines(_read(text_or_path)):
        g.setdefault(motype, []).append(kv)
    return g


def _read(text_or_path) -> str:
    if isinstance(text_or_path, (str, Path)) and "\n" not in str(text_or_path) and Path(str(text_or_path)).exists():
        return Path(text_or_path).read_bytes().decode("utf-8", "ignore")
    return str(text_or_path)


# ============ 二、custom 用的原语 ============
def split_multi(value, seps=";|,") -> list:
    """分号/竖线分隔的多值列 → 去重清洗后的列表。'A;B;;A' → ['A','B']。"""
    s = str(value or "")
    parts = re.split("[" + re.escape(seps) + "]", s)
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        if p and p.lower() not in ("none", "nan", "null") and p not in seen:
            seen.add(p); out.append(p)
    return out


def extract_kv(blob, key):
    """从 '{uppeerid=UPPEER_1,userlabel=SITE_A}' 这种引用串里取出某 key 的【所有】取值。"""
    return re.findall(re.escape(key) + r"=([^,}\s]+)", str(blob or ""))


def extract_metric(text, pattern, group=1):
    """从命令回显/日志长文本里按正则抽数值:返回所有命中的 float 列表(抽不到 → 空列表,调用方判数据不足)。

    用途:回显里「CRC: 52366 packets」这类**标签恒在、数值才是信号**的判据——
    G 类子串匹配对它必假阳(值为 0 的回显也含 'CRC:' 这个子串),必须抽出数值再比较。
    例:extract_metric(echo, r"CRC:\\s*([\\d,]+)\\s*packets") → [52366.0, 0.0, …]"""
    out = []
    for m in re.finditer(pattern, str(text or ""), re.IGNORECASE):
        try:
            out.append(float(str(m.group(group)).replace(",", "")))
        except (ValueError, IndexError):
            pass
    return out


def ip_to_int(ip):
    p = str(ip or "").strip().split(".")
    if len(p) != 4:
        return None
    try:
        a, b, c, d = (int(x) for x in p)
    except ValueError:
        return None
    if not all(0 <= x <= 255 for x in (a, b, c, d)):
        return None
    return (a << 24) | (b << 16) | (c << 8) | d


def mask_to_cidr(mask):
    m = ip_to_int(mask)
    if m is None:
        return None
    n = 0
    while m & 0x80000000:
        n += 1; m = (m << 1) & 0xFFFFFFFF
    return n


def ipv4_in_subnet(ip, dst_ip, mask_or_cidr) -> bool:
    """ip 是否落在 dst_ip/掩码 网段内。掩码可传点分(255.255.0.0)或 CIDR 整数(16)。"""
    a, b = ip_to_int(ip), ip_to_int(dst_ip)
    if a is None or b is None:
        return False
    cidr = mask_or_cidr if isinstance(mask_or_cidr, int) else mask_to_cidr(mask_or_cidr)
    if cidr is None:
        return False
    mask = (0xFFFFFFFF << (32 - cidr)) & 0xFFFFFFFF if cidr else 0
    return (a & mask) == (b & mask)


def group_rows(table) -> dict:
    """把 parse_mo_text 的扁平表 (header, rows) 按 'MO' 列重新分组 → {MO类型: [实例dict]}。
    custom 里最常用:条件 source 指整份配置(不带页签=全表),拿到 table 后 group_rows 一下就能跨 MO 追溯。"""
    _, rows = table
    g = {}
    for r in rows:
        g.setdefault(r.get("MO", ""), []).append({k: v for k, v in r.items() if k != "MO" and v != ""})
    return g


def mo_find(grouped, motype, **eq) -> list:
    """找某类 MO 里【字段=值】的实例。eq 如 epgroupid='EPGROUP_1'(支持多条件 AND)。返回实例 dict 列表。"""
    out = []
    for inst in grouped.get(motype, []):
        if all(str(inst.get(k, "")) == str(v) for k, v in eq.items()):
            out.append(inst)
    return out


def mo_get(grouped, motype, match_field, match_val, get_field) -> list:
    """找 motype 里 match_field==match_val 的实例,取它们的 get_field 值(列表,容错;查不到返回 [])。
    跨 MO 追溯就是把它一节节串起来,见模块底部自测里的 EPGROUP→对端IP 例子。"""
    return [inst.get(get_field, "") for inst in grouped.get(motype, [])
            if str(inst.get(match_field, "")) == str(match_val) and inst.get(get_field, "") != ""]


def find_header_row(rows, must_have):
    """核查表那种'表头不在首行':在已读到的 rows 里找含全部 must_have 标记的那一行,返回(该行索引, 该行作表头)。
    找不到返回 (None, None)。custom 里用它把错位的表重新对齐。"""
    for i, r in enumerate(rows):
        vals = [str(v).strip() for v in r.values()]
        if all(any(m in v for v in vals) for m in must_have):
            return i, vals
    return None, None


# ============ 三、与内核同形的 fact ============
def fact_ok(satisfied: bool, **evidence) -> dict:
    return {"status": "ok", "predicate_satisfied": bool(satisfied), "mode": "custom", **evidence}


def fact_insufficient(reason: str, **evidence) -> dict:
    return {"status": "insufficient_data", "reason": reason, "mode": "custom", **evidence}


# ============ 自测(用脱敏样例验证;无需真实数据) ============
if __name__ == "__main__":
    SAMPLE = """
    GNBCUS1:MOName=GNBCUS1_1,UpEpGroupId=EPGROUP_1,CellId=CELL_1;
    GNBCUX2:MOName=GNBCUX2_1,UpEpGroupId=EPGROUP_2,CellId=CELL_2;
    EPGROUP:MOName=EPGROUP_1,epgroupid=EPGROUP_1,userplanepeerrefs={uppeerid=UPPEER_1,userlabel=SITE_A};
    USERPLANEPEER:MOName=UPPEER_1,uppeerid=UPPEER_1,peeripv4=192.168.100.1,userlabel=SITE_A;
    IPRT:MOName=IPRT_1,dstip=192.168.100.0,dstmask=255.255.255.0,nexthop=10.200.0.1;
    NRCellAlgoSwitch:MOName=SW_1,NsaDcSwitch=OFF,CellId=CELL_1;
    """
    ok = []
    header, rows = parse_mo_text(SAMPLE)
    ok.append(("解析成表", len(rows) == 6 and "MO" in header and "peeripv4" in header))
    g = parse_mo_grouped(SAMPLE)
    ok.append(("按MO分组", set(g) == {"GNBCUS1", "GNBCUX2", "EPGROUP", "USERPLANEPEER", "IPRT", "NRCellAlgoSwitch"}))
    # 花括号值不被逗号切坏
    refs = g["EPGROUP"][0]["userplanepeerrefs"]
    ok.append(("花括号保完整", extract_kv(refs, "uppeerid") == ["UPPEER_1"]))
    # 跨 MO 追溯:EPGROUP_1 →(取 refs)→ uppeerid →(查对端IP)。三步,custom 里照抄即可:
    refs = mo_get(g, "EPGROUP", "epgroupid", "EPGROUP_1", "userplanepeerrefs")     # ['{uppeerid=UPPEER_1,...}']
    uppeerids = [u for blob in refs for u in extract_kv(blob, "uppeerid")]          # ['UPPEER_1']
    ips = [ip for uid in uppeerids for ip in mo_get(g, "USERPLANEPEER", "uppeerid", uid, "peeripv4")]
    ok.append(("链式追溯到对端IP", ips == ["192.168.100.1"]))
    # IP 网段匹配:对端 IP 是否在某条路由网段内
    ok.append(("IP在网段内", ipv4_in_subnet("192.168.100.1", "192.168.100.0", "255.255.255.0") is True))
    ok.append(("IP不在别的网段", ipv4_in_subnet("192.168.100.1", "10.100.0.0", 16) is False))
    ok.append(("掩码转CIDR", mask_to_cidr("255.255.255.0") == 24))
    # 多值列
    ok.append(("分号多值", split_multi("EPGROUP_1;EPGROUP_2;;EPGROUP_1") == ["EPGROUP_1", "EPGROUP_2"]))
    print("mo_config 自测:")
    for name, passed in ok:
        print(f"  [{'✓' if passed else '✗ 失败'}] {name}")
    import sys
    sys.exit(0 if all(p for _, p in ok) else 1)
