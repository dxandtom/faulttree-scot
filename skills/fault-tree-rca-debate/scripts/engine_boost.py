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
_HASH_DEBATE = "3015fe469f70617c57918e6ee03154cf44808fd5fda1c526f64091e725d3f6b2"


def _src_hash(fn) -> str:
    try:
        return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()
    except Exception:
        return ""


def _wrap_chat_semaphore(client, n: int):
    """并发封顶:所有 LLM 调用(含 shield 的外层重试)同时在飞的不超过 n。"""
    sem = threading.Semaphore(n)
    orig = client.chat

    def chat(messages, temperature=None):
        with sem:
            return orig(messages, temperature)

    client.chat = chat


def _wrap_match_cache(matcher):
    """实例级记忆化:key=(question, 判据元组, 当前 fallback 态)。线程安全。"""
    orig = matcher.match_conditions
    cache, lock = {}, threading.Lock()

    def cached(question, conditions):
        key = (question, tuple(conditions or ()), bool(getattr(matcher, "use_fallback", False)))
        with lock:
            if key in cache:
                return cache[key]
        result = orig(question, conditions)
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


def apply(proc, parallel: int = 1, match_cache: bool = True) -> None:
    """给真引擎实例打加速补丁。形状不符(mock/bridge/新版结构变化)时逐项安全跳过。"""
    ev = getattr(proc, "evaluator", None)
    matcher = getattr(ev, "condition_matcher", None)
    client = getattr(ev, "llm_client", None)
    if ev is None or matcher is None or client is None:
        return  # mock/bridge:无此结构,整体跳过

    try:
        import fault_tree_evaluator_bfs_advanced as eng
    except ImportError:
        return
    if not isinstance(ev, eng.FaultTreeEvaluator):
        return

    applied = []
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
        _wrap_match_cache(matcher)   # 包在(可能已并行的)match_conditions 外层:先查缓存再并行
        applied.append("匹配缓存")
    if parallel and parallel > 1:
        _wrap_chat_semaphore(client, parallel)   # 最外层:全局并发封顶,嵌套并行不超发
        applied.append(f"并发封顶{parallel}")
    if applied:
        print(f"[boost] 加速补丁已生效:{' + '.join(applied)}(vendor 原件零改动,评分公式逐字保留)")
