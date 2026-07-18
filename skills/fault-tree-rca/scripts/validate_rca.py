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
NOT_PROVEN = ("数据不足", "不成立", "部分成立", "未判定", "无法验证", "无法判定", "语义近似", "存疑")


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


def _probables_without_block(probables, input_path):
    """疑似档体检(软):按契约,疑似 = 路径无不成立、但**有数据不足节点**且叶子有强证据——
    所以每条疑似的 evidence_path 里该有至少一个真实阻塞点(有真判据、非 pass_through、
    局部子树里没有任何 status=ok 的事实 = 真缺数)。路径全干净的点名(该升确证/该写实阻塞)。
    没给 node_id 的条目核不了,跳过(理由齐不齐另有硬检查);pruned 缺失(md 形态)整体跳过。"""
    try:
        d = json.loads(Path(input_path).read_text(encoding="utf-8"))
        pruned = (d.get("fault_tree") or {}).get("pruned_nodes") or {}
    except Exception:
        return []
    if not pruned:
        return []

    def _blocked(nid):
        n = pruned.get(nid)
        if not n:
            return False
        cond = n.get("condition") or ""
        if not cond or "无条件" in cond or "无判断" in cond or n.get("pass_through"):
            return False
        return not any(f.get("status") == "ok" for f in (n.get("facts") or []))

    out = []
    for rc in probables or []:
        if not isinstance(rc, dict):
            continue
        ev = _evidence(rc)
        if any(_tag(str(e.get("judgment", "")), NOT_PROVEN) for e in ev if isinstance(e, dict)):
            continue                        # 疑虑已落字(数据不足/语义近似/存疑)——算"有伤",不点名
        ids = [str(e.get("node_id", "")) for e in ev if isinstance(e, dict)]
        ids = [i for i in ids if i]
        if not ids:
            continue
        if not any(_blocked(i) for i in ids):
            out.append((rc.get("Root_cause") or rc.get("root_cause") or "(未命名)",
                        str(rc.get("blocked_at", ""))[:40]))
    return out


def _real_blocks(input_path):
    """{node_id: 节点名} —— rca_input 里的「真实阻塞节点」:有真判据、非 pass_through、
    且局部子树里没有任何 status=ok 的事实(=真查不了)。疑似的 blocked_at 必须指向其中之一,
    才算"确有阻塞";读不到/无 pruned(md 形态)→ None(无从核实,调用方从宽)。"""
    try:
        d = json.loads(Path(input_path).read_text(encoding="utf-8"))
        pruned = (d.get("fault_tree") or {}).get("pruned_nodes") or {}
    except Exception:
        return None
    if not pruned:
        return None
    out = {}
    for nid, n in pruned.items():
        cond = n.get("condition") or ""
        if not cond or "无条件" in cond or "无判断" in cond or n.get("pass_through"):
            continue
        if any(f.get("status") == "ok" for f in (n.get("facts") or [])):
            continue
        out[nid] = str(n.get("name") or "")
    return out


def _excluded_holds_check(doc, rca_path, input_path):
    """排除核对(软):有'成立'事实、**无任何反证**(无 ok+不成立 事实)、却没进确证/疑似的候选
    (下落只能是 excluded/observed)。排除不一定错(松绑定假命中就该排),但每次排除都要晾出来:
    理由必须落在反证或绑定质量上。实测:确证/疑似都设硬检查后,excluded 成了新泄压阀——弱模型
    以"属正常配置模式,非故障"排除了业务确认的真根因(推翻树判据),还发明"证据量比别人少/
    与已确认根因同源"等排除标准。通篇没提的节点归候选对账管,这里只核"有下落但下落是排除"的。"""
    try:
        d = json.loads(Path(input_path).read_text(encoding="utf-8"))
        pruned = (d.get("fault_tree") or {}).get("pruned_nodes") or {}
        text = Path(rca_path).read_text(encoding="utf-8")
    except Exception:
        return []
    if not pruned:
        return []
    reported = set()
    for tier in ("rca_result", "probable_causes"):
        for rc in (doc.get(tier) or []) if isinstance(doc, dict) else []:
            if isinstance(rc, dict):
                for e in _evidence(rc):
                    if isinstance(e, dict) and e.get("node_id"):
                        reported.add(str(e.get("node_id")))
    ids_in_text = set(re.findall(r"\bn\d+\b", text))
    out = []
    for nid, n in pruned.items():
        facts = n.get("facts") or []
        hold = any(f.get("status") == "ok" and f.get("predicate_satisfied") is True for f in facts)
        refuted = any(f.get("status") == "ok" and f.get("predicate_satisfied") is False for f in facts)
        cond = n.get("condition") or ""
        has_cond = bool(cond) and "无条件" not in cond and "无判断" not in cond
        if not hold or refuted or not has_cond or n.get("pass_through") or nid in reported:
            continue
        name = str(n.get("name") or "")
        if nid not in ids_in_text and not (name and name in text):
            continue
        out.append((nid, name))
    out.sort(key=lambda x: int(x[0][1:]) if x[0][1:].isdigit() else 10**9)
    return out


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
    blocks = _real_blocks(sys.argv[2]) if len(sys.argv) >= 3 else None

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
        # 口径不符(硬):证据链与档位打架——evidence 里没有任何节点写'数据不足/不成立'类疑虑,
        # blocked_at 又空 = "全成立却居疑似"。模型有权凭主观怀疑降档,但怀疑必须落字、落在具体节点
        # ('单源/保险起见'不是档位理由)。实测:确证被误杀几轮后,弱模型防御性把全成立候选整批降进
        # 疑似(0 确证/13 疑似),软提示点名 10 条它照样交卷——不硬拦,往下躲永远零代价。
        _ev = _evidence(rc)
        if _ev and not any(_tag(str(n.get("judgment", "")), NOT_PROVEN) for n in _ev if isinstance(n, dict)):
            # 证据链全"成立"→ blocked_at 必须**解析到一个真实阻塞节点**(_real_blocks)。实测:堵住
            # "blocked_at 为空"后,弱模型改往里塞散文("无阻塞,匹配率极低(0.8%)")继续赖在疑似档——
            # 散文不指向任何节点,解析不到=硬拦。真有语义怀疑(如"log 是代理证据")的正道:把存疑
            # 节点的 judgment 写'数据不足/语义近似/存疑'(NOT_PROVEN 认这些词),不受本检查影响。
            ba = str(rc.get("blocked_at") or "").strip()
            resolved = False
            if ba and blocks is None:
                resolved = True                          # 没给 rca_input 无从核实,维持从宽
            elif ba and blocks:
                _ids = set(re.findall(r"\bn\d+\b", ba))
                resolved = any(i in blocks for i in _ids) or any(nm and nm in ba for nm in blocks.values())
            if not resolved:
                why = ("blocked_at 为空" if not ba
                       else f"blocked_at「{ba[:40]}」未指向任何真实阻塞节点(散文式理由不算阻塞)")
                problems.append(
                    f"[口径不符] 疑似「{name}」evidence_path 全'成立'且 {why}"
                    f"——全成立该进 rca_result;确有怀疑就写实:存疑节点 judgment 改'数据不足/语义近似',"
                    f"blocked_at 指向具体阻塞节点。'单源/保险起见/匹配率低'都不是降档理由")
        _check_fabrication(rc, "疑似", hold_map, problems)
        _check_path_chain(rc, "疑似", parent_map, problems)
        _check_unknown_nodes(rc, "疑似", universe, problems)

    # 疑似档体检(软提示,不改退出码):确证与疑似的镜像检查——确证不许路径带伤(上面的硬检查),
    # 疑似必须真有伤:evidence_path 里至少一个「有真判据、非 pass_through、且没有任何 ok 事实」的
    # 实际阻塞点。路径全干净的疑似 = 要么该升确证、要么阻塞点没写进路径(实测:校验误杀确证 →
    # 弱模型批量降档避审,疑似从个位数膨胀到 20+ ——疑似档不能当泄压阀)。
    soft_hints = 0
    if len(sys.argv) >= 3:
        nb = _probables_without_block(probables, sys.argv[2])
        soft_hints += len(nb)
        if nb:
            print(f"⚠ 疑似档体检:以下 {len(nb)} 条疑似的 evidence_path 里没有任何「有真判据且缺数据」的实际阻塞点:")
            for nm, ba in nb[:10]:
                print(f"  - 「{nm}」(blocked_at: {ba or '未写'})")
            if len(nb) > 10:
                print(f"  …(还有 {len(nb) - 10} 条)")
            print("  → 路径真干净就升确证;确有阻塞就把阻塞节点补进 evidence_path、blocked_at 写实——"
                  "疑似档不是确证的避风港,也不是候选对账的泄压阀。")

    # 排除核对(软提示,不改退出码):被排除/仅观察的"成立且无反证"候选逐条晾出来。
    # 注意:排除是合法出口,这张清单不计入"必须清零"的软提示数(不然有一条合法排除就永远清不了)
    # ——它的处置方式是:报告「已排除的候选」一节逐条晾出站得住的理由(反证/绑定质量)。
    exh = []
    if len(sys.argv) >= 3:
        exh = _excluded_holds_check(doc, sys.argv[1], sys.argv[2])
        if exh:
            print(f"⚠ 排除核对:以下 {len(exh)} 个节点有'成立'事实、**无任何反证**,却没进确证/疑似(下落=排除/仅观察):")
            for nid, name in exh[:10]:
                print(f"  - {nid} 「{name}」")
            if len(exh) > 10:
                print(f"  …(还有 {len(exh) - 10} 个)")
            print("  → 排除的合法理由只有两种:①反证(该节点有 ok+不成立 的事实);②绑定质量问题"
                  "(点名哪条绑定、为何假命中)。**'配置常见/看着正常/证据量比别人少/与已确认根因同源'"
                  "都不是排除理由——树的判据就是故障标准,你无权推翻树。**理由站不住的,挪回疑似或 undetermined。")

    # 候选对账(软提示,不改退出码):专逮"有成立事实的候选通篇没下落"的漏裁决
    if len(sys.argv) >= 3:
        miss = _unaddressed_holds(sys.argv[2], sys.argv[1])
        soft_hints += len(miss)
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
    if soft_hints:
        # "通过"两个字曾让弱模型把上方 ⚠ 全当装饰直接交卷(实测两轮)——有软提示就别给干净的台阶
        print(f"硬检查通过,但上方还有 {soft_hints} 条 ⚠ 软提示**未处置**——"
              f"**带着未处置软提示产出的报告 = 不合格交付**:逐条处置(升档/补报/excluded_note 留痕/"
              f"阻塞写实)并重跑本校验,**直到此行消失才可以写最终报告**"
              f"(当前:确证 {len(confirmed)} 条、疑似 {len(probables)} 条{note})。")
        return 0
    extra = (f";另:排除核对列出 {len(exh)} 个节点——排除是合法出口,但报告「已排除的候选」一节"
             f"必须逐条晾出理由(反证/绑定质量),无声排除=不合格交付" if exh else "")
    print(f"通过:确证根因 {len(confirmed)} 条(路径全成立)、疑似 {len(probables)} 条(理由齐备){note}{extra}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
