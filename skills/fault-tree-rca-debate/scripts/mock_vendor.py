#!/usr/bin/env python3
"""vendor 引擎的**确定性假实现**(按其文档 6.1 的接口形状)。

用途:真 vendor 代码不在本仓(见 vendor/README.md)——这个 mock 让适配层(xlsx 生成 →
question 渲染 → 结果适配 → validate_rca)能在没有真引擎、不调 LLM 的情况下端到端自测。
**它不是算法复刻**:评分逻辑是最简单的确定性文本包含匹配 + 哈希伪随机"辩论分",
只保证输出**形状**与 vendor 文档 5.2 一致(top_k×3 + debate_records)。

接口(与文档 6.1 对齐):
    p = FaultTreeProcessor(config_dict_or_none)
    df = p.load_fault_tree("tree.xlsx")
    tree = p.build_tree_structure(df)
    top_orig, top_soft, top_ratio, debate_records = p.find_top_k_paths(tree, question, k)
"""
from __future__ import annotations

import hashlib
import math
from collections import deque


class FaultTreeProcessor:
    def __init__(self, config=None):
        self.config = config or {}

    # ---- 装载:xlsx → 行 dict 列表(充当"df") ----
    def load_fault_tree(self, xlsx_path):
        from openpyxl import load_workbook
        ws = load_workbook(xlsx_path, read_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        header = [str(h or "") for h in rows[0]]
        return [dict(zip(header, r)) for r in rows[1:]]

    # ---- 建树:按 Ln Code 合并 ----
    def build_tree_structure(self, df):
        levels = sorted({int(h[1:h.index(" ")]) for h in (df[0].keys() if df else [])
                         if h.endswith(" Code") and h.startswith("L")})
        nodes, root = {}, None
        for row in df:
            parent = None
            deepest = None
            for lv in levels:
                code = row.get(f"L{lv} Code")
                if not code:
                    break
                if code not in nodes:
                    nodes[code] = {"code": code, "name": row.get(f"L{lv}") or code,
                                   "chinese_name": row.get(f"L{lv}(Chinese)") or row.get(f"L{lv}") or code,
                                   "level": lv, "judgment_conditions": [], "children": []}
                    if parent is not None:
                        nodes[parent]["children"].append(nodes[code])
                    elif root is None:
                        root = nodes[code]
                parent, deepest = code, code
            cond = str(row.get("Judgment Condition(Chinese)") or "").strip()
            if deepest and cond and cond not in nodes[deepest]["judgment_conditions"]:
                nodes[deepest]["judgment_conditions"].append(cond)
        return root

    # ---- 评分(确定性 mock;真引擎在此处是 条件匹配 + LLM Debate) ----
    @staticmethod
    def _jitter(code):                      # 稳定伪随机 0.00–0.20
        return int(hashlib.sha1(str(code).encode()).hexdigest()[:4], 16) % 200 / 1000.0

    def _score_node(self, node, question):
        conds = node.get("judgment_conditions") or []
        if not conds:
            return 1.0, "structural", []
        hit = sum(1 for c in conds if c and c in question)          # 原文包含 = 匹配(mock 简化)
        if hit == len(conds):
            return 1.0, "full", []
        if hit > 0:
            return 0.5 * hit / len(conds) + 0.25, "partial", []
        base = 0.15 + self._jitter(node["code"])                    # "辩论"伪评分
        judges = [round(min(1.0, base + d), 3) for d in (-0.05, 0.0, 0.05)]
        return round(base, 3), "debate", judges

    def find_top_k_paths(self, tree, question, k=3):
        all_paths, debate_records = [], []
        q = deque([(tree, [], 1.0)])
        while q:
            node, path, acc = q.popleft()
            score, mtype, judges = self._score_node(node, question)
            if mtype == "debate":
                mean = sum(judges) / len(judges)
                std = math.sqrt(sum((x - mean) ** 2 for x in judges) / len(judges))
                debate_records.append({"node_code": node["code"], "node_name": node["chinese_name"],
                                       "debate_contents": ["(mock) 为该节点辩护…"],
                                       "judging_scores": judges, "final_score": score,
                                       "mean_score": round(mean, 3), "std_score": round(std, 3),
                                       "variance_penalty": round(std * 0.3, 3)})
            info = {"code": node["code"], "name": node["name"], "chinese_name": node["chinese_name"],
                    "level": node["level"], "node_score": round(score, 4), "match_type": mtype}
            cur = path + [info]
            acc2 = acc * score
            if not node.get("children"):
                all_paths.append((cur, acc2))
            else:
                for ch in node["children"]:
                    q.append((ch, cur, acc2))

        # 兄弟归一(softmax / ratio)按叶子分近似:mock 只需形状对
        scores = [s for _, s in all_paths] or [1.0]
        mx = max(scores)
        exps = [math.exp((s - mx) * 4) for s in scores]
        sm = [e / sum(exps) for e in exps]
        tot = sum(scores) or 1.0
        rt = [s / tot for s in scores]

        def pack(order_key):
            idx = sorted(range(len(all_paths)), key=order_key, reverse=True)[:k]
            out = []
            for i in idx:
                nodes_, orig = all_paths[i]
                out.append(list(nodes_) + [{"original_path_score": round(orig, 4),
                                            "softmax_path_score": round(sm[i], 4),
                                            "ratio_path_score": round(rt[i], 4)}])
            return out

        return (pack(lambda i: scores[i]), pack(lambda i: sm[i]), pack(lambda i: rt[i]),
                debate_records)
