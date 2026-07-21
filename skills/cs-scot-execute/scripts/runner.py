#!/usr/bin/env python3
"""cs-scot-execute —— 轻量 SCoT 计划执行器。

执行一份符合 SCoT plan schema 的 plan.json:从 entry 出发,按 transition 遍历 nodes,
用工作记忆(working memory)+ 引用表达式($input / $memory / $step.<id> / $output)
在节点之间传数据。

支持的 node kind(SCoT schema 的子集):
- script:      跑一个自包含解析脚本(确定性);约定脚本 CLI 为
               `python scripts/<name>.py <数据文件> -o <输出.json>`,产出 JSON 存入 outputs。
- agent_skill: prompt-only 技能(模型判断,如 fault-tree-rca);runner 把解析好的 inputs
               备到 result/<id>_input.json,由所在 agent 执行该技能产出结果。

保持轻量:暂不实现 budget / reflection / parallel_after / conditions 等重型特性。

用法:
    runner.py --plan plan.json --data kpi=<file> --data chr=<file> [--base <dir>]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

_TOK = re.compile(r"[^.\[\]]+|\[\d+\]")

# 产物格式契约:执行器"认识"的版本。与 cs-scot-generate/scripts/assemble_scenario.py 的
# PRODUCT_FORMAT_VERSION 成对维护——产物结构有破坏性变化时两边一起 +1。比对只告警不阻断(知情,不是门禁)。
# v2:绑定 filter 字段 + 空表守卫(旧产物的旧内核副本会静默忽略 filter → 假阳,提示重新 generate)。
EXPECTED_PRODUCT_FORMAT = 2


def check_product_format(base: Path) -> None:
    """读产物铭牌 manifest.json,与执行器期望的格式契约版本比对;缺失/不一致只打告警。"""
    mf = base / "manifest.json"
    if not mf.exists():
        print(f"[warn] 产物缺 manifest.json(由旧版工具链生成,早于格式契约 v{EXPECTED_PRODUCT_FORMAT})——"
              "字段/语义可能与当前执行器对不上;建议用最新 cs-scot-generate 重新生成产物。")
        return
    try:
        ver = json.loads(mf.read_text(encoding="utf-8")).get("product_format_version")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] manifest.json 读取失败({e}),按旧产物对待;建议重新 generate。")
        return
    if ver == EXPECTED_PRODUCT_FORMAT:
        return
    hint = ("产物较旧 → 用最新 cs-scot-generate 重新生成"
            if isinstance(ver, int) and ver < EXPECTED_PRODUCT_FORMAT
            else "产物比执行器新 → 升级 cs-scot-* skills 再执行")
    print(f"[warn] 产物格式契约 v{ver} ≠ 执行器期望 v{EXPECTED_PRODUCT_FORMAT}:{hint}。不阻断,但字段可能对不上。")


def _get_path(obj: Any, path: str) -> Any:
    """按 a.b[0].c 在嵌套结构里取值;取不到返回 None。"""
    cur = obj
    for tok in _TOK.findall(path):
        if tok.startswith("["):
            i = int(tok[1:-1])
            cur = cur[i] if isinstance(cur, list) and -len(cur) <= i < len(cur) else None
        else:
            cur = cur.get(tok) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def _set_path(obj: dict, path: str, value: Any) -> None:
    """按 a.b.c 写入嵌套 dict。"""
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def resolve_ref(ref: Any, ctx: Dict[str, Any]) -> Any:
    """解析引用表达式;非 $ 引用按字面量原样返回。

    支持 $input.x / $memory.x.y / $step.<id>.field / $output.x。
    """
    if not isinstance(ref, str):
        return ref
    for ns in ("input", "memory", "output"):
        pre = f"${ns}."
        if ref.startswith(pre):
            return _get_path(ctx[ns], ref[len(pre):])
    if ref.startswith("$step."):
        nid, _, p = ref[len("$step."):].partition(".")
        node_out = ctx["step"].get(nid, {})
        return _get_path(node_out, p) if p else node_out
    return ref  # 字面量


def resolve_inputs(inputs: dict, ctx: Dict[str, Any]) -> dict:
    """把节点 inputs 里的每个引用都解析成实际值。"""
    return {k: resolve_ref(v, ctx) for k, v in (inputs or {}).items()}


def _input_key(node: dict) -> Any:
    """script 节点 inputs.data 若是 $input.<key> 引用,返回 key;否则 None。"""
    ref = (node.get("inputs") or {}).get("data")
    return ref[len("$input."):] if isinstance(ref, str) and ref.startswith("$input.") else None


def store_outputs(outputs: dict, value: Any, ctx: Dict[str, Any]) -> None:
    """把节点产出(一个 JSON 对象)按 outputs 映射写入 $memory / $output。

    约定:script / agent_skill 的产出整体是一个对象;outputs 一般只有一项
    形如 {"result": "$memory.kpi_result"},表示把整个产出存到该位置。
    """
    for field, target in (outputs or {}).items():
        v = value.get(field, value) if isinstance(value, dict) and field in value else value
        if isinstance(target, str) and target.startswith("$memory."):
            _set_path(ctx["memory"], target[len("$memory."):], v)
        elif isinstance(target, str) and target.startswith("$output."):
            _set_path(ctx["output"], target[len("$output."):], v)


def run_script_node(node: dict, ctx: Dict[str, Any], base: Path) -> Any:
    """执行 kind=script 节点:跑 scripts/<name>.py <数据> -o <tmp>,返回其 JSON 产出。"""
    name = node.get("name") or node["id"]
    script = base / "scripts" / f"{name}.py"
    if not script.exists():
        raise FileNotFoundError(f"节点 {node['id']} 的脚本不存在: {script}")
    ins = resolve_inputs(node.get("inputs"), ctx)
    if "data" not in ins or not ins["data"]:
        raise ValueError(f"节点 {node['id']} 缺少已绑定的 inputs.data(用 --data <指标>=<文件> 提供)")
    data_path = Path(ins["data"])
    if not data_path.is_absolute():
        data_path = base / data_path
    if not data_path.exists():
        raise FileNotFoundError(f"节点 {node['id']} 的数据文件不存在: {data_path}(--data 的路径打错了?)")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.json"
        cmd = [sys.executable, str(script), str(data_path), "-o", str(out)]
        print(f"[script] {node['id']}: {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"节点 {node['id']} 失败 (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
        result = json.loads(out.read_text(encoding="utf-8"))
    store_outputs(node.get("outputs"), result, ctx)
    return result


def _gen_run_id() -> str:
    """生成「时间戳 + 随机后缀」的运行号:可排序、且并发/重跑都不撞。"""
    import datetime, os
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{ts}-{os.urandom(3).hex()}"


def _file_fingerprint(path: str) -> dict:
    """一个输入文件的指纹(写进运行清单溯源):路径/大小/修改时间/sha1(大文件跳过 sha1)。"""
    import hashlib, datetime
    p = Path(path)
    info: Dict[str, Any] = {"path": str(path), "exists": p.exists()}
    if not p.exists():
        return info
    st = p.stat()
    info["bytes"] = st.st_size
    info["mtime"] = datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    if st.st_size <= 512 * 1024 * 1024:                 # ≤512MB 才整文件 sha1,免大文件拖慢
        try:
            h = hashlib.sha1()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            info["sha1"] = h.hexdigest()
        except Exception:                                # noqa: BLE001
            info["sha1"] = None
    else:
        info["sha1"], info["sha1_skipped"] = None, "file>512MB"
    return info


def _write_run_manifest(run_dir: Path, run_id: str, plan: dict, data_map: Dict[str, str], base: Path,
                        missing_inputs=None, runs_base: Path = None) -> None:
    """记下「这次运行 = 哪个产物 + 哪些输入数据」:run_dir/run.json + 往 runs/index.jsonl 追加一行,供生产侧管理。
    runs/index.jsonl 落在 runs_base(默认=base;--result-base 生效时=结果根目录)下。"""
    import datetime
    created = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    inputs = {k: _file_fingerprint(v) for k, v in data_map.items() if v}  # 空值=没提供:归 missing_inputs,不指纹(Path('') 会指到 CWD)
    manifest = {"run_id": run_id, "created_at": created, "plan_id": plan.get("plan_id"),
                "product_dir": str(base), "inputs": inputs}
    if missing_inputs:                                   # 部分数据运行:铭牌上写明缺口,事后一看便知这轮少了啥
        manifest["missing_inputs"] = sorted(missing_inputs)
    (run_dir / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    idx = (runs_base or base) / "runs" / "index.jsonl"
    idx.parent.mkdir(parents=True, exist_ok=True)
    with idx.open("a", encoding="utf-8") as f:           # 一行一运行:run_id + 时间 + 各输入指纹,便于事后检索/去重
        f.write(json.dumps({"run_id": run_id, "created_at": created,
                            "inputs": {k: (v.get("sha1") or v.get("bytes")) for k, v in inputs.items()}},
                           ensure_ascii=False) + "\n")


def _archive_previous_run(result_dir: Path, runs_dir: Path):
    """复用产物目录重跑前:把 result/ 里**上一轮**的结果归档到 runs/<上轮run_id>/。
    以 **run.json 是否存在**为准(每轮启动即写它)——这样即便上一轮在 RCA 完成前中断(没 rca.json),
    它的运行指纹与已产出的中间结果也照样归档、不被本轮 run.json 覆写、result/ 不会两轮混血。
    result/ 因此永远=当前一轮,历史在 runs/ 里按 run_id 版本留存。返回归档目录,或 None(首次跑)。"""
    rj = result_dir / "run.json"
    if not rj.exists():
        return None                                    # 首次跑 / 已归档 → 无上一轮可归
    prev_id = None
    try:
        prev_id = json.loads(rj.read_text(encoding="utf-8")).get("run_id")
    except Exception:                                  # noqa: BLE001
        prev_id = None
    if not prev_id:                                    # run.json 损坏/无 run_id → 用可得的最新 mtime 编个号
        import datetime
        mts = [(result_dir / f).stat().st_mtime for f in ("rca.json", "run.json") if (result_dir / f).exists()]
        stamp = (datetime.datetime.fromtimestamp(max(mts)).strftime("%Y%m%dT%H%M%S") if mts else "unknown")
        prev_id = "run-" + stamp
    dst = runs_dir / prev_id
    n = 1
    while dst.exists():
        dst = runs_dir / f"{prev_id}-{n}"; n += 1
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("rca_input.json", "rca.json", "run.json", "_compression.txt"):
        f = result_dir / name
        if f.exists():
            f.replace(dst / name)
    return dst


def _load_knowledge(tree_dir: Path, name_pool: str) -> list:
    """tree/knowledge.md(业务同事手写的外挂知识)→ 与当前子树匹配的条目 [{title, tags?, text}]。
    条目 = 「### 标题」+ 可选「适用:标签1、标签2」行 + 正文;无适用行或含「通用」= 恒载入,
    有标签则任一标签出现在 name_pool(剪枝后节点名/判据/场景名拼串)才载入——按分支载入,不全量灌。
    文件没有/解析失败 → 空列表(知识是增益,绝不因它坏了主流程)。"""
    kf = tree_dir / "knowledge.md"
    if not kf.exists():
        return []
    try:
        text = kf.read_text(encoding="utf-8")
    except OSError:
        return []
    entries, cur = [], None
    for line in text.splitlines():
        if line.startswith("### "):
            cur = {"title": line[4:].strip(), "tags": None, "body": []}
            entries.append(cur)
        elif cur is not None:
            s = line.strip().replace(":", ":")
            if cur["tags"] is None and not cur["body"] and s.startswith("适用:"):
                cur["tags"] = [t for t in re.split(r"[、,,/\s]+", s.split(":", 1)[1]) if t]
            elif line.strip() or cur["body"]:
                cur["body"].append(line)
    out = []
    for e in entries:
        body = "\n".join(e["body"]).strip()
        if not body:
            continue
        if e["tags"] and "通用" not in e["tags"] and not any(t in name_pool for t in e["tags"]):
            continue
        item = {"title": e["title"], "text": body}
        if e["tags"]:
            item["适用"] = "、".join(e["tags"])
        out.append(item)
    return out


def run_agent_skill_node(node: dict, ctx: Dict[str, Any], base: Path, result_subdir: str = "result",
                         result_base: Path = None) -> Any:
    """执行 kind=agent_skill 节点:备好输入,交给该 prompt-only 技能(模型)执行。

    runner 自身不调模型;它把解析好的 inputs 写到 result/<id>_input.json 并打印待办,
    由所在 agent harness 用对应技能产出结果。对 fault-tree-rca,inputs 里的
    fault_tree_md 若是路径会被读成正文。
    """
    name = node.get("name") or node["id"]
    ins = resolve_inputs(node.get("inputs"), ctx)
    # 上游没产出的指标输入(典型:数据源没提供、解析步骤被跳过)→ 从 indicator_results 剔除、
    # 改记 missing_indicators:RCA 把这些源的判据按「数据不足/未覆盖」对待,而不是收到 None
    # 或被跳过步骤的内部占位 dict。三种引用形态都要认:$memory.<路径>(解析为 None)、
    # 整体 $step.<id>(解析为占位 dict)、字段级 $step.<id>.<field>(解析为 None)。
    skipped_mem = ctx.get("_skipped_memory") or {}
    missing_ind: Dict[str, str] = {}
    for k, ref in (node.get("inputs") or {}).items():
        if k == "fault_tree_md":
            continue
        v = ins.get(k)
        src = v.get("_skipped_missing_input") if isinstance(v, dict) and "_skipped_missing_input" in v else None
        if v is not None and src is None:
            continue                                     # 正常解析出的事实,保留
        if src is None and isinstance(ref, str):         # None:顺引用找是哪个源缺了
            if ref.startswith("$memory."):
                src = skipped_mem.get(ref[len("$memory."):])
            elif ref.startswith("$step."):
                st = ctx["step"].get(ref[len("$step."):].split(".", 1)[0])
                if isinstance(st, dict):
                    src = st.get("_skipped_missing_input")
        missing_ind[k] = (f"数据源 '{src}' 未通过 --data 提供,解析步骤已跳过" if src
                          else "上游步骤未产出结果")
        ins.pop(k, None)
    # 约定:fault_tree_md 输入若是 .md 路径则读成正文;其余输入视为各指标解析结果
    ft = ins.pop("fault_tree_md", None)
    md_path = None
    if isinstance(ft, str) and ft.lower().endswith(".md"):
        md_path = Path(ft)
        md_path = md_path if md_path.is_absolute() else (base / md_path)

    # 有权威 index → 剪成"局部子树"(把事实挂到节点上)替代整棵 md,压缩 RCA 上下文;
    # node_id 覆盖不足或剪枝失败 → 回退完整 md(老行为)。
    payload, tree_note = None, ""
    idx_path = md_path.with_suffix(".tree_index.json") if md_path else None
    if idx_path and idx_path.exists():
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from tree_prune import compose_rca_payload
            md_text = md_path.read_text(encoding="utf-8") if (md_path and md_path.exists()) else ""
            comp = compose_rca_payload(json.loads(idx_path.read_text(encoding="utf-8")), ins, md_text)
            payload, tree_note = comp["payload"], "  " + comp["note"]   # 剪枝/floor/覆盖回退已在 compose 内统一处理
        except Exception as e:  # noqa: BLE001
            payload, tree_note = None, f"  [warn] 剪枝失败,回退完整子树: {e}"
    if payload is None:  # 无 index / 剪枝异常 → 完整 md + 全量 indicator_results
        ft_text = md_path.read_text(encoding="utf-8") if (md_path and md_path.exists()) else ft
        payload = {"fault_tree_md": ft_text, "indicator_results": ins}
    if missing_ind:      # 两种形态都带上缺数标记(顶层键,下游 RCA/辩论桥都能看到)
        payload["missing_indicators"] = missing_ind
    # 外挂知识(tree/knowledge.md,业务同事手写):按「适用」标签匹配当前子树,命中的条目随输入
    # 交给 RCA——是判读**口径**(去哪查/怎么解读/什么算正常),不是事实,不能顶替实测值。
    if md_path:
        ftree = payload.get("fault_tree")
        if isinstance(ftree, dict):                      # 剪枝形态:按保留分支的节点名+判据匹配 → 按分支载入
            pool = str(ftree.get("scenario") or "") + " " + " ".join(
                f"{(n or {}).get('name') or ''} {(n or {}).get('condition') or ''}"
                for n in (ftree.get("pruned_nodes") or {}).values())
        else:                                            # 完整 md 形态:对全文匹配
            pool = str(payload.get("fault_tree_md") or "")
        knowledge = _load_knowledge(md_path.parent, pool)
        if knowledge:
            payload["knowledge"] = knowledge
            print(f"  外挂知识:tree/knowledge.md 命中 {len(knowledge)} 条(按分支匹配),已随 RCA 输入下发:"
                  + "、".join(f"「{e['title']}」" for e in knowledge[:5]))

    in_path = (result_base or base) / result_subdir / f"{node['id']}_input.json"
    in_path.parent.mkdir(parents=True, exist_ok=True)
    in_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 注:上一轮的最终结果已在 main() 启动时整体归档到 runs/<上轮run_id>/,result/ 此刻只剩本轮的新输入,
    # 模型据此产出本轮 rca.json,不会读到过期结论。
    print(f"[agent_skill] {node['id']}: 已备好输入 → {in_path}")
    if missing_ind:
        print(f"  ⚠ 本轮缺失的数据源已写进输入的 missing_indicators: {', '.join(missing_ind)}"
              "(其判据按「数据不足」对待,别当「不成立」)")
    if tree_note:
        comp = in_path.parent / "_compression.txt"
        comp.write_text(tree_note.strip() + "\n", encoding="utf-8")
        print(tree_note)
        print(f"  ↑ 压缩统计已落盘 → {comp}(终端把它折叠了也能去这文件看)")
    # 兜底**永远给出**:技能没注册成 skill(Skill(name) 报 Unknown)时,改读其 SKILL.md 手工产出——
    # fault-tree-rca 是 prompt-only,法则全在文件里,注册与否都能做。优先产物目录内的技能副本;探不到就给
    # 标准候选位置(别再因"产物目录里本就没有 fault-tree-rca"而把兜底静默省略——最弱的 harness 恰恰需要它)。
    _found = next((p for p in (base / name / "SKILL.md", base / "skills" / name / "SKILL.md") if p.exists()), None)
    _where = str(_found) if _found else f"{name}/SKILL.md(如 ~/.claude/skills/{name}/SKILL.md 或本仓 skills/{name}/SKILL.md)"
    _fallback = (f";若 Skill(「{name}」) 报 Unknown(未注册成技能)→ 改读 {_where} "
                 f"按其法则 + 同目录 scripts/rca_query.py 手工产出(技能 prompt-only,法则全在文件里,注册与否都能做)")
    print(f"[agent_skill] 这是模型判断步骤。请用「{name}」技能{_fallback},依据该输入产出结果,"
          f"写到 {in_path.parent / 'rca.json'}(契约见 fault-tree-rca 技能)。")
    print(f"  注意:结果目录 {in_path.parent} 已建好,**直接写文件、无需 mkdir**;Windows 路径用正斜杠 /,"
          "且别让带引号的路径以反斜杠结尾(`...\\\"` 会让 shell 一直等闭合引号而卡住)。")
    print("  ⚠ 输入可能很大:**别一次性把整个 rca_input.json 读进上下文**(会超长报错)。"
          "先读 `fault_tree.outline`(id→名字→粗状态,很小)把握全局,再**按分支用 python 只提取相关 `pruned_nodes[id]`**。")
    # 占位:实际结果由 agent 执行技能后填入;这里存一个待办标记,供下游引用不致 KeyError
    placeholder = {"_pending_skill": name, "_input": str(in_path)}
    store_outputs(node.get("outputs"), placeholder, ctx)
    return placeholder


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="轻量 SCoT 计划执行器")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--data", action="append", default=[],
                    help="$input 绑定,如 --data kpi=path.csv(可多次)")
    ap.add_argument("--base", help="路径解析基准目录(默认取 plan.json 所在目录)")
    ap.add_argument("--result-base", help="结果输出根目录:result/ 与 runs/ 落在它下面(默认=--base,"
                                          "即产物目录,老行为不变)。平台不允许技能写自身目录时用它把结果"
                                          "外移,如 --result-base <技能目录>/../<技能名>-result")
    ap.add_argument("--run-id", help="本次运行号(默认自动「时间戳+随机」)。result/ 始终=当前一轮;"
                                     "重跑前自动把上一轮整体归档到 runs/<上轮run_id>/,history 不丢、不覆盖、无歧义")
    ap.add_argument("--new-run", action="store_true",
                    help="(已默认:每轮都自动归档上一轮并带 run_id)保留仅为兼容,无额外效果")
    args = ap.parse_args()

    plan_path = Path(args.plan).resolve()
    base = Path(args.base).resolve() if args.base else plan_path.parent
    check_product_format(base)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    data_map: Dict[str, str] = {}
    for item in args.data:
        if "=" not in item:
            print(f"--data 格式应为 key=path: {item}", file=sys.stderr)
            sys.exit(2)
        k, v = item.split("=", 1)
        data_map[k.strip()] = v.strip()

    nodes = {n["id"]: n for n in plan.get("nodes", [])}
    if not nodes:
        print("plan 里没有 nodes —— 这看起来不是 SCoT 计划", file=sys.stderr)
        sys.exit(2)

    # 数据源核对:plan 的 script 节点各需要哪个 $input.<key>,与本次 --data 实际提供的对照。
    # 缺的**跳过对应步骤**而不是整轮崩掉(用户只有部分数据也能跑出部分结论),结尾统一汇总缺口。
    required: Dict[str, list] = {}
    for n in plan.get("nodes", []):
        k = _input_key(n)
        if n.get("kind") == "script" and k:
            required.setdefault(k, []).append(n["id"])
    provided = {k for k, v in data_map.items() if v}
    missing = {k: ids for k, ids in required.items() if k not in provided}
    unused = sorted(provided - set(required))
    if unused:                                          # typo 防线:key 打错会变成"静默跳过",这里点破
        print(f"[warn] --data 里有 plan 用不到的数据源: {', '.join(unused)}"
              f"(plan 需要: {', '.join(required)})——key 是不是打错了?这些文件本轮不会被使用。")
    if required and not set(required) & provided:       # 一个都没给:大概率用法错误,仍硬报错并给指引
        print(f"没有绑定任何 plan 需要的数据源(需要: {', '.join(required)});"
              f"用 --data <指标>=<文件> 提供,如 --data {next(iter(required))}=<文件>。", file=sys.stderr)
        sys.exit(2)
    if missing:
        print(f"[warn] 缺少数据源: {', '.join(missing)} —— 相应解析步骤将跳过,"
              "其判据本轮无事实(RCA 按「数据不足」对待);结尾有缺口汇总。")

    # 工作记忆初始化(memory_init 里的引用也解析一遍)
    ctx: Dict[str, Any] = {"input": data_map, "memory": {}, "step": {}, "output": {}}
    for k, v in (plan.get("memory_init") or {}).items():
        ctx["memory"][k] = resolve_ref(v, ctx)
    skipped_memory: Dict[str, str] = {}                 # 被跳过步骤没产出的 $memory 路径 → 缺的数据源 key
    ctx["_skipped_memory"] = skipped_memory

    print(f"执行 SCoT: {plan.get('plan_id') or plan.get('meta', {}).get('goal', '')} "
          f"(version={plan.get('version')}, nodes={len(nodes)})")

    # 版本管理:result/ 永远=当前这一轮;重跑前把上一轮整体归档到 runs/<上轮run_id>/(留历史、不覆盖、无歧义),
    # 每轮自动带 run_id(--run-id 可自定义),写 result/run.json 标明本轮身份 + 追加 runs/index.jsonl。
    # eval/benchmark 仍读 result/(= 当前轮),不受影响。
    run_id = args.run_id or _gen_run_id()
    result_base = Path(args.result_base).resolve() if args.result_base else base
    result_dir = result_base / "result"
    archived = _archive_previous_run(result_dir, result_base / "runs")
    result_dir.mkdir(parents=True, exist_ok=True)
    _write_run_manifest(result_dir, run_id, plan, data_map, base, missing_inputs=missing,
                        runs_base=result_base)
    result_subdir = "result"
    if result_base != base:
        print(f"  结果目录:{result_dir}(--result-base 生效,不往产物/技能目录写)")
    if archived:
        print(f"  run_id={run_id};上一轮已归档 → {archived}（result/ 即当前轮，历史在 runs/）")
    else:
        print(f"  run_id={run_id}（result/ 即当前轮，历史按 run_id 存 runs/，已记 run.json + runs/index.jsonl）")

    cur = plan.get("entry")
    visited = 0
    while cur:
        if cur not in nodes:
            raise KeyError(f"transition 指向不存在的节点: {cur}")
        node = nodes[cur]
        kind = node.get("kind")
        if kind == "script":
            mk = _input_key(node)
            if mk and mk in missing:                    # 该步的数据源没提供 → 跳过,继续走后面的步骤
                print(f"[skip] {cur}: 数据源 '{mk}' 未提供(--data {mk}=<文件>),跳过该解析步骤")
                for t in (node.get("outputs") or {}).values():
                    if isinstance(t, str) and t.startswith("$memory."):
                        skipped_memory[t[len("$memory."):]] = mk
                result = {"_skipped_missing_input": mk}  # 只进 $step 占位;不写 $memory,下游解析为 None
            else:
                result = run_script_node(node, ctx, base)
        elif kind == "agent_skill":
            result = run_agent_skill_node(node, ctx, base, result_subdir, result_base)
        else:
            raise ValueError(f"节点 {cur} 的 kind={kind} 暂不支持(当前实现 script / agent_skill)")
        ctx["step"][cur] = result
        visited += 1
        if visited > 1000:
            raise RuntimeError("节点访问超过 1000,疑似 transition 成环")
        tr = node.get("transition") or {}
        cur = None if tr.get("end") else tr.get("next_on_success")

    if missing:                                          # 收尾汇总:这轮缺了啥、影响了啥、怎么补
        prov = sorted(set(required) & provided)
        print("\n⚠ 数据源缺口汇总(本轮为**部分数据**运行):")
        print(f"  plan 需要: {', '.join(required)};本次提供: {', '.join(prov) if prov else '(无)'}")
        for k, ids in missing.items():
            print(f"  - 缺 {k} → 已跳过步骤: {', '.join(ids)};该源绑定的判据本轮无事实,"
                  "RCA 按「数据不足/未覆盖」对待(rca_input 已标 missing_indicators)")
        print("  结论只代表已提供数据能看到的部分;补齐后重跑: "
              + " ".join(f"--data {k}=<文件>" for k in missing) + "(runner 会自动归档本轮)。")
    print("SCoT 执行流程完成(script 步骤已跑;agent_skill/RCA 待对应技能完成,见上方提示)。")


if __name__ == "__main__":
    main()
