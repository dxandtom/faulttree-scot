"""
故障树评估主程序 - 增强版
基于PPT中的评估方案，对故障树节点进行评估和打分
采用层序遍历（BFS）方式对故障树进行遍历
新增功能：
1. Softmax概率计算 - 将兄弟节点得分转换为概率分布
2. 比例计算 - 当前节点分数 / 兄弟节点分数总和
3. 三个得分 - 原始得分、softmax概率、比例得分
4. Debate惩罚机制 - 当兄弟节点匹配良好时，Debate节点得低分
"""

import os
import re
import yaml
import json
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque
import requests
import random
import urllib3
import math
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class EvaluationConfig:
    """评估配置"""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model_name: str = "gpt-4o-mini"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2000

    top_k: int = 3
    partial_match_penalty_ratio: float = 1.0
    num_debate_agents: int = 3
    num_judging_agents: int = 3
    variance_penalty_weight: float = 0.3
    max_debate_score_ratio: float = 0.5
    sibling_match_threshold: float = 0.5  # 兄弟节点良好匹配阈值

    # API重试配置
    max_retries: int = 3
    retry_delay: float = 1.0


@dataclass
class NodeScore:
    """节点评分结果"""
    node_id: str
    node_name: str
    score: float  # 原始得分
    match_type: str  # "full", "partial", "debate"
    softmax_prob: float = 0.0  # Softmax概率
    ratio_score: float = 0.0  # 比例得分
    match_details: Dict[str, Any] = field(default_factory=dict)
    debate_scores: List[float] = field(default_factory=list)
    judgment_agent_scores: List[List[float]] = field(default_factory=list)
    sibling_match_penalty: float = 0.0  # 兄弟节点匹配惩罚


@dataclass
class DebateRecord:
    """Debate过程记录"""
    node_code: str
    node_name: str
    sibling_nodes: List[Dict]
    debate_contents: List[str] = field(default_factory=list)
    judging_scores: List[Dict] = field(default_factory=list)
    final_score: float = 0.0
    mean_score: float = 0.0
    std_score: float = 0.0
    variance_penalty: float = 0.0
    sibling_match_penalty: float = 0.0
    original_debate_score: float = 0.0


@dataclass
class FaultTreeNode:
    """故障树节点"""
    code: str
    name: str
    chinese_name: str
    level: int
    judgment_conditions: List[str] = field(default_factory=list)
    children: List['FaultTreeNode'] = field(default_factory=list)
    parent: Optional['FaultTreeNode'] = None


def softmax(scores: List[float], temperature: float = 1.0) -> List[float]:
    """
    计算Softmax概率分布
    scores: 原始得分列表
    temperature: 温度参数，默认1.0
    返回: 概率分布列表
    """
    if not scores:
        return []
    # 减去最大值以提高数值稳定性
    max_score = max(scores)
    exp_scores = [math.exp((s - max_score) / temperature) for s in scores]
    sum_exp = sum(exp_scores)
    if sum_exp == 0:
        return [1.0 / len(scores)] * len(scores)
    return [e / sum_exp for e in exp_scores]


class LLMClient:
    """LLM客户端"""

    def __init__(self, config: EvaluationConfig):
        self.config = config
        self.url = f"{config.llm_base_url}/messages"
        self.api_key = config.llm_api_key
        self.connection_failed = False
        self.max_retries = config.max_retries
        self.retry_delay = config.retry_delay

    def chat(self, messages: List[Dict], temperature: Optional[float] = None) -> str:
        """发送聊天请求，带重试机制"""
        import time

        last_error = None
        for attempt in range(self.max_retries):
            try:
                headers = {
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01"
                }

                processed_messages = []
                system_content = ""

                for msg in messages:
                    if msg.get("role") == "system":
                        system_content += msg.get("content", "") + "\n"
                    else:
                        processed_messages.append(msg)

                if system_content and processed_messages:
                    first_msg = processed_messages[0]
                    first_msg["content"] = system_content + first_msg.get("content", "")
                elif system_content:
                    processed_messages = [{"role": "user", "content": system_content}]

                data = {
                    "model": self.config.llm_model_name,
                    "stream": False,
                    "max_tokens": self.config.llm_max_tokens,
                    "messages": processed_messages,
                    "temperature": temperature or self.config.llm_temperature,
                    "top_p": 0.98
                }

                response = requests.post(self.url, json=data, headers=headers, verify=False, timeout=180)
                response.raise_for_status()
                result = response.json()

                if "content" in result and len(result["content"]) > 0:
                    for item in result["content"]:
                        if item.get("type") == "text":
                            return item.get("text", "")
                    for item in result["content"]:
                        if "text" in item:
                            return item["text"]
                elif "choices" in result:
                    return result["choices"][0].get("message", {}).get("content", "")
                else:
                    return str(result)

            except requests.exceptions.Timeout as e:
                last_error = f"请求超时: {e}"
                print(f"LLM调用超时 (尝试 {attempt + 1}/{self.max_retries})")
            except requests.exceptions.ConnectionError as e:
                last_error = f"连接错误: {e}"
                print(f"LLM连接失败 (尝试 {attempt + 1}/{self.max_retries})")
            except Exception as e:
                last_error = str(e)
                print(f"LLM调用失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")

            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay * (attempt + 1))

        self.connection_failed = True
        print(f"LLM调用最终失败: {last_error}")
        return ""

    def parse_conditions(self, text: str) -> List[str]:
        """从判断条件文本中解析出各个条件项"""
        text = text.strip()
        lines = text.split('\n')
        conditions = []

        for line in lines:
            line = line.strip()
            match = re.match(r'^\d+[.、]\s*(.+)$', line)
            if match:
                conditions.append(match.group(1).strip())
            elif line.startswith(("如果", "当", "检查", "确认", "满足")):
                conditions.append(line)

        return conditions if conditions else [text]


class ConditionMatcher:
    """条件匹配器"""

    def __init__(self, llm_client: LLMClient, use_fallback: bool = False):
        self.llm_client = llm_client
        self.use_fallback = use_fallback

    def match_conditions(self, question: str, conditions: List[str]) -> Tuple[int, int, float]:
        """匹配问题与条件列表，返回: (匹配数量, 条件总数, 匹配比例)"""
        if not conditions:
            return 0, 0, 0.0

        matched_count = 0
        for condition in conditions:
            if self._is_condition_matched(question, condition):
                matched_count += 1

        ratio = matched_count / len(conditions) if conditions else 0.0
        return matched_count, len(conditions), ratio

    def _is_condition_matched(self, question: str, condition: str) -> bool:
        if self.use_fallback:
            return self._keyword_match(question, condition)

        prompt = f"""判断用户问题是否匹配给定的条件。

用户问题: {question}

条件: {condition}

请只回答一个字：
- 如果匹配，回答"是"
- 如果不匹配，回答"否"
不要添加任何其他文字。"""

        response = self.llm_client.chat([
            {"role": "system", "content": "你是一个专业的故障诊断助手。"},
            {"role": "user", "content": prompt}
        ])

        if response is None or response == "":
            return self._keyword_match(question, condition)

        return "是" in response

    def _keyword_match(self, question: str, condition: str) -> bool:
        question_lower = question.lower()
        condition_lower = condition.lower()

        stop_words = ['检查', '确认', '满足', '如果', '当', '条件', '以下', '之一', '一个']
        condition_clean = condition_lower
        for word in stop_words:
            condition_clean = condition_clean.replace(word, '')

        condition_words = condition_clean.split()
        match_count = sum(1 for word in condition_words if len(word) > 2 and word in question_lower)

        return match_count >= len(condition_words) / 2 if condition_words else False


class DebateAgent:
    """Debate Agent - 为自己辩护并反驳其他节点"""

    def __init__(self, llm_client: LLMClient, agent_id: int):
        self.llm_client = llm_client
        self.agent_id = agent_id

    def debate(self, my_node: Dict, sibling_nodes: List[Dict], question: str) -> str:
        my_name = my_node.get("name", "")
        my_conditions = my_node.get("judgment_conditions", [])

        sibling_info = []
        for i, sibling in enumerate(sibling_nodes):
            if sibling.get("code") != my_node.get("code"):
                sibling_info.append({
                    "index": i + 1,
                    "name": sibling.get("name", ""),
                    "conditions": sibling.get("judgment_conditions", [])
                })

        sibling_conditions = []
        for j in sibling_info[:]:
            sibling_conditions.append(f"节点{j['index']}: {j['name']}\n   条件: " + "\n   条件: ".join(j['conditions'][:]))

        prompt = f"""你是一个专业的故障诊断专家。请为以下节点进行辩护。

【你的角色】你专门负责为节点"{my_name}"进行辩护。

【用户问题】
{question}

【你的节点的判断条件】
{chr(10).join([f"{i+1}. {c}" for i, c in enumerate(my_conditions)])}

【其他兄弟节点】
{chr(10).join(sibling_conditions) if sibling_conditions else "无"}

【任务】
1. 论证为什么你的节点才是正确根因
2. 反驳其他节点，说明它们为什么不是正确根因

【回复格式】（直接回复，不要加前缀说明）
【辩护】：你的节点为什么是正确根因
【反驳】：其他节点为什么不正确

请用中文简洁回复。"""

        response = self.llm_client.chat([
            {"role": "system", "content": "你是一个专业、客观的故障诊断专家。"},
            {"role": "user", "content": prompt}
        ])

        return response if response else f"[Agent {self.agent_id} 辩护内容：无法获取回复]"


class JudgingAgent:
    """Judging Agent - 对所有节点进行独立打分"""

    def __init__(self, llm_client: LLMClient, agent_id: int):
        self.llm_client = llm_client
        self.agent_id = agent_id

    def evaluate_nodes(self, nodes: List[Dict], question: str, debate_results: List[str] = None) -> Dict:
        node_info = []
        for i, node in enumerate(nodes):
            conditions = node.get("judgment_conditions", [])
            node_info.append(f"""节点{i+1}: {node.get('name', '')}
判断条件: {chr(10).join(conditions[:])}""")

        debate_context = ""
        if debate_results:
            debate_context = "\n\n【Debate结果摘要】\n" + "\n".join([f"Agent {j+1}: {r[:]}..." for j, r in enumerate(debate_results)])

        prompt = f"""你是一个公正的故障诊断评判专家。请对以下候选节点进行独立打分。

【用户问题】
{question}

【候选节点列表】
{chr(10).join(node_info)}
{debate_context}

【打分规则】
- 分数范围: 0.0 到 1.0
- 分数越高表示该节点越有可能是根因
- 必须为每个候选节点打分（不允许省略任何节点）

【输出要求】
- 必须返回标准JSON格式
- 不要在JSON前后添加任何文字说明
- 节点索引从1开始

【回复格式】（严格按照此格式）
{{
    "scores": [
        {{"node_index": 1, "score": 0.85, "reason": "匹配度高，辩护有力"}},
        {{"node_index": 2, "score": 0.30, "reason": "匹配度低，辩护较弱"}}
    ]
}}"""

        response = self.llm_client.chat([
            {"role": "system", "content": "你是一个专业、公正的故障诊断评判专家。"},
            {"role": "user", "content": prompt}
        ])

        try:
            json_match = re.search(r'\{.*"scores".*\}', response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                scores = {int(item["node_index"]): {"score": item["score"], "reason": item.get("reason", "")} for item in result["scores"]}
                return scores
        except Exception as e:
            print(f"解析评判结果失败: {e}")

        return {i + 1: {"score": 1.0 / len(nodes), "reason": "默认分数"} for i in range(len(nodes))}


class FaultTreeEvaluator:
    """故障树评估器"""

    def __init__(self, config: EvaluationConfig):
        self.config = config
        self.llm_client = LLMClient(config)
        self.use_fallback = False
        self.condition_matcher = ConditionMatcher(self.llm_client)
        self.debate_records: List[DebateRecord] = []

    def evaluate_node(self, node: Dict, question: str, sibling_nodes: List[Dict] = None) -> NodeScore:
        """评估单个节点"""
        if sibling_nodes is None:
            sibling_nodes = node.get("sibling_nodes", [])

        if self.llm_client.connection_failed and not self.use_fallback:
            print("\n检测到API连接失败，自动切换到fallback模式（基于关键词匹配）...")
            self.use_fallback = True
            self.condition_matcher.use_fallback = True

        node_code = node.get("code", "")
        node_name = node.get("name", "")
        conditions = node.get("judgment_conditions", [])

        if not conditions or conditions == ["无条件进入"] or conditions == ["N/A"]:
            return NodeScore(
                node_id=node_code,
                node_name=node_name,
                score=1.0,
                match_type="full",
                match_details={"reason": "无条件进入"}
            )

        matched_count, total_count, ratio = self.condition_matcher.match_conditions(question, conditions)

        if ratio == 1.0:
            return NodeScore(
                node_id=node_code,
                node_name=node_name,
                score=1.0,
                match_type="full",
                match_details={"matched": matched_count, "total": total_count}
            )
        elif ratio > 0:
            score = ratio * self.config.partial_match_penalty_ratio
            return NodeScore(
                node_id=node_code,
                node_name=node_name,
                score=score,
                match_type="partial",
                match_details={"matched": matched_count, "total": total_count, "ratio": ratio}
            )
        else:
            return self._debate_evaluation(node, question, sibling_nodes)

    def _debate_evaluation(self, node: Dict, question: str, sibling_nodes: List[Dict]) -> NodeScore:
        """使用debate机制进行评估"""
        node_code = node.get("code", "")
        node_name = node.get("name", "")

        all_nodes = [node] + sibling_nodes

        print(f"\n  [Debate] 节点 '{node_name}' 条件无匹配，启动Debate机制...")
        print(f"  [Debate] 参与节点数: {len(all_nodes)}")

        # 第一阶段：每个节点进行Debate辩护
        debate_contents = []
        for i, n in enumerate(all_nodes):
            agent = DebateAgent(self.llm_client, i + 1)
            content = agent.debate(n, all_nodes, question)
            debate_contents.append(content)
            print(f"  [Debate] Agent {i+1} 为节点 '{n.get('name', '')[:]}...' 辩护完成")

        # 第二阶段：多个Judging Agent独立打分
        all_judging_results = []
        for j in range(self.config.num_judging_agents):
            judging_agent = JudgingAgent(self.llm_client, j + 1)
            scores_dict = judging_agent.evaluate_nodes(all_nodes, question, debate_contents)
            all_judging_results.append(scores_dict)
            print(f"  [Debate] Judging Agent {j+1} 评分完成")

        # 汇总评分结果
        node_scores_map = {}
        for scores_dict in all_judging_results:
            for node_idx, score_info in scores_dict.items():
                if node_idx not in node_scores_map:
                    node_scores_map[node_idx] = []
                node_scores_map[node_idx].append(score_info["score"])

        # 计算目标节点的最终得分
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

        # 检查兄弟节点匹配情况，用于惩罚
        sibling_match_penalty = 0.0
        original_debate_score = mean_score - std_score * self.config.variance_penalty_weight

        best_sibling_match = 0.0
        best_sibling_match_type = ""
        for sibling in sibling_nodes:
            sibling_result = self._quick_check_match(sibling, question)
            if sibling_result > best_sibling_match:
                best_sibling_match = sibling_result
                best_sibling_match_type = "full" if sibling_result >= 1.0 else "partial" if sibling_result > 0 else "none"

        # 只有当兄弟节点匹配度超过阈值时才进行惩罚（良好匹配阈值）
        if best_sibling_match >= self.config.sibling_match_threshold:
            sibling_match_penalty = best_sibling_match * 0.5
            original_debate_score = original_debate_score * (1.0 - sibling_match_penalty)
            print(f"  [Debate惩罚] 兄弟节点最佳匹配: {best_sibling_match_type}({best_sibling_match:.2f}), 阈值: {self.config.sibling_match_threshold}, 惩罚系数: {sibling_match_penalty:.2f}")

        final_score = original_debate_score * self.config.max_debate_score_ratio * self.config.partial_match_penalty_ratio
        final_score = max(0, final_score)

        debate_record = DebateRecord(
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

        return NodeScore(
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

    def _quick_check_match(self, node: Dict, question: str) -> float:
        """快速检查节点匹配程度"""
        conditions = node.get("judgment_conditions", [])
        if not conditions or conditions == ["无条件进入"] or conditions == ["N/A"]:
            return 1.0

        matched_count, total_count, ratio = self.condition_matcher.match_conditions(question, conditions)

        if ratio == 1.0:
            return 1.0
        elif ratio > 0:
            return ratio * self.config.partial_match_penalty_ratio
        else:
            return 0.0

    def _print_debate_visualization(self, record: DebateRecord, all_nodes: List[Dict]):
        """打印Debate过程可视化"""
        print(f"\n  {'='*60}")
        print(f"  [Debate可视化] 节点: {record.node_name}")
        print(f"  {'='*60}")

        print(f"\n  【Debate辩护内容】")
        for i, content in enumerate(record.debate_contents):
            node_name = all_nodes[i].get("name", "")[:] if i < len(all_nodes) else f"Node {i+1}"
            print(f"  Agent {i+1} (为 '{node_name}...' 辩护):")
            print(f"    {content[:150]}..." if len(content) > 150 else f"    {content}")
            print()

        print(f"\n  【Judging Agent评分详情】")
        for i, judging_score in enumerate(record.judging_scores):
            for node_idx_str, scores in judging_score.items():
                if isinstance(scores, dict):
                    print(f"  {node_idx_str}:")
                    for idx, score_info in scores.items():
                        node_name = all_nodes[idx-1].get("name", "")[:] if idx <= len(all_nodes) else f"Node {idx}"
                        print(f"    节点{idx}('{node_name}...'): {score_info.get('score', 0):.3f} - {score_info.get('reason', '')[:]}")

        print(f"\n  【汇总得分】")
        print(f"    均值: {record.mean_score:.4f}")
        print(f"    标准差: {record.std_score:.4f}")
        print(f"    方差惩罚: -{record.variance_penalty:.4f}")
        if record.sibling_match_penalty > 0:
            print(f"    兄弟匹配惩罚: -{record.sibling_match_penalty:.4f}")
            print(f"    原始Debate得分: {record.original_debate_score:.4f}")
        print(f"    最终得分: {record.final_score:.4f}")
        print(f"  {'='*60}\n")

    def evaluate_children(self, children: List[Dict], question: str) -> List[NodeScore]:
        """评估所有子节点"""
        results = []
        for child in children:
            score = self.evaluate_node(child, question)
            results.append(score)
        return results

    def calculate_sibling_scores(self, node_scores: List[NodeScore]) -> List[NodeScore]:
        """
        为兄弟节点计算Softmax概率和比例得分
        node_scores: 同一父节点下的所有子节点评分列表
        """
        if len(node_scores) <= 1:
            for ns in node_scores:
                ns.softmax_prob = 1.0
                ns.ratio_score = 1.0
            return node_scores

        original_scores = [ns.score for ns in node_scores]
        softmax_probs = softmax(original_scores)
        total_score = sum(original_scores)
        ratio_scores = [s / total_score if total_score > 0 else 0.0 for s in original_scores]

        for i, ns in enumerate(node_scores):
            ns.softmax_prob = softmax_probs[i]
            ns.ratio_score = ratio_scores[i]

        return node_scores


class FaultTreeProcessor:
    """故障树处理器"""

    def __init__(self, config: EvaluationConfig):
        self.config = config
        self.evaluator = FaultTreeEvaluator(config)
        self.visited_nodes = set()

    def load_fault_tree(self, filepath: str) -> pd.DataFrame:
        """加载故障树数据"""
        df = pd.read_excel(filepath)
        return df

    def build_tree_structure(self, df: pd.DataFrame) -> Dict:
        """构建树结构"""
        root = {
            "code": df.iloc[0]["L1 Code"],
            "name": df.iloc[0]["L1"],
            "chinese_name": df.iloc[0]["L1(Chinese)"],
            "level": 1,
            "children": []
        }

        l2_groups = df.groupby("L2 Code")

        for l2_code, group in l2_groups:
            l2_node = {
                "code": l2_code,
                "name": group.iloc[0]["L2"],
                "chinese_name": group.iloc[0]["L2(Chinese)"],
                "level": 2,
                "judgment_conditions": self._extract_conditions(group),
                "children": []
            }

            l3_groups = group.groupby("L3 Code")
            for l3_code, l3_group in l3_groups:
                l3_node = {
                    "code": l3_code,
                    "name": l3_group.iloc[0]["L3"],
                    "chinese_name": l3_group.iloc[0]["L3(Chinese)"],
                    "level": 3,
                    "judgment_conditions": self._extract_conditions(l3_group),
                    "children": []
                }

                l3_node["children"] = self._build_deeper_levels(l3_group, 4)
                l2_node["children"].append(l3_node)

            root["children"].append(l2_node)

        return root

    def _build_deeper_levels(self, group: pd.DataFrame, level: int) -> List[Dict]:
        """构建更深层级的节点"""
        if level > 6:
            return []

        level_code_col = f"L{level} Code"
        level_name_col = f"L{level}"
        level_ch_col = f"L{level}(Chinese)"

        if level_code_col not in group.columns:
            return []

        nodes = []
        level_groups = group.groupby(level_code_col)

        for code, sub_group in level_groups:
            if pd.isna(code) or code == "":
                continue

            node = {
                "code": code,
                "name": sub_group.iloc[0][level_name_col] if pd.notna(sub_group.iloc[0][level_name_col]) else "",
                "chinese_name": sub_group.iloc[0][level_ch_col] if pd.notna(sub_group.iloc[0][level_ch_col]) else "",
                "level": level,
                "judgment_conditions": self._extract_conditions(sub_group),
                "children": self._build_deeper_levels(sub_group, level + 1)
            }
            nodes.append(node)

        for node in nodes:
            node["sibling_nodes"] = [n for n in nodes if n["code"] != node["code"]]

        return nodes

    def _extract_conditions(self, group: pd.DataFrame) -> List[str]:
        """从DataFrame组中提取判断条件"""
        if "Judgment Condition(Chinese)" not in group.columns:
            return []

        conditions_text = group.iloc[0]["Judgment Condition(Chinese)"]
        if pd.isna(conditions_text) or conditions_text == "":
            return []

        lines = str(conditions_text).split('\n')
        conditions = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            match = re.match(r'^\d+[.、:：]\s*(.+)$', line)
            if match:
                conditions.append(match.group(1).strip())
            elif line.startswith(("如果", "当", "满足", "检查", "确认")):
                conditions.append(line)

        return conditions if conditions else [str(conditions_text)]

    def bfs_evaluate(self, root: Dict, question: str) -> List[List[Dict]]:
        """
        层序遍历（BFS）评估故障树
        返回所有完整路径（从根到叶）
        """
        if not root:
            return []

        all_paths = []
        # 用于存储每个节点的评分，按父节点分组
        # key: parent_code, value: list of (node_code, node_score)
        sibling_groups: Dict[str, List[Tuple[str, NodeScore]]] = {}
        # 节点评分映射：node_code -> NodeScore
        node_score_map: Dict[str, NodeScore] = {}

        queue = deque([(root, [])])

        while queue:
            node, current_path = queue.popleft()
            node_code = node.get("code", "")

            sibling_nodes = node.get("sibling_nodes", [])
            node_score = self.evaluator.evaluate_node(node, question, sibling_nodes)
            node_score_map[node_code] = node_score

            # 记录兄弟节点分组
            parent_code = current_path[-1]["code"] if current_path else "root"
            if parent_code not in sibling_groups:
                sibling_groups[parent_code] = []
            sibling_groups[parent_code].append((node_code, node_score))

            current_score = (current_path[-1]["path_score"] if current_path else 1.0) * node_score.score

            path_info = {
                "code": node["code"],
                "name": node["name"],
                "chinese_name": node["chinese_name"],
                "level": node["level"],
                "node_score": node_score.score,
                "path_score": current_score,
                "match_type": node_score.match_type,
                "parent_code": parent_code
            }

            path_with_current = current_path + [path_info]

            if not node.get("children"):
                all_paths.append(path_with_current)
            else:
                for child in node["children"]:
                    queue.append((child, path_with_current))

        # 对每个兄弟组计算Softmax概率和比例得分
        for parent_code, score_list in sibling_groups.items():
            node_scores = [item[1] for item in score_list]
            if len(node_scores) > 1:
                self.evaluator.calculate_sibling_scores(node_scores)
            elif len(node_scores) == 1:
                # 没有兄弟节点时，默认值为1.0
                node_scores[0].softmax_prob = 1.0
                node_scores[0].ratio_score = 1.0
            # 更新node_score_map
            for node_code, ns in score_list:
                node_score_map[node_code] = ns

        # 更新路径中的softmax_prob和ratio_score，并计算三种路径得分
        for path in all_paths:
            original_path_score = 1.0
            softmax_path_score = 1.0
            ratio_path_score = 1.0

            for node_info in path:
                node_code = node_info["code"]
                if node_code in node_score_map:
                    ns = node_score_map[node_code]
                    node_info["softmax_prob"] = ns.softmax_prob
                    node_info["ratio_score"] = ns.ratio_score
                    original_path_score *= node_info["node_score"]
                    softmax_path_score *= ns.softmax_prob
                    ratio_path_score *= ns.ratio_score
                else:
                    original_path_score *= node_info["node_score"]
                    softmax_path_score *= 1.0  # 默认概率
                    ratio_path_score *= 1.0  # 默认比例

            # 添加三种路径得分到路径信息中
            path_info = {
                "original_path_score": original_path_score,
                "softmax_path_score": softmax_path_score,
                "ratio_path_score": ratio_path_score
            }
            path.append(path_info)

        return all_paths

    def find_top_k_paths(self, root: Dict, question: str, k: int = 3) -> Tuple[List[Dict], List[Dict], List[Dict], List[DebateRecord]]:
        """
        找到得分最高的k条根因路径
        返回: (原始得分top_k, softmax概率top_k, 比例得分top_k, Debate记录)
        """
        self.visited_nodes.clear()
        all_paths = self.bfs_evaluate(root, question)

        # 三种排序方式
        sorted_by_original = sorted(all_paths, key=lambda x: x[-1].get("original_path_score", 0) if isinstance(x[-1], dict) and "original_path_score" in x[-1] else 0, reverse=True)
        sorted_by_softmax = sorted(all_paths, key=lambda x: x[-1].get("softmax_path_score", 0) if isinstance(x[-1], dict) and "softmax_path_score" in x[-1] else 0, reverse=True)
        sorted_by_ratio = sorted(all_paths, key=lambda x: x[-1].get("ratio_path_score", 0) if isinstance(x[-1], dict) and "ratio_path_score" in x[-1] else 0, reverse=True)

        # 分别取top_k
        top_k_original = sorted_by_original[:k]
        top_k_softmax = sorted_by_softmax[:k]
        top_k_ratio = sorted_by_ratio[:k]

        return top_k_original, top_k_softmax, top_k_ratio, self.evaluator.debate_records


def save_results(results: Dict, output_dir: str):
    """保存评估结果"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    result_file = os.path.join(output_dir, f"evaluation_result_{timestamp}.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    summary_file = os.path.join(output_dir, f"summary_{timestamp}.md")
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("# 故障树评估结果摘要\n\n")
        f.write(f"评估时间: {results.get('timestamp', '')}\n\n")
        f.write(f"问题: {results.get('question', '')}\n\n")

        # 原始得分排序的Top-K
        f.write("## Top-K 根因路径（按原始得分排序）\n\n")
        for i, path_entry in enumerate(results.get("top_k_paths_original", []), 1):
            path = path_entry if isinstance(path_entry, list) else path_entry
            path_score = path[-1].get("original_path_score", 0) if isinstance(path[-1], dict) else 0
            f.write(f"### 路径 {i}\n")
            f.write(f"- 路径得分: {path_score:.4f}\n")
            if path:
                f.write(f"- 路径节点数: {len(path) - 1}\n")  # -1 因为最后一项是score_info
                leaf_node = path[-2] if len(path) > 1 else path[-1]  # 获取叶子节点（score_info前一项）
                f.write(f"- 叶节点: {leaf_node.get('name', '')} ({leaf_node.get('code', '')})\n")
                f.write(f"- 叶节点原始得分: {leaf_node.get('node_score', 0):.4f}\n")
                f.write(f"- 叶节点Softmax概率: {leaf_node.get('softmax_prob', 0):.4f}\n")
                f.write(f"- 叶节点比例得分: {leaf_node.get('ratio_score', 0):.4f}\n")
            f.write("\n")

        # Softmax概率排序的Top-K
        f.write("## Top-K 根因路径（按Softmax概率排序）\n\n")
        for i, path_entry in enumerate(results.get("top_k_paths_softmax", []), 1):
            path = path_entry if isinstance(path_entry, list) else path_entry
            path_score = path[-1].get("softmax_path_score", 0) if isinstance(path[-1], dict) else 0
            f.write(f"### 路径 {i}\n")
            f.write(f"- 路径得分: {path_score:.6f}\n")
            if path:
                f.write(f"- 路径节点数: {len(path) - 1}\n")
                leaf_node = path[-2] if len(path) > 1 else path[-1]
                f.write(f"- 叶节点: {leaf_node.get('name', '')} ({leaf_node.get('code', '')})\n")
                f.write(f"- 叶节点原始得分: {leaf_node.get('node_score', 0):.4f}\n")
                f.write(f"- 叶节点Softmax概率: {leaf_node.get('softmax_prob', 0):.4f}\n")
                f.write(f"- 叶节点比例得分: {leaf_node.get('ratio_score', 0):.4f}\n")
            f.write("\n")

        # 比例得分排序的Top-K
        f.write("## Top-K 根因路径（按比例得分排序）\n\n")
        for i, path_entry in enumerate(results.get("top_k_paths_ratio", []), 1):
            path = path_entry if isinstance(path_entry, list) else path_entry
            path_score = path[-1].get("ratio_path_score", 0) if isinstance(path[-1], dict) else 0
            f.write(f"### 路径 {i}\n")
            f.write(f"- 路径得分: {path_score:.6f}\n")
            if path:
                f.write(f"- 路径节点数: {len(path) - 1}\n")
                leaf_node = path[-2] if len(path) > 1 else path[-1]
                f.write(f"- 叶节点: {leaf_node.get('name', '')} ({leaf_node.get('code', '')})\n")
                f.write(f"- 叶节点原始得分: {leaf_node.get('node_score', 0):.4f}\n")
                f.write(f"- 叶节点Softmax概率: {leaf_node.get('softmax_prob', 0):.4f}\n")
                f.write(f"- 叶节点比例得分: {leaf_node.get('ratio_score', 0):.4f}\n")
            f.write("\n")

    print(f"结果已保存到: {result_file}")
    print(f"摘要已保存到: {summary_file}")


def main():
    """主函数"""
    config_path = "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config_dict = yaml.safe_load(f)

    eval_config = EvaluationConfig(
        llm_api_key=config_dict["llm"]["api_key"],
        llm_base_url=config_dict["llm"]["base_url"],
        llm_model_name=config_dict["llm"]["model_name"],
        llm_temperature=config_dict["llm"]["temperature"],
        llm_max_tokens=config_dict["llm"]["max_tokens"],
        top_k=config_dict["evaluation"]["top_k"],
        partial_match_penalty_ratio=config_dict["evaluation"]["partial_match_penalty_ratio"],
        num_debate_agents=config_dict["evaluation"]["debate"]["num_debate_agents"],
        num_judging_agents=config_dict["evaluation"]["debate"]["num_judging_agents"],
        variance_penalty_weight=config_dict["evaluation"]["debate"]["variance_penalty_weight"],
        max_debate_score_ratio=config_dict["evaluation"]["debate"]["max_debate_score_ratio"],
        sibling_match_threshold=config_dict["evaluation"]["thresholds"].get("sibling_match_threshold", 0.5),
        max_retries=config_dict["evaluation"].get("max_retries", 3),
        retry_delay=config_dict["evaluation"].get("retry_delay", 1.0)
    )

    processor = FaultTreeProcessor(eval_config)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)

    fault_tree_path = config_dict["data"]["fault_tree_path"]
    if not os.path.isabs(fault_tree_path):
        fault_tree_path = os.path.normpath(os.path.join(project_dir, fault_tree_path))
    print(f"正在加载故障树数据: {fault_tree_path}")
    df = processor.load_fault_tree(fault_tree_path)

    print("正在构建故障树结构...")
    tree = processor.build_tree_structure(df)

    question = "用户反馈网络接入失败，终端不发起接入，请分析可能的原因。"
    print(f"\n问题: {question}")

    print("正在进行故障树评估（层序遍历 + Softmax/比例算分）...")
    top_k_original, top_k_softmax, top_k_ratio, debate_records = processor.find_top_k_paths(tree, question, k=eval_config.top_k)

    results = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "question": question,
        "top_k_paths_original": top_k_original,
        "top_k_paths_softmax": top_k_softmax,
        "top_k_paths_ratio": top_k_ratio,
        "debate_records": debate_records,
        "config": config_dict
    }

    output_dir = config_dict["result"]["output_path"]
    if not os.path.isabs(output_dir):
        output_dir = os.path.normpath(os.path.join(project_dir, output_dir))
    save_results(results, output_dir)

    print("\n评估完成!")
    print(f"\n=== Top-{eval_config.top_k} 根因路径（按原始得分排序）===")
    for i, path_entry in enumerate(top_k_original, 1):
        path = path_entry if isinstance(path_entry, list) else path_entry
        # path_entry如果是list，path[-1]是score_info dict
        score = path[-1].get("original_path_score", 0) if isinstance(path[-1], dict) else 0
        print(f"\n路径 {i}:")
        print(f"  路径得分: {score:.4f}")
        if path:
            leaf = path[-2] if len(path) > 1 else path[-1]
            print(f"  叶节点: {leaf.get('name', '')}")
            print(f"  原始得分: {leaf.get('node_score', 0):.4f}")
            print(f"  Softmax概率: {leaf.get('softmax_prob', 0):.4f}")
            print(f"  比例得分: {leaf.get('ratio_score', 0):.4f}")

    print(f"\n=== Top-{eval_config.top_k} 根因路径（按Softmax概率排序）===")
    for i, path_entry in enumerate(top_k_softmax, 1):
        path = path_entry if isinstance(path_entry, list) else path_entry
        score = path[-1].get("softmax_path_score", 0) if isinstance(path[-1], dict) else 0
        print(f"\n路径 {i}:")
        print(f"  路径得分: {score:.6f}")
        if path:
            leaf = path[-2] if len(path) > 1 else path[-1]
            print(f"  叶节点: {leaf.get('name', '')}")
            print(f"  原始得分: {leaf.get('node_score', 0):.4f}")
            print(f"  Softmax概率: {leaf.get('softmax_prob', 0):.4f}")
            print(f"  比例得分: {leaf.get('ratio_score', 0):.4f}")

    print(f"\n=== Top-{eval_config.top_k} 根因路径（按比例得分排序）===")
    for i, path_entry in enumerate(top_k_ratio, 1):
        path = path_entry if isinstance(path_entry, list) else path_entry
        score = path[-1].get("ratio_path_score", 0) if isinstance(path[-1], dict) else 0
        print(f"\n路径 {i}:")
        print(f"  路径得分: {score:.6f}")
        if path:
            leaf = path[-2] if len(path) > 1 else path[-1]
            print(f"  叶节点: {leaf.get('name', '')}")
            print(f"  原始得分: {leaf.get('node_score', 0):.4f}")
            print(f"  Softmax概率: {leaf.get('softmax_prob', 0):.4f}")
            print(f"  比例得分: {leaf.get('ratio_score', 0):.4f}")


if __name__ == "__main__":
    main()
