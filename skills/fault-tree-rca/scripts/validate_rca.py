#!/usr/bin/env python3
"""RCA 输出诚实性自检(指标无关、机械兜底)。

只兜**结构底线 + 诚实**,不碰强度/排序/你怀疑什么(那是模型判断)。分档严格度不同:
- **rca_result(确证根因)**:严。揪 ① 自相矛盾(判"成立"却又承认拿不到数据);
  ② 假根因(路径含"数据不足/不成立"节点);③ 缺路径(没 evidence_path)。
- **probable_causes(疑似)**:松——路径含"数据不足"**甚至"不成立"**节点都**放行**(那正是疑似该体现的泛化),
  只查 ④ 有没有把理由说清(`blocked_at`/`caveats` + `support`/`evidence`)。
- **任何档**:⑤ 给"无事实/未覆盖"节点编一个"成立" = 伪造,揪(需传 rca_input.json 才能核对)。
- ⑥ 空诊断(三档全空)。

用法:python validate_rca.py <rca.json> [rca_input.json]   # 不合格 → 退出码 1
     (带上 rca_input.json 才能做"成立必须有事实"的伪造核对)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# judgment 里"承认拿不到/不够"的标记(出现这些就不该判成立)
MISSING = ("无实测", "未找到", "找不到", "不存在", "事实中无", "无此值", "查不到",
           "未提供", "无法验证", "无法判定", "缺数据", "缺少", "数据不足")
# 确证根因路径里不允许出现的"未完整成立"标记
NOT_PROVEN = ("数据不足", "不成立", "部分成立", "未判定", "无法验证", "无法判定")


def _is_clean_hold(judgment: str) -> bool:
    j = judgment or ""
    return "成立" in j and not any(m in j for m in NOT_PROVEN)


def _tag(judgment: str, tags):
    j = judgment or ""
    return next((t for t in tags if t in j), None)


def _confirmed(doc):
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for k in ("rca_result", "root_causes", "results"):
            if isinstance(doc.get(k), list):
                return doc[k]
    return []


def _evidence(rc):
    return rc.get("evidence_path") or rc.get("evidence") or [] if isinstance(rc, dict) else []


def _holding_map(input_path):
    """从 rca_input.json 的 pruned_nodes 读 {node_id: (是否 pass_through, 是否有成立事实)};
    读不到 / 无 pruned_nodes(退回 md 形态)→ None,跳过伪造核对。"""
    try:
        d = json.loads(Path(input_path).read_text(encoding="utf-8"))
        pruned = (d.get("fault_tree") or {}).get("pruned_nodes") or {}
    except Exception:
        return None
    if not pruned:
        return None
    out = {}
    for nid, n in pruned.items():
        hold = any((f.get("status") == "ok" and f.get("predicate_satisfied") is True)
                   for f in (n.get("facts") or []))
        cond = n.get("condition") or ""
        has_cond = bool(cond) and "无条件" not in cond and "无判断" not in cond   # 根/分组节点(无条件进入)不算"有真条件"
        out[nid] = (bool(n.get("pass_through")), hold, has_cond)
    return out


def _node_universe(input_path):
    """pruned 形态下,rca_input 全文出现过的 nN 就是节点宇宙——evidence 里冒出宇宙外的 nN 必是编造。
    实测漏洞:伪造核对对"不认识的节点"豁免、路径链核对把它过滤,纯捏造 node_id 的确证曾全绿通过。"""
    try:
        text = Path(input_path).read_text(encoding="utf-8")
    except Exception:
        return None
    return set(re.findall(r"\bn\d+\b", text))


def _check_unknown_nodes(rc, tier, universe, problems):
    if universe is None:
        return
    name = rc.get("Root_cause") or rc.get("root_cause") or f"(未命名{tier})"
    for node in _evidence(rc):
        nid = str(node.get("node_id") or "") if isinstance(node, dict) else ""
        if re.fullmatch(r"n\d+", nid) and nid not in universe:
            problems.append(f"[未知节点] {tier}「{name}」evidence_path 里的 node_id「{nid}」"
                            f"在 rca_input 全文中不存在——编造的节点,删掉或改用真实节点(名字引用不受此限)")


def _unaddressed_holds(input_path, rca_path):
    """候选对账(软提示):有成立事实、有真条件、非 pass_through 的候选节点,
    在 rca.json **全文**里(node_id 按词边界认,防 n13 命中 n136;节点全名也认——契约允许只写名字)
    一处都没出现 → 视为"漏裁决"点名。只提示不拦:契约的 excluded 档允许"一句话带过不进列表",
    硬卡会误杀合法排除,还会重演"逼重写→编假节点"的旧病(docs/notes 有案底)。
    实测背景:同产物同数据两跑,第二跑把 5 个有成立事实的分支通篇漏掉,原有校验(只核"写了的对不对")全数放行。"""
    try:
        d = json.loads(Path(input_path).read_text(encoding="utf-8"))
        pruned = (d.get("fault_tree") or {}).get("pruned_nodes") or {}
        text = Path(rca_path).read_text(encoding="utf-8")
    except Exception:
        return []
    if not pruned:
        return []
    ids_in_text = set(re.findall(r"\bn\d+\b", text))
    missing = []
    for nid, n in pruned.items():
        hold = any(f.get("status") == "ok" and f.get("predicate_satisfied") is True
                   for f in (n.get("facts") or []))
        cond = n.get("condition") or ""
        has_cond = bool(cond) and "无条件" not in cond and "无判断" not in cond
        if not hold or not has_cond or n.get("pass_through"):
            continue
        name = str(n.get("name") or "")
        if nid in ids_in_text or (name and name in text):
            continue
        missing.append((nid, name))
    missing.sort(key=lambda x: int(x[0][1:]) if x[0][1:].isdigit() else 10**9)
    return missing


def _check_fabrication(rc, tier, hold_map, problems):
    """任何档:判'成立'的节点,若它**有真条件**却在局部子树里没有成立事实 = 伪造
    (pass_through / 无条件的根·分组节点判成立是结构性的,放行)。"""
    if hold_map is None:
        return
    name = rc.get("Root_cause") or rc.get("root_cause") or f"(未命名{tier})"
    for node in _evidence(rc):
        if not isinstance(node, dict) or not _is_clean_hold(str(node.get("judgment", ""))):
            continue
        nid = node.get("node_id")
        if nid not in hold_map:   # 不在局部子树里就不强判(避免误伤)
            continue
        pass_through, hold, has_cond = hold_map[nid]
        if has_cond and not pass_through and not hold:
            problems.append(
                f"[伪造成立] {tier}「{name}」/节点「{node.get('node', nid)}」判'成立',"
                f"但它有判断条件、局部子树里却没有任何'成立'事实(也非 pass_through)→ 别给没数据的节点编成立")


def _parent_map(input_path):
    """从 rca_input.json 的 pruned_nodes 读 {node_id: parent_id}。剪枝时祖先全保留 → 链完整到根,
    故沿 parent 上溯无缺口、不会因剪枝误判。读不到 → None,跳过路径校验。"""
    try:
        d = json.loads(Path(input_path).read_text(encoding="utf-8"))
        pruned = (d.get("fault_tree") or {}).get("pruned_nodes") or {}
    except Exception:
        return None
    return {nid: n.get("parent") for nid, n in pruned.items()} if pruned else None


def _ancestors(nid, pm, limit=400):
    seen, cur, steps = set(), pm.get(nid), 0
    while cur and cur not in seen and steps < limit:
        seen.add(cur); cur = pm.get(cur); steps += 1
    return seen


def _check_path_chain(rc, tier, pm, problems):
    """evidence_path 相邻两节点必须在同一条根→叶链上(一个是另一个的祖先,允许剪枝跳过中间节点)。
    两者互不为祖先 = 跨了不同分支硬拼路径(典型:把某叶子挂到不相干的中间节点下,模型搞混树结构时常见)→ 揪。"""
    if pm is None:
        return
    name = rc.get("Root_cause") or rc.get("root_cause") or f"(未命名{tier})"
    ev = [n for n in _evidence(rc) if isinstance(n, dict) and n.get("node_id") in pm]
    for a, b in zip(ev, ev[1:]):
        pid, cid = a.get("node_id"), b.get("node_id")
        if pid == cid:
            continue
        if pid not in _ancestors(cid, pm) and cid not in _ancestors(pid, pm):
            problems.append(
                f"[路径不实] {tier}「{name}」evidence_path 里「{a.get('node', pid)}」({pid}) 与"
                f"「{b.get('node', cid)}」({cid}) 不在同一条根→叶链上(互不为祖先)——跨分支拼的路径,"
                f"按 tree_index 真实层级重排,别把叶子挂到不相干的节点下")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print("用法:python validate_rca.py <rca.json> [rca_input.json]", file=sys.stderr)
        return 2
    p = Path(sys.argv[1])
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"无法读取/解析 {p}: {e}", file=sys.stderr)
        return 2
    hold_map = _holding_map(sys.argv[2]) if len(sys.argv) >= 3 else None
    parent_map = _parent_map(sys.argv[2]) if len(sys.argv) >= 3 else None
    universe = _node_universe(sys.argv[2]) if (len(sys.argv) >= 3 and hold_map is not None) else None

    confirmed = _confirmed(doc)
    probables = doc.get("probable_causes", []) if isinstance(doc, dict) else []
    undet = doc.get("undetermined", []) if isinstance(doc, dict) else []
    problems = []

    # ⑥ 空诊断:三档全空 = 啥也没说
    if not confirmed and not probables and not undet:
        problems.append("[空诊断] rca_result / probable_causes / undetermined 全空——"
                        "就算没有根因,也必须在 undetermined 写清:卡在哪个节点、缺哪个量、看到哪些观察。")

    # 确证根因:严(①自相矛盾 ②假根因 ③缺路径 ⑤伪造)
    for rc in confirmed:
        if not isinstance(rc, dict):
            continue
        name = rc.get("Root_cause") or rc.get("root_cause") or "(未命名根因)"
        ev = _evidence(rc)
        if not ev:
            problems.append(f"[缺路径] 确证根因「{name}」没有 evidence_path——确证必须把根→叶整条路径、每节点的条件/实际值/判断摆出来")
        for node in ev:
            if not isinstance(node, dict):
                continue
            judg, actual, nname = str(node.get("judgment", "")), str(node.get("actual", "")), node.get("node", "?")
            if _is_clean_hold(judg) and any(m in (judg + actual) for m in MISSING):
                problems.append(f"[自相矛盾] 确证根因「{name}」/节点「{nname}」判'成立'却承认拿不到数据 → {judg!r}")
            tag = _tag(judg, NOT_PROVEN)
            if tag:
                # 结构节点豁免:该节点在 rca_input 里是 pass_through / 无真条件(根·分组·汇总节点),
                # 本就没有独立判据,谈不上"数据不足"破坏确证——弱模型常把它标成'数据不足'而不是契约
                # 要求的'无独立条件',那是标签瑕疵、不是路径断了。以 rca_input 的机械事实为准,
                # 不按层级/位置豁免(靠前的节点也可能真有判据,位置豁免会放走真的假确证)。
                if hold_map:
                    pt, _h, hc = hold_map.get(str(node.get("node_id", "")), (False, False, True))
                    if pt or not hc:
                        continue
                problems.append(
                    f"[假根因] 确证根因「{name}」路径含'{tag}'节点「{nname}」"
                    f"→ 挪进 probable_causes 或 excluded,别凑路径、别造节点")
        _check_fabrication(rc, "确证根因", hold_map, problems)
        _check_path_chain(rc, "确证根因", parent_map, problems)
        _check_unknown_nodes(rc, "确证根因", universe, problems)

    # 疑似:松——只查 ④理由齐不齐 + ⑤有没有伪造成立;路径含 不成立/数据不足 一律放行
    for rc in probables:
        if not isinstance(rc, dict):
            continue
        name = rc.get("Root_cause") or "(未命名疑似)"
        if not (rc.get("blocked_at") or rc.get("caveats")):
            problems.append(f"[疑似缺理由] 疑似「{name}」没说清为什么只是疑似——必须写 `blocked_at`(卡在哪个节点)或 `caveats`(为何不确定)")
        if not (rc.get("support") or _evidence(rc)):
            problems.append(f"[疑似缺证据] 疑似「{name}」既无 `support` 也无 `evidence_path`——疑似也得摆出支撑它的正向证据")
        _check_fabrication(rc, "疑似", hold_map, problems)
        _check_path_chain(rc, "疑似", parent_map, problems)
        _check_unknown_nodes(rc, "疑似", universe, problems)

    # 候选对账(软提示,不改退出码):专逮"有成立事实的候选通篇没下落"的漏裁决
    if len(sys.argv) >= 3:
        miss = _unaddressed_holds(sys.argv[2], sys.argv[1])
        if miss:
            print(f"⚠ 候选对账:以下 {len(miss)} 个节点有'成立'事实,rca.json 里却通篇没有下落(漏裁决嫌疑):")
            for nid, name in miss[:15]:
                print(f"  - {nid} 「{name}」")
            if len(miss) > 15:
                print(f"  …(还有 {len(miss) - 15} 个,用 rca_query --holding 看全量)")
            print("  → 逐个裁决(rca_query --node nX 走查),**别为消除本提示把候选整批塞进 probable_causes**"
                  "——成立事实里常混着背景噪声(如子串命中),照单全收=放弃裁决、报告变噪音墙。"
                  "真候选就补报(确证/疑似,或至少在 undetermined.observed 给一行);"
                  "背景噪声/确属排除的,在 excluded_note 一句话点名留痕,别静默略过。")

    if problems:
        print(f"不合格:发现 {len(problems)} 处问题:")
        for pr in problems:
            print("  - " + pr)
        print("\n修法:确证根因要路径全成立 + 摆出 evidence_path;疑似可含'数据不足/不成立'节点,"
              "但要写清 blocked_at/caveats/support;任何'成立'都得有真事实。**别凑路径、别造节点、别给没数据的节点判成立。**")
        return 1

    note = "(已用 rca_input 核对'成立'有事实)" if hold_map is not None else "(未带 rca_input,跳过伪造核对)"
    print(f"通过:确证根因 {len(confirmed)} 条(路径全成立)、疑似 {len(probables)} 条(理由齐备){note}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
