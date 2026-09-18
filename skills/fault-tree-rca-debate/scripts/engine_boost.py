#!/usr/bin/env python3
"""引擎加速补丁(运行时、实例级、opt-in)——vendor 原件零改动。

两个彼此独立的加速器,由 run_debate_rca 的开关控制:
1. **匹配记忆化缓存**(--no-match-cache 关):引擎在辩论里对同一兄弟反复 _quick_check_match,
   同一(question, 判据集)只问一次 LLM,其余全部命中缓存——纯 I/O 去重,零语义变化
   (代价:同参重复调用不再重新采样,匹配结果反而更自洽)。
2. **并行执行**(--parallel N,默认 1=关):把三处**彼此独立**的串行 LLM 调用循环换成
   线程池并发——逐条件匹配、逐成员辩护、逐评委打分、逐兄弟 quick check。
   评分公式与聚合逻辑**逐字拷自 vendor 并加源码哈希守卫**:上游发新版引擎、方法源码
   一变,对应补丁自动退回串行并打印告警,绝不带着过期拷贝静默跑。
   全局并发由一个信号量封顶(包在 client.chat 最外层),嵌套并行也不会超发压垮网关。

只在真引擎(fault_tree_evaluator_bfs_advanced)上生效;mock/bridge 形状不符时整体跳过。
"""
from __future__ import annotations

import hashlib
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from types import MethodType

# 补丁对应的 vendor 方法源码指纹(升级引擎后不符 → 该补丁自动跳过)
_HASH_MATCH = "0ed35b2e565ae694b389db3c60e4eb34faf9d30c20fd089fa2d41e788ac1b076"
_HASH_DEFENSE = "590833df0cdd43925f9a429060b2e260917380cef27c937fbf1b5133d6c5ab80"
_HASH_DEBATE = "3015fe469f70617c57918e6ee03154cf44808fd5fda1c526f64091e725d3f6b2"


def _src_hash(fn) -> str:
    try:
        return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()
    except Exception:
        return ""


class _AdaptiveGate:
    """自适应并发限制(AIMD,本地真跑 429 压垮网关后引入):
    - 网关回 429/503 → 并发上限**减半**(乘性降,最低 1);
    - 连续 8 次成功 → 上限 **+1**(加性升,封顶初始值)——错误消失后逐步回升。
    限制在 HTTP 层(requests.post),重试间隙会释放名额,比 chat 级信号量更不压网关。"""

    def __init__(self, max_limit: int):
        self.max = max(1, int(max_limit))
        self.limit = float(self.max)
        self.active, self._ok = 0, 0
        self.cv = threading.Condition()

    def __enter__(self):
        with self.cv:
            while self.active >= max(1, int(self.limit)):
                self.cv.wait(timeout=5)
            self.active += 1
        return self

    def __exit__(self, *exc):
        with self.cv:
            self.active -= 1
            self.cv.notify_all()

    def on_throttle(self):
        with self.cv:
            new = max(1.0, self.limit / 2)
            if int(new) < int(self.limit):
                print(f"[自适应并发] 网关限流(429/503),并发 {int(self.limit)}→{int(new)}")
            self.limit, self._ok = new, 0

    def on_success(self):
        with self.cv:
            if self.limit >= self.max:
                return
            self._ok += 1
            if self._ok >= 8:
                self._ok = 0
                new = min(float(self.max), self.limit + 1)
                if int(new) > int(self.limit):
                    print(f"[自适应并发] 网关恢复,并发 {int(self.limit)}→{int(new)}")
                self.limit = new
                self.cv.notify_all()


def _wrap_post_adaptive(eng, n: int):
    """把并发限制装在 vendor 模块的 requests.post 上(vendor 文件零改动):
    比包 chat 更贴近网关——能看见真实状态码(429 才降档,连接错误不降,那不是流控信号),
    且 vendor 内层重试的 sleep 间隙不占名额。"""
    orig_post = eng.requests.post
    if getattr(orig_post, "_boost_adaptive", False):
        return None                        # 已装过(同进程二次 apply):不重复叠加
    gate = _AdaptiveGate(n)

    def post(url, **kw):
        with gate:
            resp = orig_post(url, **kw)
        code = getattr(resp, "status_code", 200)
        if code in (429, 503):
            gate.on_throttle()
        elif 200 <= code < 300:
            gate.on_success()
        return resp

    post._boost_adaptive = True
    eng.requests.post = post
    return gate


def _wrap_chat_failcounter(client):
    """给 chat 挂一个单调递增的失败计数(空响应=该调用重试耗尽)。
    用途:① 缓存防污染(见 _wrap_match_cache);② 运行结束时报告瞬时失败次数——
    shield 撤销降级标记后,'中途失败过'这件事否则对外不可见。"""
    orig = client.chat
    client._boost_fail_count = 0
    lock = threading.Lock()

    def chat(messages, temperature=None):
        resp = orig(messages, temperature)
        if not resp:
            with lock:
                client._boost_fail_count += 1
        return resp

    client.chat = chat


def _wrap_match_cache(matcher, client):
    """实例级记忆化:key=(question, 判据元组, 当前 fallback 态)。线程安全。

    防污染(对抗审查发现的实际问题):某次匹配恰逢故障窗口,单条判据会静默走关键词回退,
    结果与健康 LLM 不同——若缓存,网络恢复后污染值被永久复用且无降级记录。
    故:计算期间 chat 失败计数只要动过,该结果**只用不缓存**(下次重算)。"""
    orig = matcher.match_conditions
    cache, lock = {}, threading.Lock()
    matcher._boost_match_cache = cache   # 暴露给 seed_match_cache(P2a 预播种)用

    def cached(question, conditions):
        key = (question, tuple(conditions or ()), bool(getattr(matcher, "use_fallback", False)))
        with lock:
            if key in cache:
                return cache[key]
        before = getattr(client, "_boost_fail_count", 0)
        result = orig(question, conditions)
        if getattr(client, "_boost_fail_count", 0) == before:   # 期间零失败才可信
            with lock:
                cache.setdefault(key, result)
        return result

    matcher.match_conditions = cached


def _parallel_match(matcher, workers: int):
    """match_conditions 的并行版(语义=vendor 原版:数命中/总数/比例)。"""
    def match_conditions(question, conditions):
        if not conditions:
            return 0, 0, 0.0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            hits = list(ex.map(lambda c: matcher._is_condition_matched(question, c), conditions))
        matched_count = sum(1 for h in hits if h)
        ratio = matched_count / len(conditions) if conditions else 0.0
        return matched_count, len(conditions), ratio

    matcher.match_conditions = match_conditions


def _parallel_debate(evaluator, eng, workers: int):
    """_debate_evaluation 的并行版:三个循环并发化,其余逐字保留 vendor 逻辑。"""
    import numpy as np
    import random

    def _debate_evaluation(self, node, question, sibling_nodes):
        node_code = node.get("code", "")
        node_name = node.get("name", "")

        all_nodes = [node] + sibling_nodes

        print(f"\n  [Debate] 节点 '{node_name}' 条件无匹配，启动Debate机制...")
        print(f"  [Debate] 参与节点数: {len(all_nodes)}(并行×{workers})")

        # 第一阶段(并行):每个节点进行Debate辩护
        with ThreadPoolExecutor(max_workers=workers) as ex:
            debate_contents = list(ex.map(
                lambda t: eng.DebateAgent(self.llm_client, t[0] + 1).debate(t[1], all_nodes, question),
                enumerate(all_nodes)))
        for i, n in enumerate(all_nodes):
            print(f"  [Debate] Agent {i+1} 为节点 '{n.get('name', '')[:]}...' 辩护完成")

        # 第二阶段(并行):多个Judging Agent独立打分
        with ThreadPoolExecutor(max_workers=workers) as ex:
            all_judging_results = list(ex.map(
                lambda j: eng.JudgingAgent(self.llm_client, j + 1)
                             .evaluate_nodes(all_nodes, question, debate_contents),
                range(self.config.num_judging_agents)))
        for j in range(len(all_judging_results)):
            print(f"  [Debate] Judging Agent {j+1} 评分完成")

        # ---- 以下聚合/惩罚/记录逻辑逐字来自 vendor(仅兄弟 quick check 改并行取数) ----
        node_scores_map = {}
        for scores_dict in all_judging_results:
            for node_idx, score_info in scores_dict.items():
                if node_idx not in node_scores_map:
                    node_scores_map[node_idx] = []
                node_scores_map[node_idx].append(score_info["score"])

        target_node_idx = 1
        for i, n in enumerate(all_nodes):
            if n.get("code") == node_code:
                target_node_idx = i + 1
                break

        if target_node_idx in node_scores_map:
            scores = node_scores_map[target_node_idx]
        else:
            scores = [random.uniform(0.1, 0.3) for _ in range(self.config.num_judging_agents)]

        mean_score = np.mean(scores)
        std_score = np.std(scores)

        sibling_match_penalty = 0.0
        original_debate_score = mean_score - std_score * self.config.variance_penalty_weight

        best_sibling_match = 0.0
        best_sibling_match_type = ""
        with ThreadPoolExecutor(max_workers=workers) as ex:
            sibling_results = list(ex.map(lambda s: self._quick_check_match(s, question), sibling_nodes))
        for sibling_result in sibling_results:
            if sibling_result > best_sibling_match:
                best_sibling_match = sibling_result
                best_sibling_match_type = "full" if sibling_result >= 1.0 else "partial" if sibling_result > 0 else "none"

        if best_sibling_match >= self.config.sibling_match_threshold:
            sibling_match_penalty = best_sibling_match * 0.5
            original_debate_score = original_debate_score * (1.0 - sibling_match_penalty)
            print(f"  [Debate惩罚] 兄弟节点最佳匹配: {best_sibling_match_type}({best_sibling_match:.2f}), 阈值: {self.config.sibling_match_threshold}, 惩罚系数: {sibling_match_penalty:.2f}")

        final_score = original_debate_score * self.config.max_debate_score_ratio * self.config.partial_match_penalty_ratio
        final_score = max(0, final_score)

        debate_record = eng.DebateRecord(
            node_code=node_code,
            node_name=node_name,
            sibling_nodes=sibling_nodes,
            debate_contents=debate_contents,
            judging_scores=[{f"Agent{j+1}": all_judging_results[j]} for j in range(len(all_judging_results))],
            final_score=final_score,
            mean_score=mean_score,
            std_score=std_score,
            variance_penalty=std_score * self.config.variance_penalty_weight,
            sibling_match_penalty=sibling_match_penalty,
            original_debate_score=original_debate_score
        )
        self.debate_records.append(debate_record)

        self._print_debate_visualization(debate_record, all_nodes)

        return eng.NodeScore(
            node_id=node_code,
            node_name=node_name,
            score=final_score,
            match_type="debate",
            match_details={
                "mean": mean_score,
                "std": std_score,
                "variance_penalty": std_score * self.config.variance_penalty_weight,
                "sibling_match_penalty": sibling_match_penalty
            },
            debate_scores=scores,
            judgment_agent_scores=[scores],
            sibling_match_penalty=sibling_match_penalty
        )

    evaluator._debate_evaluation = MethodType(_debate_evaluation, evaluator)


def seed_match_cache(proc, tree, question: str, facts_status: dict) -> tuple:
    """P2a 预播种:把 execute 已判定的节点结论直接写进匹配缓存——匹配层不再让 LLM
    重新发现已知答案(30 场景实测:与'理想字面 LLM 匹配'零分歧,还消除了采样噪声与
    '\"是\" in response' 解析脆弱性)。

    播种规则(=回声策略的确定性收口):held→满配(N,N,1.0);refuted/insufficient→零配
    (0,N,0.0)(它们的判据本就不回声进 question,理想字面匹配即零命中)。
    无事实节点(uncovered)不播,留给 LLM——rca_input 对它们确实没有结论。
    前置:匹配缓存已开(_wrap_match_cache 已挂);vendor match_conditions 源码哈希
    不符(引擎升级)则整体跳过、交回 LLM。返回 (满配数, 零配数)。"""
    matcher = getattr(getattr(proc, "evaluator", None), "condition_matcher", None)
    cache = getattr(matcher, "_boost_match_cache", None)
    if cache is None or not isinstance(tree, dict):
        return 0, 0
    try:
        import fault_tree_evaluator_bfs_advanced as eng
    except ImportError:
        return 0, 0
    if _src_hash(eng.ConditionMatcher.match_conditions) != _HASH_MATCH:
        print("[seed] ⚠ vendor.match_conditions 源码已变(引擎升级?),预播种跳过、匹配交回 LLM")
        return 0, 0

    n_full = n_zero = 0
    stack = [tree]
    while stack:
        node = stack.pop()
        stack.extend(node.get("children") or [])
        conds = node.get("judgment_conditions") or []
        st = facts_status.get(node.get("code"))
        if not conds or conds == ["无条件进入"] or conds == ["N/A"] or st is None:
            continue                       # 无判据/无事实结论的节点不播,行为与原来一致
        key = (question, tuple(conds), False)   # 只播非降级键;降级路径行为不变
        if st == "held":
            cache.setdefault(key, (len(conds), len(conds), 1.0))
            n_full += 1
        elif st in ("refuted", "insufficient"):
            cache.setdefault(key, (0, len(conds), 0.0))
            n_zero += 1
    return n_full, n_zero


def _wrap_debate_memo(eng) -> bool:
    """P2b 辩护记忆化:引擎对 m 人零匹配组的每个成员各开一场辩论,每场都为全组每人**重新**
    生成辩护——同 (节点, 组, question) 的辩护被采样 m 次(实测 -47% 辩护调用可省)。
    与匹配缓存同一论证:同参数重复调用不再重采样,反而更自洽。失败占位串不缓存。
    类级包装(DebateAgent 每次调用都新建实例,无法实例级);哈希守卫,引擎升级自动跳过。"""
    agent_cls = getattr(eng, "DebateAgent", None)        # V2 起拆成 Defense/Attack,旧类没了
    if agent_cls is None or not hasattr(agent_cls, "debate"):
        print("[boost] ⚠ vendor 无 DebateAgent.debate(引擎已升级为防御-攻击三段式?),"
              "辩护记忆化跳过——按 V2 结构重建记忆化是后续提速项")
        return False
    if getattr(agent_cls.debate, "_boost_memo", False) or getattr(agent_cls.debate, "_boost_aq", False):
        return True                       # 已处理过(记忆化已挂;或二次 apply 时它在角色材料包装内层)
    if _src_hash(agent_cls.debate) != _HASH_DEFENSE:
        print("[boost] ⚠ vendor.DebateAgent.debate 源码已变(引擎升级?),辩护记忆化跳过")
        return False
    orig = eng.DebateAgent.debate
    memo, lock = {}, threading.Lock()

    def debate(self, my_node, sibling_nodes, question):
        # 组键按 code 排序:引擎每场辩论把目标节点排在组首(同一组在 m 场辩论里顺序各不同),
        # 有序元组永远对不上;组是集合语义,排序后同组即同键
        key = (my_node.get("code"),
               tuple(sorted(str(n.get("code")) for n in sibling_nodes)), question)
        with lock:
            if key in memo:
                return memo[key]
        r = orig(self, my_node, sibling_nodes, question)
        if r and not r.startswith("[Agent"):   # 失败占位不缓存,下次重试
            with lock:
                memo.setdefault(key, r)
        return r

    debate._boost_memo = True
    eng.DebateAgent.debate = debate
    return True


def _sig_ok(fn, *names) -> bool:
    """方法还在、且前几个参数名与预期一致才允许包装(引擎升级改签名→安全跳过)。"""
    if fn is None:
        return False
    try:
        return tuple(inspect.signature(fn).parameters)[:len(names)] == names
    except (ValueError, TypeError):
        return False


def _wrap_agent_questions(eng) -> list:
    """辩论侧按角色换材料:只把传给各 agent 的 question 参数,换成 DossierKit 出的分层角色材料
    (角色任务框架+本组节点档案+全局案情;**信息全员对称**,判据原文全员必读——含被证伪/
    缺数节点的,附实测与反证。设计见 facts_to_question.DossierKit)。agent 的提示词与算法
    零改动;机械匹配器不在此列,继续用回声安全版 question(防误配规则不变)。
    kit 从 eng._boost_kit 现取(同进程重复 apply 只换 kit,不叠包装;kit=None 即回退原样);
    类/方法/签名对不上的逐项跳过——引擎再升级,安全退化为原 question。"""
    wrapped = []                                         # 报"生效角色"(新包/已包都算):
                                                         # 同进程二次 apply 时包装已在,不能误报为失效
    def _kit():
        return getattr(eng, "_boost_kit", None)

    de = getattr(eng, "DefenseAgent", None)
    if de and _sig_ok(getattr(de, "defend", None), "self", "my_node", "sibling_nodes", "question") \
            and not getattr(de.defend, "_boost_aq", False):
        _o1 = de.defend

        def defend(self, my_node, sibling_nodes, question):
            k = _kit()
            if k is not None:
                question = k.for_defense(my_node, sibling_nodes)
            return _o1(self, my_node, sibling_nodes, question)

        defend._boost_aq = True
        de.defend = defend
    if de and getattr(getattr(de, "defend", None), "_boost_aq", False):
        wrapped.append("辩护")
    if de and _sig_ok(getattr(de, "respond", None), "self", "my_node", "question", "attack_content") \
            and not getattr(de.respond, "_boost_aq", False):
        _o2 = de.respond

        def respond(self, my_node, question, attack_content):
            k = _kit()
            if k is not None:
                question = k.for_respond(my_node)
            return _o2(self, my_node, question, attack_content)

        respond._boost_aq = True
        de.respond = respond
    if de and getattr(getattr(de, "respond", None), "_boost_aq", False):
        wrapped.append("回应")
    at = getattr(eng, "AttackAgent", None)
    if at and _sig_ok(getattr(at, "attack", None), "self", "target_node", "question", "defense_content") \
            and not getattr(at.attack, "_boost_aq", False):
        _o3 = at.attack

        def attack(self, target_node, question, defense_content):
            k = _kit()
            if k is not None:
                question = k.for_attack(target_node)
            return _o3(self, target_node, question, defense_content)

        attack._boost_aq = True
        at.attack = attack
    if at and getattr(getattr(at, "attack", None), "_boost_aq", False):
        wrapped.append("攻击")
    cc = getattr(eng, "ConsistencyChecker", None)
    if cc and _sig_ok(getattr(cc, "check_consistency", None),
                      "self", "defense_content", "conditions", "question") \
            and not getattr(cc.check_consistency, "_boost_aq", False):
        _o4 = cc.check_consistency

        def check_consistency(self, defense_content, conditions, question):
            k = _kit()
            if k is not None:
                question = k.for_consistency()
            return _o4(self, defense_content, conditions, question)

        check_consistency._boost_aq = True
        cc.check_consistency = check_consistency
    if cc and getattr(getattr(cc, "check_consistency", None), "_boost_aq", False):
        wrapped.append("一致性")
    ju = getattr(eng, "JudgingAgent", None)
    if ju and _sig_ok(getattr(ju, "evaluate_nodes", None), "self", "nodes", "question") \
            and not getattr(ju.evaluate_nodes, "_boost_aq", False):
        _o5 = ju.evaluate_nodes

        def evaluate_nodes(self, nodes, question, debate_contents=None):
            k = _kit()
            if k is not None:
                question = k.for_judge(nodes)
            return _o5(self, nodes, question, debate_contents)

        evaluate_nodes._boost_aq = True
        ju.evaluate_nodes = evaluate_nodes
    if ju and getattr(getattr(ju, "evaluate_nodes", None), "_boost_aq", False):
        wrapped.append("评委")
    v1 = getattr(eng, "DebateAgent", None)                # V1 引擎的辩护角色也认(同一份材料)
    if v1 and _sig_ok(getattr(v1, "debate", None), "self", "my_node", "sibling_nodes", "question") \
            and not getattr(v1.debate, "_boost_aq", False):
        _o6 = v1.debate

        def debate(self, my_node, sibling_nodes, question):
            k = _kit()
            if k is not None:
                question = k.for_defense(my_node, sibling_nodes)
            return _o6(self, my_node, sibling_nodes, question)

        debate._boost_aq = True
        v1.debate = debate
    if v1 and getattr(getattr(v1, "debate", None), "_boost_aq", False):
        wrapped.append("辩护(V1)")
    return wrapped


def apply(proc, parallel: int = 1, match_cache: bool = True, debate_cache: bool = True,
          agent_kit=None) -> list:
    """给真引擎实例打加速补丁。形状不符(mock/bridge/新版结构变化)时逐项安全跳过。
    agent_kit=facts_to_question.DossierKit 实例 → 辩论侧各角色的 question 换分层角色材料;
    None=不换(或显式关闭,已包装过的类自动回退原 question)。"""
    ev = getattr(proc, "evaluator", None)
    matcher = getattr(ev, "condition_matcher", None)
    client = getattr(ev, "llm_client", None)
    if ev is None or matcher is None or client is None:
        return []  # mock/bridge:无此结构,整体跳过

    try:
        import fault_tree_evaluator_bfs_advanced as eng
    except ImportError:
        return []
    # 严格同类才打补丁:bridge/子类可能覆写了这些方法,基类拷贝会把覆写静默顶掉
    if type(ev) is not eng.FaultTreeEvaluator or type(matcher) is not eng.ConditionMatcher:
        print("[boost] 引擎实例非原生类型(bridge/子类?),加速补丁整体跳过、保持原行为")
        return []

    applied = []
    _wrap_chat_failcounter(client)   # 恒开:失败计数是缓存防污染与瞬时失败报告的地基
    if parallel and parallel > 1:
        if _src_hash(eng.ConditionMatcher.match_conditions) == _HASH_MATCH:
            _parallel_match(matcher, parallel)
            applied.append(f"匹配并行×{parallel}")
        else:
            print("[boost] ⚠ vendor.match_conditions 源码已变(引擎升级?),匹配并行补丁跳过、保持串行")
        if _src_hash(eng.FaultTreeEvaluator._debate_evaluation) == _HASH_DEBATE:
            _parallel_debate(ev, eng, parallel)
            applied.append(f"辩论并行×{parallel}")
        else:
            print("[boost] ⚠ vendor._debate_evaluation 源码已变(引擎升级?),辩论并行补丁跳过、保持串行")
    if match_cache:
        _wrap_match_cache(matcher, client)   # 包在(可能已并行的)match_conditions 外层:先查缓存再并行
        applied.append("匹配缓存(带失败防污染)")
    if debate_cache and _wrap_debate_memo(eng):
        applied.append("辩护记忆化")
    eng._boost_kit = agent_kit               # 显式记录(None=关闭,已包装的类自动回退原 question)
    if agent_kit is not None:
        w = _wrap_agent_questions(eng)
        if w:
            applied.append("角色卷宗(" + "+".join(w) + ")")
        else:
            print("[boost] ⚠ 角色卷宗一个角色都没接上(引擎类名/签名全对不上?)——辩论仍用原 question,"
                  "审计字段会如实记 False")
    if parallel and parallel > 1:
        _wrap_post_adaptive(eng, parallel)   # HTTP 层自适应并发限制:429/503 减半,连续成功回升
        applied.append(f"自适应并发≤{parallel}(429 降档/恢复回升)")
    if applied:
        print(f"[boost] 加速补丁已生效:{' + '.join(applied)}(vendor 原件零改动,评分公式逐字保留)")
    return applied
