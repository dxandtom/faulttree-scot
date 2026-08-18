#!/usr/bin/env python3
"""辩论式 RCA 的执行壳:吃 rca_input.json + tree_index.json,吐 rca.json(四档契约)。

在流水线里的位置 = 原 fault-tree-rca 手工步骤的**平替**(runner/plan/generate 零改动):
    runner 备好本轮结果目录的 rca_input.json 后,原来"请用 fault-tree-rca 技能手工产出",
    现在跑本脚本即可(它自己调 LLM,不需要 harness 模型)。

流程:事实短路筛(draft_rca 机械分类:全干净→引擎整场跳过;有争议→只辩争议分支,--no-screen 关)
      → facts_to_question 渲染证据文本 → tree_to_vendor_xlsx 生成引擎格式故障树 →
      调 vendor 引擎(--mock 用假引擎,真引擎见 vendor/README.md)→
      result_adapter 出 rca.json(--mechanism all 时三份都出,便于 A/B/C 评测)→
      原始引擎输出落 rca_debate_full.json(排查/审计用)。

用法(标准产物布局下最小命令,tree-index/out-dir 自动推断):
  run_debate_rca.py --rca-input <OUT>/result-<运行号>/rca_input.json [--mechanism all]
完整参数(非标准布局/调参时):
  run_debate_rca.py --rca-input <...> [--tree-index <...>] [--out-dir <...>]
      [--mechanism original|softmax|ratio|all] [--k 3] [--mock] [--set K=V ...]
      [--vendor-dir <默认 ../vendor>] [--config <默认 vendor/config.yaml>]
产物:<out-dir>/rca.json(=所选机制;all 时=original)+ rca.<机制>.json ×3(all 时)+ rca_debate_full.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from facts_to_question import build_question                    # noqa: E402
from result_adapter import to_rca_json, _MECH_KEY, _facts_index  # noqa: E402
from tree_to_vendor_xlsx import enrich_conditions, write_xlsx   # noqa: E402


def _rca_scripts() -> Path:
    """同门 fault-tree-rca 的 scripts 目录(事实短路筛借它的 draft/finalize)。"""
    return _HERE.parent.parent / "fault-tree-rca" / "scripts"


def _fact_screen(rca_input_path: Path, out_dir: Path):
    """事实短路筛(1根因 vs 100节点):跑同门 draft_rca 机械分类,划分干净/争议候选。
    干净候选(自身成立+路径无实测不成立/冲突)代码机械确证,不需要辩论;
    争议候选(自身冲突/层级矛盾)才值得开辩论庭。同门缺失/md 形态 → None(退回全树辩论)。"""
    import subprocess
    draft_py = _rca_scripts() / "draft_rca.py"
    if not draft_py.exists():
        print("[筛] 未找到同门 fault-tree-rca/draft_rca.py,跳过事实短路筛(全树辩论)")
        return None
    draft_path = out_dir / "rca_draft.screen.json"       # 别叫 rca_draft.json:手工路径用那个名字
    r = subprocess.run([sys.executable, str(draft_py), str(rca_input_path), "-o", str(draft_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not draft_path.exists():
        print(f"[筛] draft_rca 失败(exit {r.returncode}),跳过事实短路筛:"
              f"{(r.stderr or r.stdout or '')[-200:]}")
        return None
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    if "holding_candidates" in draft:                    # 键在=md 形态(零成立候选的空列表也算,别放过)
        print("[筛] rca_input 为完整 md 形态(无剪枝树),跳过事实短路筛(全树辩论)")
        return None
    clean = [str(e["node_id"]) for e in draft.get("confirmed_candidates") or []
             if str(e.get("auto_verdict")) == "confirmed"]
    contested = [str(e["node_id"]) for e in draft.get("conflict_nodes") or []]
    contested += [str(e["node_id"]) for e in draft.get("probable_candidates") or []
                  if e.get("auto_verdict")]              # 层级矛盾:两面证据值得辩给人看
    return {"clean_confirmed": clean, "contested": contested,
            "draft_path": str(draft_path), "draft": draft}


def _contested_scope(tree_index: dict, contested_ids) -> set:
    """争议节点所在分支 = 每个争议节点的祖先链(含自身)∪ 以其为根的子树。"""
    nodes = tree_index.get("nodes") or {}
    kids: dict = {}
    for nid, n in nodes.items():
        kids.setdefault(str(n.get("parent")), []).append(nid)
    keep: set = set()
    unknown = [c for c in contested_ids if c not in nodes]
    if unknown:                                          # 不认识的 id 贡献零节点=该候选分支被无声排除,必须响
        print(f"  [筛][warn] 辩题节点 {unknown} 不在 tree_index 里,其分支不会进辩论范围——"
              "核对 rca_input 与树是否同一棵")
    for cid in contested_ids:
        cur, seen = cid, set()
        while cur and cur in nodes and cur not in seen:
            seen.add(cur)
            keep.add(cur)
            cur = str(nodes[cur].get("parent")) if nodes[cur].get("parent") else None
        stack = [cid]
        while stack:
            for k in kids.get(stack.pop(), []):
                if k not in keep:
                    keep.add(k)
                    stack.append(k)
    return keep


def _short_circuit(screen: dict, rca_input_path: Path, out_dir: Path) -> int:
    """全部候选机械闭环:finalize 直接合成 rca.json,辩论引擎整场不跑(0 次 LLM 调用)。
    诚实注记:**不写 rca_debate_full.json**(评估侧靠它分引擎,机械结果不冒充辩论结果);
    审计落 debate_screen.json。"""
    import subprocess
    cmd = [sys.executable, str(_rca_scripts() / "finalize_rca.py"), screen["draft_path"],
           "-o", str(out_dir / "rca.json"), "--rca-input", str(rca_input_path)]
    sol = rca_input_path.parent.parent / "tree" / "solutions.json"
    if sol.exists():
        cmd += ["--solutions", str(sol)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print((r.stdout or "").strip())
    if r.returncode != 0:
        print(f"[短路] finalize 失败(exit {r.returncode}):{(r.stderr or '')[-300:]}"
              "——修数据或加 --no-screen 退回全树辩论")
        return r.returncode
    (out_dir / "debate_screen.json").write_text(json.dumps({
        "mode": "fact_short_circuit", "topics": [],
        "clean_confirmed": screen["clean_confirmed"], "contested": [],
        "note": "全部候选自身成立且路径无实测不成立/冲突——代码机械确证,辩论引擎跳过"
                "(0 次 LLM 调用),结果与手工 RCA 引擎同口径;强制全树辩论:--no-screen"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[短路] 事实短路生效:{len(screen['clean_confirmed'])} 条候选全部机械闭环,"
          f"辩论引擎跳过(0 次 LLM 调用)→ {out_dir / 'rca.json'};审计 → debate_screen.json")
    return 0


def _finalize_clean(screen: dict, rca_input_path: Path, out_dir: Path) -> list:
    """干净候选单独机械合成,取其 rca_result 供辩后合并(争议候选走引擎,两不相扰)。"""
    import subprocess
    draft = screen["draft"]
    slim = {"_readme": "事实短路筛拆分:仅干净候选(机械确证);争议候选走辩论引擎,辩后合并",
            "confirmed_candidates": [e for e in draft.get("confirmed_candidates") or []
                                     if str(e.get("auto_verdict")) == "confirmed"],
            "probable_candidates": [], "conflict_nodes": [],
            "excluded_candidates": draft.get("excluded_candidates") or [],
            "undetermined_skeleton": []}
    p = out_dir / "rca_draft_clean.json"
    p.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
    outp = out_dir / "rca_manual_clean.json"
    cmd = [sys.executable, str(_rca_scripts() / "finalize_rca.py"), str(p), "-o", str(outp),
           "--rca-input", str(rca_input_path)]
    sol = rca_input_path.parent.parent / "tree" / "solutions.json"
    if sol.exists():
        cmd += ["--solutions", str(sol)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"[筛] 干净候选机械合成失败,放弃合并(引擎结果原样交付):"
              f"{(r.stderr or r.stdout or '')[-200:]}")
        return []
    entries = json.loads(outp.read_text(encoding="utf-8")).get("rca_result") or []
    for e in entries:
        e["support"] = ("确定性代码判定(事实短路筛:候选自身成立且路径无实测不成立/冲突,"
                        "未经辩论引擎);" + str(e.get("support") or ""))
    return entries


def _leaf_id(entry: dict):
    """rca 条目的候选 node_id:适配层条目在 evidence_path 末端,机械合成条目在顶层。"""
    return (entry.get("evidence_path") or [{}])[-1].get("node_id") or entry.get("node_id")


def _merge_manual(doc: dict, manual_entries: list) -> dict:
    """机械合成的干净候选并入 rca.json(竞争模式兜底):①只补引擎 top-k 漏掉的候选,
    **垫尾**——兜底条目未经辩论排序,不许占榜首;②跨档收口:已进确证档的候选从
    probable_causes 剔除(中间节点候选的引擎路径会因"截断"落疑似,同一节点占两档自相矛盾)。"""
    have = {_leaf_id(e) for e in doc.get("rca_result") or []}
    doc["rca_result"] = (doc.get("rca_result") or []) \
        + [e for e in manual_entries if e.get("node_id") not in have]
    conf_ids = {_leaf_id(e) for e in doc["rca_result"]}
    doc["probable_causes"] = [p for p in (doc.get("probable_causes") or [])
                              if _leaf_id(p) not in conf_ids]
    return doc


def _focus_note(contested: list, topic_ids: list, tree_index: dict) -> str:
    """分诊结果 → 角色卷宗的【辩题聚焦】注记(竞争/争议两款;topic_ids 空返回空串)。
    竞争款只对含干净候选的多候选组合;纯争议候选(可多个)走争议款——冲突候选真伪未定,
    竞争款"不辩真伪"的说法会错误禁掉真伪之辩。"""
    if not topic_ids:
        return ""
    nmap = {nid: (n.get("name") or nid) for nid, n in (tree_index.get("nodes") or {}).items()}
    tnames = "、".join(f"「{nmap.get(t, t)}」" for t in topic_ids[:6]) \
        + ("…" if len(topic_ids) > 6 else "")
    if len(topic_ids) > len(contested):
        return (f"【辩题聚焦】本案候选 {len(topic_ids)} 个({tnames})均有实测支撑、并存竞争——"
                "辩论目标是分主次/并发/连锁并给出排序;候选真伪以证据引擎实测为准,"
                "实测成立的不辩真伪,实测排除的背景节点不得翻案(翻案线索写复核提示)。")
    return (f"【辩题聚焦】本案争点为 {tnames};实测排除的节点不得翻案(翻案线索写复核提示),"
            "未验证/数据不足的节点如实标注,勿当已排除。")


def _coerce(s: str):
    """--set 的值串 → int/float/bool/str(按最贴切的类型)。"""
    low = s.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def _load_processor(vendor_dir: Path, config_path: Path | None, mock: bool,
                    overrides: dict | None = None, shield: bool = False):
    """→ (processor, 描述串)。引擎的接口差异只会打到这一个函数里(见 references/engine_ops.md 升级核对)。
    overrides:命令行 --set 的引擎字段覆盖(如 num_judging_agents=2),盖在 config.yaml 之上——
    临时调参不用动配置文件。
    shield=True 时自动收敛引擎内层重试(见下),--set 显式指定的不动。"""
    if mock:
        from mock_vendor import FaultTreeProcessor
        return FaultTreeProcessor({}), "mock(确定性假引擎,不调 LLM)"
    sys.path.insert(0, str(vendor_dir))
    import yaml
    cfg_file = config_path or (vendor_dir / "config.yaml")
    cfg = (yaml.safe_load(Path(cfg_file).read_text(encoding="utf-8")) or {}) if Path(cfg_file).exists() else {}
    if not cfg:
        print(f"[warn] 未读到配置 {cfg_file}(不存在或为空)——引擎参数将全用默认/空。")

    # 接口适配钩子:真引擎接口与文档不符时,本地**只新建** vendor/bridge.py 包一层(暴露文档形状的
    # FaultTreeProcessor(cfg_dict) 三方法),不改本壳、不改引擎原件。有 bridge 就优先走 bridge。
    if (vendor_dir / "bridge.py").exists():
        from bridge import FaultTreeProcessor as BridgeProcessor  # type: ignore
        return BridgeProcessor(cfg), f"vendor 引擎(经 vendor/bridge.py 适配,config={cfg_file})"

    try:
        from fault_tree_evaluator_bfs_advanced import FaultTreeProcessor, EvaluationConfig  # type: ignore
    except ImportError as e:
        raise SystemExit(f"未找到真实引擎:{vendor_dir}/fault_tree_evaluator_bfs_advanced.py({e})。"
                         f"引擎 .py 已随仓携带——缺失请用 git checkout 恢复或核对 --vendor-dir;"
                         f"config.yaml 须本地放入 vendor/(见其 README);或先用 --mock 自测适配层。")
    import dataclasses
    known = {f.name for f in dataclasses.fields(EvaluationConfig)}

    # 展平配置:引擎的 config.yaml 是嵌套结构,且**深达两层**——引擎自己的 main() 读的就是
    # evaluation.debate.num_debate_agents / evaluation.thresholds.sibling_match_threshold,
    # 而 EvaluationConfig 是扁平字段(llm_api_key/top_k/num_debate_agents/...)。
    # 递归下钻到叶子,按「路径后缀」从长到短试拼(llm.api_key→llm_api_key;evaluation.debate.
    # num_debate_agents→num_debate_agents),只走一层会把 debate 调参静默丢回默认值。
    # 重命名类键靠后缀拼不出来(引擎 main() 做的是显式改名映射,如
    # evaluation.debate.consistency_check.base_penalty → consistency_base_penalty):
    # 照抄引擎 main() 的取数路径列成别名表,否则这些调参静默回默认值。
    _alias = {"consistency_check_base_penalty": "consistency_base_penalty",
              "consistency_check_penalty_scaling": "consistency_penalty_scaling"}
    flat, unmapped = {}, []
    def _walk(prefix: list, d: dict):
        for k, v in d.items():
            if isinstance(v, dict):
                _walk(prefix + [str(k)], v)
                continue
            parts = prefix + [str(k)]
            sufs = ["_".join(parts[i:]) for i in range(len(parts))]
            cand = next((s for s in sufs if s in known), None) \
                or next((_alias[s] for s in sufs if s in _alias and _alias[s] in known), None)
            if cand:
                flat.setdefault(cand, v)
            else:
                unmapped.append(".".join(parts))
    _walk([], cfg)
    if unmapped:
        print(f"[warn] config.yaml 有映射不到引擎字段的键,已忽略:{sorted(unmapped)}")

    # --set 覆盖(盖在 config.yaml 之上;键必须是引擎认识的字段,拼错当场报错防静默无效)
    for k, v in (overrides or {}).items():
        if k not in known:
            raise SystemExit(f"--set {k}=... 不是引擎字段。可用字段:{sorted(known)}")
        flat[k] = v
    if overrides:
        print(f"[override] 命令行覆盖引擎参数:{overrides}")

    # 重试收敛:引擎内层重试间隔按 retry_delay×次数 递增,config 常见 30 次/15s 之类的
    # 豪放配置——光睡眠就 >100 分钟/调用,再叠加壳的外层加固(shield)纯属双重重试。
    # shield 开着时把内层收敛成"快失败",重试交给外层退避;--set 显式指定的尊重不动。
    if shield:
        clamped = {}
        for k, cap in (("max_retries", 2), ("retry_delay", 1.0)):
            v = flat.get(k)
            if k not in (overrides or {}) and isinstance(v, (int, float)) and v > cap:
                clamped[k] = f"{v}→{cap}"
                flat[k] = cap
        if clamped:
            print(f"[retry收敛] 外层加固已接管重试,引擎内层重试参数已收敛:{clamped}"
                  f"(要保留 config 原值请 --set 显式指定,或 --no-llm-shield)")

    # 硬门禁:LLM 关键字段一个都没映射到 → 引擎会拿空配置"静默降级"成不调 LLM 的兜底模式,
    # 跑出看似正常实则无效的结果(实测 0.03s/场景、debate_records 全空)。宁可当场报错。
    llm_fields = [f for f in ("llm_api_key", "llm_base_url", "llm_model_name") if f in known]
    missing = [f for f in llm_fields if not flat.get(f)]
    if missing:
        raise SystemExit(
            f"config.yaml 未能映射出引擎必需的 LLM 配置字段:{missing}。\n"
            f"  config 顶层键 = {sorted(cfg)};已映射到 = {sorted(flat)}。\n"
            f"  请核对 config.yaml 的嵌套层级/键名(或在 vendor/bridge.py 里自行构造配置)。"
            f"**不要**让引擎拿空配置跑——那会静默降级、不调 LLM、产出无效结果。")

    # 引擎的 LLM 客户端用 requests,会吃 HTTP(S)_PROXY 代理环境变量——不同 shell 代理配置不同,
    # 是"同一台机器有时通有时不通"的元凶。把 LLM 主机塞进 NO_PROXY,内网直连、与 shell 无关。
    import os
    from urllib.parse import urlparse
    host = urlparse(str(flat.get("llm_base_url") or "")).hostname
    if host:
        for var in ("NO_PROXY", "no_proxy"):
            cur = os.environ.get(var, "")
            if host not in cur:
                os.environ[var] = f"{cur},{host}" if cur else host
        print(f"[net] 已把 LLM 主机加入 NO_PROXY(内网直连,不走 shell 代理)")
    return FaultTreeProcessor(EvaluationConfig(**flat)), \
        f"vendor 引擎(config={cfg_file},已映射 {len(flat)} 字段:{','.join(sorted(flat))})"


def _shield_llm(proc, outer_retries: int = 3, base_delay: float = 2.0) -> bool:
    """壳层传输加固(只包运输、不碰评分算法):给 vendor LLMClient.chat 套一层外层重试。

    治的是引擎的"永久降级"设计:一次 502/超时把内层重试耗尽后,引擎置 connection_failed、
    下个节点起**整程**切关键词匹配 fallback——一阵网关抖动就毁掉整次运行的匹配语义。
    本层:chat 空响应(=内层全败)→ 退避后再给整轮机会;任何一次成功 → 撤销 connection_failed。
    只有**持续**故障才会真降级(且会被降级检测报出来)。实例级包装,vendor 文件零改动。"""
    client = getattr(getattr(proc, "evaluator", None), "llm_client", None)
    if client is None or not callable(getattr(client, "chat", None)):
        return False
    import copy as _copy
    import time as _time
    orig = client.chat

    def chat(messages, temperature=None):
        for i in range(outer_retries):
            # vendor 的 chat 会**原地改** caller 的 messages(把 system 内容前插进 user 条目),
            # 每次重试再插一遍——深拷贝隔离,防外层重试把 system 行叠加 N 份污染语义
            resp = orig(_copy.deepcopy(messages), temperature)
            if resp:
                client.connection_failed = False   # 成功即撤销"永久降级"标记
                return resp
            if i < outer_retries - 1:
                _time.sleep(base_delay * (i + 1))
        return ""

    client.chat = chat
    return True


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="辩论式 RCA 执行壳(rca_input → rca.json)")
    ap.add_argument("--rca-input", "--input", dest="rca_input", required=True,
                    help="本轮结果目录的 rca_input.json(--input 是别名,写错参数名的容错)")
    ap.add_argument("--tree-index",
                    help="<OUT>/tree/<场景>.tree_index.json;缺省时按标准布局自动发现"
                         "(rca_input 上两级的 tree/ 下唯一的 *.tree_index.json)")
    ap.add_argument("--out-dir", help="产物目录;缺省=rca_input 所在目录(通常就是本轮 result-<运行号>/)")
    ap.add_argument("--mechanism", default="all", choices=list(_MECH_KEY) + ["all"])
    ap.add_argument("--k", type=int, default=10,
                    help="每种机制输出的 Top-K 根因路径数(默认 10)。只影响最后对排序结果的切片,"
                         "不增加任何 LLM 调用;这是唯一生效的 k(引擎签名默认与 config 的 top_k 均被本参数覆盖)")
    ap.add_argument("--mock", action="store_true", help="用确定性假引擎跑通适配层(不调 LLM)")
    ap.add_argument("--vendor-dir", default=str(_HERE.parent / "vendor"))
    ap.add_argument("--config", help="vendor 配置(默认 <vendor-dir>/config.yaml)")
    ap.add_argument("--pruned-only", action="store_true",
                    help="只喂 rca_input.fault_tree.pruned_nodes(有事实节点+祖先)的剪枝局部子树——"
                         "大树控 LLM 耗时/成本的主开关;rca_input 无 pruned_nodes 时自动回退全树")
    ap.add_argument("--set", dest="overrides", action="append", default=[], metavar="K=V",
                    help="临时覆盖引擎参数(盖在 config.yaml 之上,可多次),如 "
                         "--set num_judging_agents=2 --set llm_temperature=0.2")
    ap.add_argument("--no-llm-shield", action="store_true",
                    help="关闭壳层 LLM 传输加固(默认开:外层重试+成功撤销降级标记,防一阵 502 毁全程)")
    ap.add_argument("--parallel", type=int, default=4, metavar="N",
                    help="并发 LLM 调用数(默认 4;--parallel 1 退回纯串行;评分公式零改动,"
                         "过高易压垮网关吃 502)")
    ap.add_argument("--no-seed-match", action="store_true",
                    help="关闭匹配预播种(默认开:把 execute 已判定的节点结论直接写进匹配缓存,"
                         "匹配层不再调 LLM 重新发现已知答案;实测与理想 LLM 匹配零分歧)")
    ap.add_argument("--no-debate-cache", action="store_true",
                    help="关闭辩护记忆化(默认开:同一辩论组里同(节点,组,question)的辩护只生成一次)")
    ap.add_argument("--no-agent-question", action="store_true",
                    help="关闭辩论侧角色卷宗(默认开:辩护/攻击/回应/一致性/评委收到的 question "
                         "换成分层卷宗——判据原文全员必读(含被证伪/缺数节点)+实测与反证+按角色任务框架;"
                         "机械匹配器不受影响,仍吃回声安全版防误配)")
    ap.add_argument("--no-match-cache", action="store_true",
                    help="关闭匹配记忆化缓存(默认开:同一节点判据只问一次 LLM,辩论里的重复兄弟检查全走缓存)")
    ap.add_argument("--no-validate", action="store_true", help="跳过产出后的 validate_rca 自检")
    ap.add_argument("--no-screen", action="store_true",
                    help="关闭事实短路筛(默认开:干净候选机械确证不辩论,全干净时引擎整场跳过;"
                         "争议候选只辩其所在分支)")
    a = ap.parse_args()

    # 全部路径先转绝对:真引擎运行时我们会把工作目录切到 vendor/(见下),相对路径会失效
    a.rca_input = str(Path(a.rca_input).resolve())
    # tree-index / out-dir 按标准产物布局(<OUT>/result-<运行号>/rca_input.json + <OUT>/tree/*.tree_index.json)
    # 自动推断——最小命令只需 --rca-input;非标准布局(如评测场景)才需要显式传。
    ri_path = Path(a.rca_input)
    if not a.out_dir:
        a.out_dir = str(ri_path.parent)
        print(f"[默认] --out-dir 未给,用 rca_input 所在目录:{a.out_dir}")
    if not a.tree_index:
        tree_dir = ri_path.parent.parent / "tree"
        cands = sorted(tree_dir.glob("*.tree_index.json")) if tree_dir.is_dir() else []
        if len(cands) == 1:
            a.tree_index = str(cands[0])
            print(f"[默认] --tree-index 未给,按标准布局自动发现:{a.tree_index}")
        else:
            raise SystemExit(
                f"--tree-index 未给,且无法自动发现:{tree_dir} 下找到 {len(cands)} 个候选"
                f"{[c.name for c in cands] if cands else ''}。请显式传 --tree-index <tree_index.json>。")
    a.tree_index = str(Path(a.tree_index).resolve())
    out_dir = Path(a.out_dir).resolve()
    vendor_dir = Path(a.vendor_dir).resolve()
    cfg_path = Path(a.config).resolve() if a.config else None
    rca_input = json.loads(Path(a.rca_input).read_text(encoding="utf-8"))
    tree_index = json.loads(Path(a.tree_index).read_text(encoding="utf-8"))
    tree_index = enrich_conditions(tree_index, rca_input)   # 评测树判据在 rca_input 里,合回;生产树无感
    out_dir.mkdir(parents=True, exist_ok=True)

    # 版本管理:out-dir 里永远只有**当前轮** debate 产物。
    # 重跑前把上一轮整体归档到 debate_runs/<时间戳>/——否则新旧 rca.json 混在一起,
    # agent 会把上一轮结果误当本轮汇报(本地真跑实锤过)。
    products = ["rca.json", "rca_debate_full.json", "vendor_fault_tree.xlsx",
                "debate_screen.json", "rca_draft.screen.json",
                "rca_draft_clean.json", "rca_manual_clean.json"] \
        + [f"rca.{m}.json" for m in _MECH_KEY]
    stale = [p for p in products if (out_dir / p).exists()]
    if stale:
        arch = out_dir / "debate_runs" / datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        arch.mkdir(parents=True, exist_ok=True)
        for p in stale:
            (out_dir / p).rename(arch / p)
        print(f"[归档] 上一轮 debate 产物 {len(stale)} 个 → {arch}(out-dir 只保留当前轮)")

    # ---------- 事实短路筛(1根因 vs 100节点)+ 辩题分诊(三类:冲突/竞争/带伤) ----------
    screen = None if a.no_screen else _fact_screen(ri_path, out_dir)
    topic_ids = []
    if screen:
        cands = list(dict.fromkeys(screen["contested"] + screen["clean_confirmed"]))
        if not cands:
            print("[筛] 无成立候选(引擎将按弃权垫片工作),照常全树辩论供排序参考")
            screen = None
        elif not screen["contested"] and len(cands) == 1:
            return _short_circuit(screen, ri_path, out_dir)   # 唯一干净候选:引擎整场跳过
        elif len(cands) >= 2:                            # 多候选竞争:全部候选都是辩题——辩主次/并发/
            topic_ids = cands                            # 连锁与排序;确证仍凭机械事实,得分只管排序
        else:
            topic_ids = list(screen["contested"])        # 单一争议候选(自身冲突/层级矛盾):只辩它
    allowed_contested = None
    if screen:
        allowed_contested = _contested_scope(tree_index, topic_ids)
        competition = len(topic_ids) > len(screen["contested"])
        n_all = len(tree_index.get("nodes") or {})
        if competition:
            print(f"[筛] 多候选竞争 {len(topic_ids)} 个"
                  f"({'、'.join(topic_ids[:6])}{'…' if len(topic_ids) > 6 else ''})"
                  f",引擎辩其所在分支定主次/排序:{len(allowed_contested)}/{n_all} 节点;"
                  f"其中争议候选 {len(screen['contested'])} 个、干净候选 {len(screen['clean_confirmed'])} 个"
                  "(确证凭机械事实,辩论得分只管排序与主次)")
        else:
            # 此分支 clean 恒为空(有干净候选且总数≥2 必走竞争;唯一干净已短路)——只说争议的事
            print(f"[筛] 争议候选 {len(screen['contested'])} 个"
                  f"({'、'.join(screen['contested'][:6])}{'…' if len(screen['contested']) > 6 else ''})"
                  f",引擎只辩其所在分支:{len(allowed_contested)}/{n_all} 节点")
        (out_dir / "debate_screen.json").write_text(json.dumps({
            "mode": "competition" if competition else "contested_only",
            "topics": topic_ids,
            "clean_confirmed": screen["clean_confirmed"],
            "contested": screen["contested"],
            "note": ("多候选竞争:全部候选进辩题分主次/并发/排序;确证凭机械事实,辩论得分只管排序;"
                     "引擎 top-k 漏掉的干净候选由机械合成垫尾补位(不占辩论排序)"
                     if competition else
                     "争议候选(自身冲突/层级矛盾)真伪凭辩论质证+人工复核;引擎只辩其所在分支")},
            ensure_ascii=False, indent=2), encoding="utf-8")

    question = build_question(rca_input, tree_index)
    print(f"[1/4] 证据 question 已渲染({len(question)} 字符)")

    # --pruned-only:只喂剪枝局部子树(runner 的 tree_prune 已确定"有事实节点+祖先"=相关分支),
    # 引擎的匹配/辩论调用量随树规模线性到超线性增长,这是大树最有效的提速手段。
    allowed = None
    if a.pruned_only:
        pn = set(((rca_input.get("fault_tree") or {}).get("pruned_nodes")) or {})
        allowed = {nid for nid in pn if nid in (tree_index.get("nodes") or {})}
        if not allowed:
            print("[pruned-only] rca_input 没有 pruned_nodes(完整形态?),回退喂全树")
            allowed = None
    if allowed_contested is not None:                    # 事实短路筛的争议分支,与 pruned-only 求交
        allowed = allowed_contested if allowed is None else (allowed & allowed_contested)

    # 真引擎:把工作目录切到 vendor/,**复刻"右键直跑"的运行环境**——引擎内部若有相对路径操作
    # (自行读 config.yaml、写结果目录等),都按 vendor/ 解析;否则在仓库根下运行会静默读不到而降级。
    import os
    prev_cwd = os.getcwd()
    if not a.mock:
        os.chdir(vendor_dir)
    overrides = {}
    for kv in a.overrides:
        if "=" not in kv:
            raise SystemExit(f"--set 格式应为 key=value,收到:{kv!r}")
        k, v = kv.split("=", 1)
        overrides[k.strip()] = _coerce(v)

    try:
        proc, desc = _load_processor(vendor_dir, cfg_path, a.mock, overrides,
                                     shield=not a.no_llm_shield)
        if not a.mock and not a.no_llm_shield:
            if _shield_llm(proc):
                print("[llm加固] 已启用外层重试(×3,退避 2s/4s):瞬时 502/超时不再让引擎永久降级"
                      "关键词匹配;--no-llm-shield 可关闭")
        boost_applied = []
        if not a.mock:
            from engine_boost import apply as _boost
            kit = None
            if not a.no_agent_question:
                from facts_to_question import DossierKit
                focus = _focus_note(screen["contested"], topic_ids, tree_index) if screen else ""
                kit = DossierKit(rca_input, tree_index, focus_note=focus)
            boost_applied = _boost(proc, parallel=a.parallel, match_cache=not a.no_match_cache,
                                   debate_cache=not a.no_debate_cache, agent_kit=kit) or []

        # 心跳:每次 LLM 调用完成刷新单行文件——长跑时用它区分"还在跑(单次调用就是慢)"
        # 与"真挂了"(心跳超过 ~20 分钟不动才值得怀疑,180s 超时×重试的最坏栈就这么长)。
        hb_file = out_dir / "debate_heartbeat.txt"
        _hb_client = getattr(getattr(proc, "evaluator", None), "llm_client", None)
        if _hb_client is not None and callable(getattr(_hb_client, "chat", None)):
            import threading as _th
            _orig_chat_hb, _hb = _hb_client.chat, {"n": 0}
            _hb_lock = _th.Lock()

            def _chat_hb(messages, temperature=None):
                r = _orig_chat_hb(messages, temperature)
                with _hb_lock:
                    _hb["n"] += 1
                    n = _hb["n"]
                try:
                    hb_file.write_text(
                        f"{datetime.datetime.now().strftime('%H:%M:%S')} 第 {n} 次 LLM 调用完成"
                        f"(累计失败 {getattr(_hb_client, '_boost_fail_count', 0)})\n", encoding="utf-8")
                except Exception:
                    pass
                return r

            _hb_client.chat = _chat_hb
        # xlsx 落在 out_dir(不用临时目录):真/假引擎都可能持有文件句柄,Windows 上临时目录退出清理
        # 会 WinError 32;落盘产物顺带可审计(引擎实际吃到的树长什么样,一看便知)。
        xlsx = out_dir / "vendor_fault_tree.xlsx"
        n_rows = write_xlsx(tree_index, xlsx, allowed)
        print(f"[2/4] vendor 格式故障树已生成({n_rows} 行 → {xlsx.name});引擎={desc}")

        # 进度可见化:长跑时另开终端 tail <out-dir>/debate_progress.txt 即知跑到哪了。
        # BFS 每评估完一个节点**追加**一行(日志式,保留全程轨迹;每轮运行开头写分隔行);
        # 评估在主线程串行,无并发写。实例级包装、只记数不碰返回值,评分零影响;
        # mock/bridge 无 evaluator 时自动跳过。
        ev_obj = getattr(proc, "evaluator", None)
        prog_file = out_dir / "debate_progress.txt"

        def _plog(line: str):
            try:
                with open(prog_file, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.datetime.now().strftime('%H:%M:%S')} {line}\n")
            except Exception:
                pass

        if ev_obj is not None and callable(getattr(ev_obj, "evaluate_node", None)):
            _plog(f"===== 新一轮 debate 开始(共 {n_rows} 节点)=====")
            _orig_eval, _n = ev_obj.evaluate_node, {"v": 0}

            def _eval_with_progress(node, question, sibling_nodes=None):
                r = _orig_eval(node, question, sibling_nodes)
                _n["v"] += 1
                _plog(f"已评估 {_n['v']}/{n_rows};{node.get('name', '?')}"
                      f"(得分 {getattr(r, 'score', 0):.2f}, {getattr(r, 'match_type', '?')})")
                return r

            ev_obj.evaluate_node = _eval_with_progress
            print(f"[进度] 实时进度日志:{prog_file}(每评估一个节点追加一行,tail 即可跟踪)")

        df = proc.load_fault_tree(str(xlsx))
        tree = proc.build_tree_structure(df)

        # P2a 预播种:execute 已判定的节点结论直接进匹配缓存(见 engine_boost.seed_match_cache)
        if not a.mock and not a.no_seed_match and not a.no_match_cache:
            from engine_boost import seed_match_cache
            fx_seed = _facts_index(rca_input)
            nf, nz = seed_match_cache(proc, tree, question, {k: v["st"] for k, v in fx_seed.items()})
            if nf or nz:
                print(f"[seed] 已按 execute 事实预播种匹配结果:{nf} 节点满配(成立)+ {nz} 节点零配"
                      f"(排除/数据不足)——匹配层不再调 LLM 重新发现已知答案;--no-seed-match 关闭")

        top_orig, top_soft, top_ratio, debate_records = proc.find_top_k_paths(tree, question, a.k)
        _plog("全部节点评估完成,正在写产物")
    finally:
        os.chdir(prev_cwd)

    # 降级检测:引擎在 LLM 连接彻底失败一次后会**永久**切到关键词匹配 fallback 静默续跑——
    # 结果看着正常实则匹配语义已经变了。这里把降级状态显式暴露出来(打印 + 落盘 + rca.json caveat)。
    ev = getattr(proc, "evaluator", None)
    degraded = bool(getattr(ev, "use_fallback", False)
                    or getattr(getattr(ev, "llm_client", None), "connection_failed", False))
    if degraded:
        print("⚠ [降级警告] 运行中 LLM 连接曾失败,引擎已自动退回关键词匹配 fallback 续跑——"
              "本次结果的匹配语义已受损,建议排查网络/服务(probe_llm.py)后重跑。")
    # 瞬时失败可见化:shield 恢复后"中途失败过"不再触发降级,但个别调用走了兜底
    # (单条判据关键词匹配/辩护缺席/评委默认分)。计数>0 就要知道,多了该重跑。
    n_fail = getattr(getattr(ev, "llm_client", None), "_boost_fail_count", 0)
    if n_fail and not degraded:
        print(f"⚠ [瞬时失败] 运行期间 {n_fail} 次 LLM 调用重试耗尽(相应判据/辩护/评委走了兜底,"
              f"受影响的匹配结果已拒绝入缓存);整体未降级,失败数偏多时建议重跑。")

    # 引擎的 debate_records 是 dataclass(DebateRecord),直接 json.dumps 会 TypeError——转 dict。
    # **必须先剔 sibling_nodes 再转**:引擎给 L4+ 兄弟节点互相塞引用(A.sibling_nodes 含 B,
    # B.sibling_nodes 含 A,循环引用),dataclasses.asdict 深递归遇环必爆 RecursionError
    # (本地真跑实锤:只在"L4+ 多兄弟组真的辩论了"时触发,之前 asdict 后再 pop 是顺序反了)。
    # 逐字段浅取,跳过环源字段;其余字段(str/float/评分 dict)都是无环平结构。
    import dataclasses
    recs = []
    for r in debate_records or []:
        if dataclasses.is_dataclass(r):
            d = {f.name: getattr(r, f.name) for f in dataclasses.fields(r) if f.name != "sibling_nodes"}
        elif isinstance(r, dict):
            d = {k: v for k, v in r.items() if k != "sibling_nodes"}
        else:
            d = {"repr": str(r)}
        recs.append(d)
    full = {"timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "question": question, "k": a.k, "llm_degraded": degraded,
            # 落"实际生效"不落"意图":包装被守卫跳过时这里必须如实为 False(审计不许被骗)
            "agent_question_dossier": any(s.startswith("角色卷宗") for s in boost_applied),
            "boost_applied": boost_applied,
            "llm_call_failures": n_fail,
            "top_k_paths_original": top_orig, "top_k_paths_softmax": top_soft,
            "top_k_paths_ratio": top_ratio, "debate_records": recs}
    (out_dir / "rca_debate_full.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[3/4] 引擎原始输出 → {out_dir / 'rca_debate_full.json'}"
          f"(路径 {len(top_orig)}/{len(top_soft)}/{len(top_ratio)},辩论 {len(debate_records)} 节点)")

    # 干净候选的机械合成兜底——只有竞争模式会走到(contested_only 时 clean 恒空):引擎照辩
    # 干净候选、适配层按事实升档,这里只兜"引擎 top-k 漏掉某干净候选"的缺口(_merge_manual:
    # 垫尾+跨档收口)。审计 debate_screen.json 已在分诊处落盘,此处不再写。
    manual_entries = []
    if screen and screen["clean_confirmed"]:
        manual_entries = _finalize_clean(screen, ri_path, out_dir)

    mechs = list(_MECH_KEY) if a.mechanism == "all" else [a.mechanism]
    for m in mechs:
        doc = to_rca_json(full, m, tree_index, rca_input)
        if manual_entries:
            doc = _merge_manual(doc, manual_entries)
        (out_dir / f"rca.{m}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    chosen = "original" if a.mechanism == "all" else a.mechanism
    (out_dir / "rca.json").write_text(
        (out_dir / f"rca.{chosen}.json").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[4/4] rca.json(机制={chosen})+ {', '.join('rca.' + m + '.json' for m in mechs)} → {out_dir}/")

    # 顺手机械自检(与老路径同一道闸门;闸门脚本不在预期位置就只提示、不阻断)
    if not a.no_validate:
        vr = _HERE.parent.parent / "fault-tree-rca" / "scripts" / "validate_rca.py"
        if vr.exists():
            import subprocess
            r = subprocess.run([sys.executable, str(vr), str(out_dir / "rca.json"), a.rca_input],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            tailline = (r.stdout or r.stderr or "").strip().splitlines()
            print(f"[validate_rca] exit {r.returncode}:{tailline[-1] if tailline else ''}")
            if r.returncode != 0:
                return r.returncode
        else:
            print(f"[validate_rca] 未找到 {vr},跳过自检(手动跑:validate_rca.py <rca.json> <rca_input.json>)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
