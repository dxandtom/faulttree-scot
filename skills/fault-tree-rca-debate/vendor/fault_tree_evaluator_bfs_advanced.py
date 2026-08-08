"""
故障树评估主程序 - 增强版V2
基于多智能体辩论的故障树评估方法 - 改进版
新增功能：
1. 防御-攻击辩论框架 + 逻辑一致性检验
2. 置信度和不确定性量化（置信区间计算与传播）
3. 证据追溯和引用链

改进流程：
Defense Agent（辩护）→ Attack Agent（质疑）→ Defense Agent（回应）
       ↓
Consistency Checker（一致性检验）
       ↓
Judging Agent（打分）
       ↓
置信区间计算 + Softmax/Ratio归一化
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
from scipy import stats
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
    num_debate_agents: int = 1  # 辩护智能体数量
    num_judging_agents: int = 3
    variance_penalty_weight: float = 0.3
    max_debate_score_ratio: float = 0.5
    sibling_match_threshold: float = 0.5

    # 逻辑一致性检验配置
    consistency_base_penalty: float = 0.3
    consistency_penalty_scaling: float = 0.2

    # 置信区间计算方法: "frequentist" (t分布) 或 "bayesian" (Beta-Binomial共轭)
    ci_method: str = "frequentist"

    # API重试配置
    max_retries: int = 3
    retry_delay: float = 1.0


@dataclass
class EvidenceRef:
    """证据引用"""
    source: str  # NGAP消息 | 核心网告警 | KPI指标 | 传输监测 | 电源监测
    condition: str
    description: str
    matched: bool
    confidence: float = 1.0


@dataclass
class NodeScore:
    """节点评分结果"""
    node_id: str
    node_name: str
    score: float  # 原始得分（调整后）
    raw_mean: float = 0.0  # 原始均值
    raw_std: float = 0.0  # 原始标准差
    ci_lower: float = 0.0  # 置信区间下界
    ci_upper: float = 0.0  # 置信区间上界
    match_type: str = "full"  # "full", "partial", "debate"
    softmax_prob: float = 0.0
    ratio_score: float = 0.0
    match_details: Dict[str, Any] = field(default_factory=dict)
    debate_scores: List[float] = field(default_factory=list)
    judgment_agent_scores: List[List[float]] = field(default_factory=list)
    sibling_match_penalty: float = 0.0
    consistency_score: float = 1.0  # 一致性得分
    consistency_penalty: float = 0.0  # 一致性惩罚
    evidence_chain: List[EvidenceRef] = field(default_factory=list)


@dataclass
class DebateRecord:
    """Debate过程记录"""
    node_code: str
    node_name: str
    sibling_nodes: List[Dict]
    defense_contents: List[str] = field(default_factory=list)
    attack_contents: List[str] = field(default_factory=list)
    response_contents: List[str] = field(default_factory=list)
    judging_scores: List[Dict] = field(default_factory=list)
    consistency_check_result: Dict = field(default_factory=dict)
    final_score: float = 0.0
    mean_score: float = 0.0
    std_score: float = 0.0
    variance_penalty: float = 0.0
    sibling_match_penalty: float = 0.0
    consistency_penalty: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0


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


# 证据来源关键词映射
EVIDENCE_KEYWORDS = {
    # 通信网络领域
    "NGAP消息": ["NGAP", "Cause", "Context setup", "NGAP message", "信令"],
    "核心网告警": ["告警", "alarm", "abnormal", "AMF", "SMF", "核心网"],
    "KPI指标": ["KPI", "RRC.ConnReq", "Preamble", "Succ", "指标", "成功率"],
    "传输监测": ["X2", "时延", "delay", "丢包", "传输", "30ms"],
    "电源监测": ["电压", "voltage", "48V", "电池", "供电", "DC"],
    # 电脑故障诊断领域
    "硬件表型": ["蜂鸣声", "黑屏", "蓝屏", "屏幕", "键盘灯", "风扇", "指示灯", "无法启动", "无反应", "无任何", "任何反应"],
    "系统现象": ["BOOTMGR", "boot", "DHCP", "BIOS", "F12", "蓝屏", "错误代码", "自检"],
    "电源状态": ["电源", "开机键", "风扇", "指示灯", "供电", "电压", "电源线"],
    "硬件状态": ["内存", "硬盘", "主板", "电容", "数据线", "内存条"],
    "软件现象": ["No bootable", "Invalid partition", "Missing operating system"]
}


def extract_evidence(question: str, conditions: List[str]) -> List[EvidenceRef]:
    """从问题描述中提取证据"""
    evidence_chain = []

    for condition in conditions:
        condition_lower = condition.lower()
        matched = False
        source = "未知来源"
        description = ""
        matched_keyword = ""

        # 根据条件关键词匹配证据来源
        question_lower = question.lower()
        for src, keywords in EVIDENCE_KEYWORDS.items():
            for keyword in keywords:
                if keyword.lower() in condition_lower:
                    source = src
                    matched_keyword = keyword  # 记录匹配的关键词
                    # 检查条件中匹配的关键词是否也在问题中出现
                    if keyword.lower() in question_lower:
                        description = f"问题描述中检测到{keyword}相关指标"
                        matched = True
                    break  # 只用第一个匹配的关键词
            if matched_keyword:  # 如果找到任何匹配就退出外层循环
                break

        # 如果没有精确匹配但条件包含数字或比较符号，也认为有证据
        if not matched:
            if any(c in condition for c in ['>', '<', '=', '>', '<']) or \
               any(char.isdigit() for char in condition):
                description = "问题描述中包含相关判断条件"
                matched = True

        evidence_chain.append(EvidenceRef(
            source=source,
            condition=condition,
            description=description if description else "无法从问题描述中提取相关证据",
            matched=matched,
            confidence=1.0 if matched else 0.0
        ))

    return evidence_chain


def calculate_ci_bayesian(scores: List[float], max_score_ratio: float = 1.0) -> Tuple[float, float, float]:
    """
    使用贝叶斯方法（Beta-Binomial共轭）计算置信区间
    返回: (均值, 下界, 上界)

    原理:
    - 假设每个评判分数是来自Beta分布的参数θ的观测值
    - 使用无信息先验 Beta(1, 1) 作为先验分布
    - 后验分布为 Beta(α_posterior, β_posterior)
    - 其中 α_posterior = α_prior + Σ(scores)
    - β_posterior = β_prior + n - Σ(scores)
    """
    if not scores:
        return 0.0, 0.0, 0.0

    # 无信息先验 Beta(1, 1) - 均匀分布
    alpha_prior = 1.0
    beta_prior = 1.0

    n = len(scores)  # 评判次数
    k = sum(scores)  # 总成功度量（分数之和）

    # 后验参数
    alpha_post = alpha_prior + k
    beta_post = beta_prior + (n - k)

    # 计算后验均值
    mean = alpha_post / (alpha_post + beta_post)

    # 计算95%可信区间（从后验分布的分位数）
    ci_lower = stats.beta.ppf(0.025, alpha_post, beta_post)
    ci_upper = stats.beta.ppf(0.975, alpha_post, beta_post)

    # 应用缩放因子并截断到[0, 1]
    ci_lower = max(0.0, ci_lower * max_score_ratio)
    ci_upper = min(1.0, ci_upper * max_score_ratio)

    return mean, ci_lower, ci_upper


def calculate_confidence_interval(scores: List[float], max_score_ratio: float = 1.0,
                                   method: str = "frequentist") -> Tuple[float, float, float]:
    """
    计算置信区间（统一接口）

    参数:
        scores: 评判分数列表
        max_score_ratio: 最大分数比例
        method: "frequentist" (t分布) 或 "bayesian" (Beta-Binomial共轭)

    返回: (均值, 下界, 上界)
    """
    if method == "bayesian":
        return calculate_ci_bayesian(scores, max_score_ratio)
    else:
        # 默认使用频率学派方法
        if len(scores) < 2:
            mean = np.mean(scores) if scores else 0.0
            std = 0.0
        else:
            mean = np.mean(scores)
            std = np.std(scores, ddof=1)

        if len(scores) >= 2:
            se = std / np.sqrt(len(scores))
            t_critical = stats.t.ppf(0.975, df=len(scores) - 1)
            margin = t_critical * se
        else:
            margin = std * 2 if std > 0 else 0.1

        ci_lower = max(0.0, (mean - margin) * max_score_ratio)
        ci_upper = min(1.0, (mean + margin) * max_score_ratio)

        return mean, ci_lower, ci_upper


def softmax(scores: List[float], temperature: float = 1.0) -> List[float]:
    """
    计算Softmax概率分布
    scores: 原始得分列表
    temperature: 温度参数，默认1.0
    返回: 概率分布列表
    """
    if not scores:
        return []
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

        prompt = f"""你是一个严格的故障诊断专家。请判断条件是否与用户问题直接相关。

【重要】判断标准：
- 条件必须与问题描述中的具体症状强相关才能算匹配
- 如果条件描述的症状（如"蜂鸣声"、"蓝屏"）在问题中未提及，则不算匹配
- 即使条件和问题都属于同一故障域（如都是电脑故障），但症状不匹配也不算匹配

用户问题: {question}

条件: {condition}

请只回答一个字：
- 如果条件中的症状在问题中有明确描述或高度相关，回答"是"
- 如果条件描述的症状在问题中未提及或不相关，回答"否"
不要添加任何其他文字。"""

        response = self.llm_client.chat([
            {"role": "system", "content": "你是一个严格专业的故障诊断专家。"},
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


class DefenseAgent:
    """Defense Agent - 辩护智能体"""

    def __init__(self, llm_client: LLMClient, agent_id: int):
        self.llm_client = llm_client
        self.agent_id = agent_id

    def defend(self, my_node: Dict, sibling_nodes: List[Dict], question: str) -> str:
        """生成辩护内容"""
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

    def respond(self, my_node: Dict, question: str, attack_content: str) -> str:
        """回应质疑"""
        my_name = my_node.get("name", "")
        my_conditions = my_node.get("judgment_conditions", [])

        prompt = f"""你是一个专业的故障诊断专家。你需要回应对方对你辩护内容的质疑。

【你的节点】
{my_name}

【你的判断条件】
{chr(10).join([f"{i+1}. {c}" for i, c in enumerate(my_conditions)])}

【用户问题】
{question}

【对方的质疑内容】
{attack_content}

【任务】
针对对方的质疑进行回应，维护你的辩护观点。

【回复格式】
【回应】：针对质疑的回应内容

请用中文简洁回复。"""

        response = self.llm_client.chat([
            {"role": "system", "content": "你是一个专业、客观的故障诊断专家。"},
            {"role": "user", "content": prompt}
        ])

        return response if response else f"[Agent {self.agent_id} 回应：无法获取回复]"


class AttackAgent:
    """Attack Agent - 攻击智能体"""

    def __init__(self, llm_client: LLMClient, agent_id: int):
        self.llm_client = llm_client
        self.agent_id = agent_id

    def attack(self, target_node: Dict, question: str, defense_content: str) -> str:
        """对辩护内容进行质疑"""
        target_name = target_node.get("name", "")
        target_conditions = target_node.get("judgment_conditions", [])

        prompt = f"""你是一个专业的故障诊断专家，专门负责质疑和挑战其他节点的辩护内容。

【被质疑的节点】
{target_name}

【该节点的判断条件】
{chr(10).join([f"{i+1}. {c}" for i, c in enumerate(target_conditions)])}

【用户问题】
{question}

【该节点的辩护内容】
{defense_content}

【任务】
仔细分析辩护内容与判断条件之间是否存在逻辑矛盾或漏洞，并进行质疑。

【质疑角度】
1. 辩护内容是否与判断条件矛盾？
2. 辩护内容是否承认了某些条件不满足？
3. 辩护内容的论证逻辑是否有漏洞？

【回复格式】
【质疑】：你的质疑内容，指出逻辑矛盾或漏洞

请用中文简洁回复。"""

        response = self.llm_client.chat([
            {"role": "system", "content": "你是一个专业、严格的故障诊断评审专家。"},
            {"role": "user", "content": prompt}
        ])

        return response if response else f"[Agent {self.agent_id} 质疑：无法获取回复]"


class ConsistencyChecker:
    """一致性检验器"""

    def __init__(self, llm_client: LLMClient, base_penalty: float = 0.3, penalty_scaling: float = 0.2):
        self.llm_client = llm_client
        self.base_penalty = base_penalty
        self.penalty_scaling = penalty_scaling

    def check_consistency(self, defense_content: str, conditions: List[str], question: str) -> Dict:
        """
        检验辩护内容与判断条件的一致性
        返回: {consistency_score, contradictions, penalty}
        """
        if not conditions:
            return {"consistency_score": 1.0, "contradictions": [], "penalty": 0.0}

        prompt = f"""分析辩护内容对各判断条件的立场，判断是否存在逻辑矛盾。

【用户问题】
{question}

【判断条件列表】
{chr(10).join([f"{i+1}. {c}" for i, c in enumerate(conditions)])}

【辩护内容】
{defense_content}

【任务】
逐一分析辩护内容对每个判断条件的立场：
- 如果辩护内容明确肯定或承认某条件满足 → 标记为"一致"
- 如果辩护内容明确否定或承认某条件不满足 → 标记为"矛盾"
- 如果辩护内容未明确提及某条件 → 标记为"未提及"

【输出格式】
请返回以下格式的JSON（不要添加任何其他文字）：
{{
    "analysis": [
        {{"condition": "条件1", "position": "一致|矛盾|未提及", "reason": "原因"}},
        ...
    ],
    "consistency_score": 0.0-1.0之间的一致性得分,
    "contradictions": ["矛盾1", "矛盾2", ...]
}}
"""

        response = self.llm_client.chat([
            {"role": "system", "content": "你是一个严格逻辑分析专家。"},
            {"role": "user", "content": prompt}
        ])

        try:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                consistency_score = result.get("consistency_score", 1.0)
                contradictions = result.get("contradictions", [])

                # 如果有矛盾但LLM返回的consistency_score仍为1.0，则修正consistency_score
                # 存在逻辑矛盾时，consistency_score不应该是1.0
                if contradictions and consistency_score >= 1.0:
                    # 根据矛盾数量计算更合理的consistency_score
                    consistency_score = max(0.3, 1.0 - len(contradictions) * 0.3)
                    consistency_score = min(consistency_score, 0.5)

                # 计算惩罚：P = base_penalty + penalty_scaling × (1 - S_consistency)
                penalty = 0.0
                if contradictions:
                    penalty = self.base_penalty + self.penalty_scaling * (1 - consistency_score)
                    penalty = min(penalty, 0.5)  # 惩罚上限0.5

                return {
                    "consistency_score": consistency_score,
                    "contradictions": contradictions,
                    "penalty": penalty,
                    "analysis": result.get("analysis", [])
                }
        except Exception as e:
            print(f"一致性检验解析失败: {e}")

        return {"consistency_score": 1.0, "contradictions": [], "penalty": 0.0}


class JudgingAgent:
    """Judging Agent - 评判智能体"""

    def __init__(self, llm_client: LLMClient, agent_id: int):
        self.llm_client = llm_client
        self.agent_id = agent_id

    def evaluate_nodes(self, nodes: List[Dict], question: str, debate_contents: Dict = None) -> Dict:
        """对所有节点进行独立打分

        debate_contents: {
            "defense": [defense1, defense2, ...],
            "attack": [attack1, attack2, ...],
            "response": [response1, response2, ...]
        }
        """
        node_info = []
        for i, node in enumerate(nodes):
            conditions = node.get("judgment_conditions", [])
            node_info.append(f"""节点{i+1}: {node.get('name', '')}
判断条件: {chr(10).join(conditions[:])}""")

        debate_context = ""
        if debate_contents:
            defense_contents = debate_contents.get("defense", [])
            attack_contents = debate_contents.get("attack", [])
            response_contents = debate_contents.get("response", [])

            debate_parts = []
            for i in range(len(nodes)):
                part = f"节点{i+1}:\n"
                if i < len(defense_contents):
                    part += f"【辩护】{defense_contents[i][:100]}...\n"
                if i < len(attack_contents):
                    part += f"【质疑】{attack_contents[i][:100]}...\n"
                if i < len(response_contents):
                    part += f"【回应】{response_contents[i][:100]}...\n"
                debate_parts.append(part)

            debate_context = "\n\n【辩论过程摘要】\n" + "\n".join(debate_parts)

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
        self.consistency_checker = ConsistencyChecker(
            self.llm_client,
            config.consistency_base_penalty,
            config.consistency_penalty_scaling
        )

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

        # 提取证据链
        evidence_chain = extract_evidence(question, conditions)

        if not conditions or conditions == ["无条件进入"] or conditions == ["N/A"]:
            return NodeScore(
                node_id=node_code,
                node_name=node_name,
                score=1.0,
                raw_mean=1.0,
                ci_lower=1.0,
                ci_upper=1.0,
                match_type="full",
                match_details={"reason": "无条件进入"},
                evidence_chain=evidence_chain
            )

        matched_count, total_count, ratio = self.condition_matcher.match_conditions(question, conditions)

        if ratio == 1.0:
            return NodeScore(
                node_id=node_code,
                node_name=node_name,
                score=1.0,
                raw_mean=1.0,
                ci_lower=1.0,
                ci_upper=1.0,
                match_type="full",
                match_details={"matched": matched_count, "total": total_count},
                evidence_chain=evidence_chain
            )
        elif ratio > 0:
            score = ratio * self.config.partial_match_penalty_ratio
            # 条件匹配得分：置信区间为点估计[score, score]
            ci = min(1.0, max(0.0, score))
            return NodeScore(
                node_id=node_code,
                node_name=node_name,
                score=score,
                raw_mean=score,
                ci_lower=ci,
                ci_upper=ci,
                match_type="partial",
                match_details={"matched": matched_count, "total": total_count, "ratio": ratio},
                evidence_chain=evidence_chain
            )
        else:
            return self._debate_evaluation(node, question, sibling_nodes, evidence_chain)

    def _debate_evaluation(self, node: Dict, question: str, sibling_nodes: List[Dict],
                           evidence_chain: List[EvidenceRef]) -> NodeScore:
        """使用多阶段debate机制进行评估"""
        node_code = node.get("code", "")
        node_name = node.get("name", "")

        all_nodes = [node] + sibling_nodes

        print(f"\n  [Debate] 节点 '{node_name}' 条件无匹配，启动多阶段辩论机制...")
        print(f"  [Debate] 参与节点数: {len(all_nodes)}")

        # 第一阶段：Defense Agent 辩护
        defense_contents = []
        for i, n in enumerate(all_nodes):
            agent = DefenseAgent(self.llm_client, i + 1)
            content = agent.defend(n, all_nodes, question)
            defense_contents.append(content)
            print(f"  [Debate] Defense Agent {i+1} 为节点 '{n.get('name', '')[:30]}...' 辩护完成")

        # 第二阶段：Attack Agent 质疑
        attack_contents = []
        for i, n in enumerate(all_nodes):
            agent = AttackAgent(self.llm_client, i + 1)
            content = agent.attack(n, question, defense_contents[i])
            attack_contents.append(content)
            print(f"  [Debate] Attack Agent {i+1} 对节点 '{n.get('name', '')[:30]}...' 质疑完成")

        # 第三阶段：Defense Agent 回应
        response_contents = []
        for i, n in enumerate(all_nodes):
            agent = DefenseAgent(self.llm_client, i + 1)
            content = agent.respond(n, question, attack_contents[i])
            response_contents.append(content)
            print(f"  [Debate] Defense Agent {i+1} 对节点 '{n.get('name', '')[:30]}...' 回应完成")

        # 第四阶段：一致性检验
        consistency_results = []
        for i, n in enumerate(all_nodes):
            result = self.consistency_checker.check_consistency(
                defense_contents[i] + "\n" + response_contents[i],
                n.get("judgment_conditions", []),
                question
            )
            consistency_results.append(result)
            print(f"  [Debate] Consistency Checker 对节点 {i+1} 检验完成 - 一致性得分: {result.get('consistency_score', 1.0):.2f}")

        # 第五阶段：Judging Agent 评分
        debate_contents_dict = {
            "defense": defense_contents,
            "attack": attack_contents,
            "response": response_contents
        }
        all_judging_results = []
        for j in range(self.config.num_judging_agents):
            judging_agent = JudgingAgent(self.llm_client, j + 1)
            scores_dict = judging_agent.evaluate_nodes(all_nodes, question, debate_contents_dict)
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
        std_score = np.std(scores, ddof=1) if len(scores) > 1 else 0.0

        # 计算置信区间（仅计算均值和margin，暂不缩放）
        raw_mean, ci_lower, ci_upper = calculate_confidence_interval(
            scores, 1.0, method=self.config.ci_method  # 不使用max_score_ratio缩放
        )
        # 计算margin用于后续CI计算
        margin = ci_upper - raw_mean  # 因为ci_lower = raw_mean - margin, ci_upper = raw_mean + margin

        # 获取一致性检验结果
        target_consistency = consistency_results[target_node_idx - 1] if target_node_idx <= len(consistency_results) else {
            "consistency_score": 1.0, "contradictions": [], "penalty": 0.0
        }
        consistency_score = target_consistency.get("consistency_score", 1.0)
        consistency_penalty = target_consistency.get("penalty", 0.0)

        # 检查兄弟节点匹配情况，用于惩罚
        sibling_match_penalty = 0.0
        variance_penalty = std_score * self.config.variance_penalty_weight
        original_debate_score = mean_score - variance_penalty

        best_sibling_match = 0.0
        best_sibling_match_type = ""
        for sibling in sibling_nodes:
            sibling_result = self._quick_check_match(sibling, question)
            if sibling_result > best_sibling_match:
                best_sibling_match = sibling_result
                best_sibling_match_type = "full" if sibling_result >= 1.0 else "partial" if sibling_result > 0 else "none"

        if best_sibling_match >= self.config.sibling_match_threshold:
            sibling_match_penalty = best_sibling_match * 0.5
            original_debate_score = original_debate_score * (1.0 - sibling_match_penalty)
            print(f"  [Debate惩罚] 兄弟节点最佳匹配: {best_sibling_match_type}({best_sibling_match:.2f}), 阈值: {self.config.sibling_match_threshold}, 惩罚系数: {sibling_match_penalty:.2f}")

        # 应用一致性惩罚
        if consistency_penalty > 0:
            original_debate_score = original_debate_score * (1 - consistency_penalty)
            print(f"  [一致性惩罚] 一致性得分: {consistency_score:.2f}, 惩罚: {consistency_penalty:.2f}")

        # 应用最终缩放
        final_score = original_debate_score * self.config.max_debate_score_ratio * self.config.partial_match_penalty_ratio
        final_score = max(0, min(1.0, final_score))

        # CI也应用相同的惩罚和缩放逻辑
        # 先计算缩放后的均值
        scaled_mean = (raw_mean - variance_penalty) * self.config.max_debate_score_ratio * self.config.partial_match_penalty_ratio

        # 应用兄弟节点惩罚和一致性惩罚到CI
        penalty_multiplier = (1.0 - sibling_match_penalty) * (1.0 - consistency_penalty) if consistency_penalty > 0 else (1.0 - sibling_match_penalty)

        ci_lower_final = max(0.0, (scaled_mean - margin * self.config.max_debate_score_ratio * self.config.partial_match_penalty_ratio) * penalty_multiplier)
        ci_upper_final = min(1.0, (scaled_mean + margin * self.config.max_debate_score_ratio * self.config.partial_match_penalty_ratio) * penalty_multiplier)

        # 确保CI下界不超过上界
        if ci_lower_final > ci_upper_final:
            ci_lower_final = ci_upper_final

        debate_record = DebateRecord(
            node_code=node_code,
            node_name=node_name,
            sibling_nodes=sibling_nodes,
            defense_contents=defense_contents,
            attack_contents=attack_contents,
            response_contents=response_contents,
            judging_scores=[{f"Agent{j+1}": all_judging_results[j]} for j in range(len(all_judging_results))],
            consistency_check_result=target_consistency,
            final_score=final_score,
            mean_score=mean_score,
            std_score=std_score,
            variance_penalty=variance_penalty,
            sibling_match_penalty=sibling_match_penalty,
            consistency_penalty=consistency_penalty,
            ci_lower=ci_lower_final,
            ci_upper=ci_upper_final
        )
        self.debate_records.append(debate_record)

        self._print_debate_visualization(debate_record, all_nodes, consistency_results)

        return NodeScore(
            node_id=node_code,
            node_name=node_name,
            score=final_score,
            raw_mean=raw_mean,
            raw_std=std_score,
            ci_lower=ci_lower_final,
            ci_upper=ci_upper_final,
            match_type="debate",
            match_details={
                "mean": mean_score,
                "std": std_score,
                "variance_penalty": variance_penalty,
                "sibling_match_penalty": sibling_match_penalty,
                "consistency_penalty": consistency_penalty
            },
            debate_scores=scores,
            judgment_agent_scores=[scores],
            sibling_match_penalty=sibling_match_penalty,
            consistency_score=consistency_score,
            consistency_penalty=consistency_penalty,
            evidence_chain=evidence_chain
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

    def _print_debate_visualization(self, record: DebateRecord, all_nodes: List[Dict],
                                    consistency_results: List[Dict]):
        """打印Debate过程可视化"""
        print(f"\n  {'='*60}")
        print(f"  [Debate可视化] 节点: {record.node_name}")
        print(f"  {'='*60}")

        print(f"\n  【辩护内容】")
        for i, content in enumerate(record.defense_contents):
            node_name = all_nodes[i].get("name", "")[:30] if i < len(all_nodes) else f"Node {i+1}"
            print(f"  Defense Agent {i+1} (为 '{node_name}...' 辩护):")
            print(f"    {content[:150]}..." if len(content) > 150 else f"    {content}")
            print()

        print(f"\n  【质疑内容】")
        for i, content in enumerate(record.attack_contents):
            node_name = all_nodes[i].get("name", "")[:30] if i < len(all_nodes) else f"Node {i+1}"
            print(f"  Attack Agent {i+1} (对 '{node_name}...' 质疑):")
            print(f"    {content[:150]}..." if len(content) > 150 else f"    {content}")
            print()

        print(f"\n  【回应内容】")
        for i, content in enumerate(record.response_contents):
            node_name = all_nodes[i].get("name", "")[:30] if i < len(all_nodes) else f"Node {i+1}"
            print(f"  Defense Agent {i+1} (对 '{node_name}...' 回应):")
            print(f"    {content[:150]}..." if len(content) > 150 else f"    {content}")
            print()

        print(f"\n  【一致性检验结果】")
        for i, result in enumerate(consistency_results):
            node_name = all_nodes[i].get("name", "")[:30] if i < len(all_nodes) else f"Node {i+1}"
            print(f"  节点{i+1} ('{node_name}...'): 一致性得分={result.get('consistency_score', 1.0):.2f}, 惩罚={result.get('penalty', 0.0):.2f}")
            if result.get('contradictions'):
                print(f"    矛盾: {result['contradictions']}")

        print(f"\n  【Judging Agent评分详情】")
        for i, judging_score in enumerate(record.judging_scores):
            for node_idx_str, scores in judging_score.items():
                if isinstance(scores, dict):
                    print(f"  {node_idx_str}:")
                    for idx, score_info in scores.items():
                        node_name = all_nodes[idx-1].get("name", "")[:30] if idx <= len(all_nodes) else f"Node {idx}"
                        print(f"    节点{idx}('{node_name}...'): {score_info.get('score', 0):.3f} - {score_info.get('reason', '')[:]}")

        print(f"\n  【汇总得分】")
        print(f"    均值: {record.mean_score:.4f}")
        print(f"    标准差: {record.std_score:.4f}")
        print(f"    方差惩罚: -{record.variance_penalty:.4f}")
        if record.sibling_match_penalty > 0:
            print(f"    兄弟匹配惩罚: -{record.sibling_match_penalty:.4f}")
        if record.consistency_penalty > 0:
            print(f"    一致性惩罚: -{record.consistency_penalty:.4f}")
        print(f"    置信区间: [{record.ci_lower:.4f}, {record.ci_upper:.4f}]")
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
        sibling_groups: Dict[str, List[Tuple[str, NodeScore]]] = {}
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

            # 路径得分计算
            current_score = (current_path[-1]["path_score"] if current_path else 1.0) * node_score.score

            # 路径置信区间传播
            current_ci_lower = (current_path[-1]["ci_lower"] if current_path else 1.0) * node_score.ci_lower
            current_ci_upper = (current_path[-1]["ci_upper"] if current_path else 1.0) * node_score.ci_upper

            path_info = {
                "code": node["code"],
                "name": node["name"],
                "chinese_name": node["chinese_name"],
                "level": node["level"],
                "node_score": node_score.score,
                "path_score": current_score,
                "ci_lower": current_ci_lower,
                "ci_upper": current_ci_upper,
                "match_type": node_score.match_type,
                "parent_code": parent_code,
                "evidence_chain": [
                    {
                        "source": e.source,
                        "condition": e.condition,
                        "description": e.description,
                        "matched": e.matched,
                        "confidence": e.confidence
                    }
                    for e in node_score.evidence_chain
                ] if node_score.evidence_chain else [],
                "consistency_score": node_score.consistency_score,
                "consistency_penalty": node_score.consistency_penalty,
                "softmax_prob": node_score.softmax_prob,
                "ratio_score": node_score.ratio_score
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
                node_scores = self.evaluator.calculate_sibling_scores(node_scores)
            elif len(node_scores) == 1:
                node_scores[0].softmax_prob = 1.0
                node_scores[0].ratio_score = 1.0
            # 更新node_score_map - 使用计算后的node_scores
            for i, (node_code, _) in enumerate(score_list):
                node_score_map[node_code] = node_scores[i]

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
                    softmax_path_score *= 1.0
                    ratio_path_score *= 1.0

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

        sorted_by_original = sorted(all_paths, key=lambda x: x[-1].get("original_path_score", 0) if isinstance(x[-1], dict) and "original_path_score" in x[-1] else 0, reverse=True)
        sorted_by_softmax = sorted(all_paths, key=lambda x: x[-1].get("softmax_path_score", 0) if isinstance(x[-1], dict) and "softmax_path_score" in x[-1] else 0, reverse=True)
        sorted_by_ratio = sorted(all_paths, key=lambda x: x[-1].get("ratio_path_score", 0) if isinstance(x[-1], dict) and "ratio_path_score" in x[-1] else 0, reverse=True)

        top_k_original = sorted_by_original[:k]
        top_k_softmax = sorted_by_softmax[:k]
        top_k_ratio = sorted_by_ratio[:k]

        return top_k_original, top_k_softmax, top_k_ratio, self.evaluator.debate_records


def save_results(results: Dict, output_dir: str):
    """保存评估结果"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    result_file = os.path.join(output_dir, f"evaluation_result_v2_{timestamp}.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    summary_file = os.path.join(output_dir, f"summary_v2_{timestamp}.md")
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write("# 故障树评估结果摘要 (V2增强版)\n\n")
        f.write(f"评估时间: {results.get('timestamp', '')}\n\n")
        f.write(f"问题: {results.get('question', '')}\n\n")
        f.write("## 改进特性\n\n")
        f.write("- 防御-攻击辩论框架 + 逻辑一致性检验\n")
        f.write("- 置信度和不确定性量化\n")
        f.write("- 证据追溯和引用链\n\n")

        # 原始得分排序的Top-K
        f.write("## Top-K 根因路径（按原始得分排序）\n\n")
        for i, path_entry in enumerate(results.get("top_k_paths_original", []), 1):
            path = path_entry if isinstance(path_entry, list) else path_entry
            path_score = path[-1].get("original_path_score", 0) if isinstance(path[-1], dict) else 0
            ci_lower = path[-2].get("ci_lower", 0) if len(path) > 1 else 0
            ci_upper = path[-2].get("ci_upper", 0) if len(path) > 1 else 0
            f.write(f"### 路径 {i}\n")
            f.write(f"- 路径得分: {path_score:.4f}\n")
            f.write(f"- 置信区间: [{ci_lower:.4f}, {ci_upper:.4f}]\n")
            if path:
                f.write(f"- 路径节点数: {len(path) - 1}\n")
                leaf_node = path[-2] if len(path) > 1 else path[-1]
                f.write(f"- 叶节点: {leaf_node.get('name', '')} ({leaf_node.get('code', '')})\n")
                f.write(f"- 叶节点原始得分: {leaf_node.get('node_score', 0):.4f}\n")
                f.write(f"- 叶节点一致性得分: {leaf_node.get('consistency_score', 1.0):.2f}\n")
                f.write(f"- 叶节点Softmax概率: {leaf_node.get('softmax_prob', 0):.4f}\n")
                f.write(f"- 叶节点比例得分: {leaf_node.get('ratio_score', 0):.4f}\n")

                # 证据链
                evidence_chain = leaf_node.get("evidence_chain", [])
                if evidence_chain:
                    f.write(f"- 证据链:\n")
                    for ev in evidence_chain:
                        matched_str = "✓" if ev.get("matched") else "✗"
                        f.write(f"  {matched_str} {ev.get('source', '')}: {ev.get('condition', '')}\n")
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

        # 决策建议
        f.write("## 决策建议\n\n")
        f.write("- 置信区间不重叠的路径可明确区分优劣\n")
        f.write("- 置信区间重叠的路径建议人工复核\n")
        f.write("- 一致性得分低于0.7的节点建议重点关注\n")

    print(f"结果已保存到: {result_file}")
    print(f"摘要已保存到: {summary_file}")


def main():
    """主函数"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    config_path = os.path.join(script_dir, "config.yaml")

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
        num_debate_agents=config_dict["evaluation"]["debate"].get("num_debate_agents", 1),
        num_judging_agents=config_dict["evaluation"]["debate"]["num_judging_agents"],
        variance_penalty_weight=config_dict["evaluation"]["debate"]["variance_penalty_weight"],
        max_debate_score_ratio=config_dict["evaluation"]["debate"]["max_debate_score_ratio"],
        sibling_match_threshold=config_dict["evaluation"]["thresholds"].get("sibling_match_threshold", 0.5),
        consistency_base_penalty=config_dict["evaluation"]["debate"].get("consistency_check", {}).get("base_penalty", 0.3),
        consistency_penalty_scaling=config_dict["evaluation"]["debate"].get("consistency_check", {}).get("penalty_scaling", 0.2),
        ci_method=config_dict["evaluation"]["debate"].get("ci_method", "frequentist"),
        max_retries=config_dict["evaluation"].get("max_retries", 3),
        retry_delay=config_dict["evaluation"].get("retry_delay", 1.0)
    )

    processor = FaultTreeProcessor(eval_config)

    fault_tree_path = config_dict["data"]["fault_tree_path"]
    if not os.path.isabs(fault_tree_path):
        fault_tree_path = os.path.normpath(os.path.join(project_dir, fault_tree_path))
    print(f"正在加载故障树数据: {fault_tree_path}")
    df = processor.load_fault_tree(fault_tree_path)

    print("正在构建故障树结构...")
    tree = processor.build_tree_structure(df)

    question = "电脑发出连续蜂鸣声，屏幕黑屏，无法启动，请分析可能的原因。"
    print(f"\n问题: {question}")

    print("正在进行故障树评估（V2增强版：多阶段辩论 + 一致性检验 + 置信区间 + 证据链）...")
    top_k_original, top_k_softmax, top_k_ratio, debate_records = processor.find_top_k_paths(tree, question, k=eval_config.top_k)

    results = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "question": question,
        "top_k_paths_original": top_k_original,
        "top_k_paths_softmax": top_k_softmax,
        "top_k_paths_ratio": top_k_ratio,
        "debate_records": [
            {
                "node_code": r.node_code,
                "node_name": r.node_name,
                "final_score": r.final_score,
                "mean_score": r.mean_score,
                "std_score": r.std_score,
                "consistency_score": r.consistency_check_result.get("consistency_score", 1.0),
                "contradictions": r.consistency_check_result.get("contradictions", []),
                "ci_lower": r.ci_lower,
                "ci_upper": r.ci_upper
            }
            for r in debate_records
        ],
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
        score = path[-1].get("original_path_score", 0) if isinstance(path[-1], dict) else 0
        ci_lower = path[-2].get("ci_lower", 0) if len(path) > 1 else 0
        ci_upper = path[-2].get("ci_upper", 0) if len(path) > 1 else 0
        print(f"\n路径 {i}:")
        print(f"  路径得分: {score:.4f} [{ci_lower:.4f}, {ci_upper:.4f}]")
        if path:
            leaf = path[-2] if len(path) > 1 else path[-1]
            print(f"  叶节点: {leaf.get('name', '')}")
            print(f"  原始得分: {leaf.get('node_score', 0):.4f}")
            print(f"  一致性得分: {leaf.get('consistency_score', 1.0):.2f}")
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
            print(f"  Softmax概率: {leaf.get('softmax_prob', 0):.4f}")

    print(f"\n=== Top-{eval_config.top_k} 根因路径（按比例得分排序）===")
    for i, path_entry in enumerate(top_k_ratio, 1):
        path = path_entry if isinstance(path_entry, list) else path_entry
        score = path[-1].get("ratio_path_score", 0) if isinstance(path[-1], dict) else 0
        print(f"\n路径 {i}:")
        print(f"  路径得分: {score:.6f}")
        if path:
            leaf = path[-2] if len(path) > 1 else path[-1]
            print(f"  叶节点: {leaf.get('name', '')}")
            print(f"  比例得分: {leaf.get('ratio_score', 0):.4f}")


if __name__ == "__main__":
    main()
