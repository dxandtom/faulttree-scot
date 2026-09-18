#!/usr/bin/env python3
"""新格式故障树 Excel(多页签 + 判断列按数据源分列)→ 层级 md + tree_index。

与老 `excel_to_tree.py` 的差异(老脚本一行不改、老格式照常):
- **多页签**:遍历所有页签;表头里没有 `Ln` 层级列的(非树/说明页)自动跳过;
  页签名带 `old`/`旧` 的默认跳过(过期版),`--include-old` 可保留。
- **判断列按源分列**:`CHR判断方法 / X2 cause / RB日志判断方法 / 配置数据(核查/判断方法) /
  DBG日志(DEBUG) / 信令判断方法 / 话统(PFM) …` —— 表头**模糊匹配**成规范源标签
  (CHR/X2/RB/配置/DBG/信令/话统),落进节点的 `data_source`,**列头自带"查哪个源"、给绑定预路由**;
  判据没按源分列的(混血表的 `Judgment Condition` 单列/「判断条件」)归 **通用** 标签(无预路由,
  路由靠模型读判据原文);同层 L n 与 L n(Chinese) 双列只取中文列(空格逐格回退英文格)。
- **按场景名(L2)跨页签提取**:输入 `--start-node "NSA场景接入失败"`,把**所有树页签**里该 L2
  场景的行收集起来,按"根→叶"路径合并成**一棵**从 L2 起的子树;同一节点的判断按"源标签 + 出处页签"累积。
  (同名场景散在 NSA NR / NSA LTE 等多页 = 不同侧视角,合并成超集;喂数据时缺的源自动数据不足、自然剪掉。)
  `--sheet "<页签名>"` 可限定只用某一页(等价"按页签名选")。
- **元数据**:`和基础诊断对接KPI索引 / 解决方案 / 备注` 存为节点元数据(**不进判断条件**);
  `数据链接` 丢弃;`产品 / 制式` 不参与建树分支。
- 输出契约与老脚本同形(`<短名>.md` + `<短名>.tree_index.json`);**文件/目录名用英文短名(slug)**:
  自动取场景所在层的英文列转小写连字符(如 layer-3-service-issues),取不到沿用中文名、可 --slug 指定;
  中文场景名保留在 index.scenario。节点多了可选 `meta`/`sheets` 字段(additive,老消费方忽略即可)。
- **推荐解决方案**:`Recommend Solution(Chinese/English)` 列按节点收进 `tree/solutions.json`
  (不进树、不进判据、不进裁决上下文;RCA 报根因时按 node_id 点查,给结论附解决方案)。

用法:
    excel_to_tree_v2.py <xlsx> --list                          # 列出所有可输入的场景名(L2)+ 各自出处页签
    excel_to_tree_v2.py <xlsx> --start-node "NSA场景接入失败"   # 跨页签合并出该场景子树
    excel_to_tree_v2.py <xlsx> --start-node "NSA场景接入失败" --sheet "NSA NR"   # 只用某一页
读 xlsx 走同目录 xlsx_reader.py(零三方依赖,不需要 openpyxl)。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from excel_to_tree import _clean_text, _infer_combine, _is_skip_judgment, scenario_out_dir  # 复用老内核的通用件

_LEVEL_RE = re.compile(r"^L(\d+)(?:\(Chinese\))?$")          # 新格式 L1.. / 老格式 L1(Chinese) 都认
_OLD_RE = re.compile(r"old\b|旧", re.IGNORECASE)            # 页签名带 old/旧 = 过期版,默认跳过
DEFAULT_START_LEVEL = 2                                      # 背景业务知识:场景节点通常在 L2
# 判断列表头 → 规范源标签(按关键词模糊匹配,兼容各页签的命名差异)。顺序无所谓,一个表头通常只含一个源词。
_SRC_KEYWORDS: List[Tuple[str, str]] = [
    ("CHR", "CHR"), ("X2", "X2"), ("RB", "RB"), ("DBG", "DBG"), ("DEBUG", "DBG"),
    ("信令", "信令"), ("配置", "配置"), ("话统", "话统"), ("PFM", "话统"),
]


def classify(h: str) -> Tuple[str, Any]:
    """一个表头列归类:('level',层级) / ('method',源标签) / ('meta',元数据名) / ('drop',原因)。"""
    h = (h or "").strip()
    m = _LEVEL_RE.match(h)
    if m:
        return ("level", int(m.group(1)))
    if "KPI索引" in h or "基础诊断对接KPI" in h:
        return ("meta", "KPI索引")
    if "解决方案" in h:
        return ("meta", "解决方案")
    if h == "备注" or h.startswith("备注"):
        return ("meta", "备注")
    if "数据链接" in h:
        return ("drop", "链接")
    if h in ("产品", "制式"):
        return ("drop", "产品制式")
    for kw, tag in _SRC_KEYWORDS:                            # 含源关键词 → 判断方法列
        if kw.lower() in h.lower():
            return ("method", tag)
    # 通用判据列(老格式单列判据,常见于"混血"表:v2 层级列 + v1 判据列,如数通树)——不认它
    # 会把整棵树的判据**静默丢光**(建出 0 判据的空树,下游只能瞎猜)。
    # 标签"通用"= 判据未按源分列,路由靠模型读判据原文。
    if "judgment condition" in h.lower() or "判断条件" in h:
        return ("method", "通用")
    return ("drop", None)


def sheet_schema(header: List[str]):
    """表头 → (层级列[(列名,层级)]升序, 判断列{列名:源标签}, 元数据列{列名:名}, 层级备选列{层级:[列名]})。

    「混血」表(同层有 L1 与 L1(Chinese) 双列、判据中英双列)的双列处理:
    - 同一层级多列 → 主列取「(Chinese)」那列(业务语言);其余列进**备选表**,取值时逐格回退
      (某行中文格空、英文格有值 → 用英文格,不整列丢弃)——否则中英文名会被当成两层节点
      串进路径,树深翻倍;整列取舍又会把漏译的行静默挤层/丢支。
    - **仅「通用」标签**(Judgment Condition 中英双列=同一判据的翻译)去重:优先留列名带
      Chinese 的那列,防同一判据中英文拼接。其他源标签的多列(如 话统+PFM、DBG+DEBUG)是
      **不同的判断列**,全部保留、由 build_index 按标签累积——别按标签收敛(会静默丢整列判据)。"""
    levels, methods, metas = [], {}, {}
    for h in header:
        kind, tag = classify(h)
        if kind == "level":
            levels.append((h, tag))
        elif kind == "method":
            if tag == "通用" and tag in methods.values():    # 通用判据中英双列:留 Chinese 那列
                cur = next(c for c, t in methods.items() if t == tag)
                if "chinese" in h.lower() and "chinese" not in cur.lower():
                    del methods[cur]
                    methods[h] = tag
            else:
                methods[h] = tag
        elif kind == "meta":
            metas[h] = tag
    by_lvl: Dict[int, List[str]] = {}
    for c, l in levels:
        by_lvl.setdefault(l, []).append(c)
    primary, fallback = [], {}
    for l, cols in by_lvl.items():
        pick = next((c for c in cols if "chinese" in c.lower()), cols[0])
        primary.append((pick, l))
        rest = [c for c in cols if c != pick]
        if rest:
            fallback[l] = rest
    primary.sort(key=lambda x: x[1])
    return primary, methods, metas, fallback


def _lvl_value(row, col: str, fb_cols) -> str:
    """某行某层的取值:主列(中文)优先,空则逐格回退备选列(英文)——漏译一格不丢一支。"""
    v = (row.get(col) or "").strip()
    if v:
        return v
    for c in fb_cols or ():
        v = (row.get(c) or "").strip()
        if v:
            return v
    return ""


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")   # 中日韩统一表意文字(含扩展A/兼容区)


def slugify(name: str):
    """英文名 → 文件/目录用短名(slug):小写、非字母数字一律转 '-'、合并去首尾。
    含中文字符(英文格没翻译、填的还是中文)或转换后为空 → 返回 None(不可用,由上层用备用做法)。"""
    s = str(name or "").strip()
    if not s or _CJK_RE.search(s):
        return None
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if len(s) > 60:                                      # 文件/目录名要跨平台安全,超长英文名截到词边界
        s = s[:60].rstrip("-")
    return s or None


def find_english_slug(tree_sheets, scenario: str):
    """任意层级:找「场景名」所在格的同层英文备选列取值,第一个能转成合法 slug 的当英文短名。
    (混血表同层是 Lx(Chinese)/Lx 双列,主列=中文、备选列=英文——场景名住哪层就取哪层的英文格。)"""
    for _name, header, rows in tree_sheets:
        levels, _, _, fb = sheet_schema(header)
        for col, lvl in levels:
            fbc = fb.get(lvl)
            if not fbc:
                continue
            for row in rows:
                if (row.get(col) or "").strip() == scenario:
                    for c in fbc:
                        s = slugify((row.get(c) or "").strip())
                        if s:
                            return s
    return None


def resolve_scenario_alias(tree_sheets, scenario: str):
    """--start-node 传的是英文列(备选列)里的叫法时,翻译成主列真名。
    背景:混血双列表的 --list 显示中文主列名,但老版脚本曾把英文列名列出来——用户/老会话
    记住的英文名在新版下点不中,直接报"未找到"会让人以为脚本坏了。返回真名或 None。"""
    for _name, header, rows in tree_sheets:
        levels, _m, _me, fb = sheet_schema(header)
        for col, lvl in levels:
            for c in fb.get(lvl) or ():
                for row in rows:
                    if (row.get(c) or "").strip() == scenario:
                        v = _lvl_value(row, col, fb.get(lvl))
                        if v and v != scenario:
                            return v
    return None


def is_tree_sheet(header: List[str]) -> bool:
    return any(classify(h)[0] == "level" for h in header)


def default_out(anchor: Path, stem: str, slug, scenario: str):
    """默认落点:**直接生成技能形态**——scot技能/<短名>/(execute 的执行指引与脚本+SKILL 身份),产物=其 assets/,
    流水线跑完技能即成品,免掉单独打包一步(调优/迁移本就支持在 assets 上就地做)。
    直接生成的前提:有英文短名(云端技能名要英文)+ 本机 skills 目录下有 cs-scot-execute / fault-tree-rca;
    不满足 → 退回老的 scot产物/<场景名>/ 产物形态,明确提示最后跑 package_scenario.py 单独打包。
    返回 (技能目录|None, 产物目录, 说明)。"""
    skill_dir = anchor / "scot技能" / stem
    if (skill_dir / "SKILL.md").exists() and (skill_dir / "assets").is_dir():
        return skill_dir, skill_dir / "assets", "技能已存在,续用(重跑建树直接更新其 assets)"
    try:
        import package_scenario                          # 单文件部署(只带 v1/v2/xlsx_reader 三件套)
    except ImportError:                                  # 没有打包脚本:不许崩,退回产物形态照常出树
        return None, scenario_out_dir(anchor, stem), \
            "打包脚本 package_scenario.py 不在(单文件部署)——只生成产物目录;要直接生成技能请带全 scripts/ 目录"
    exe = package_scenario.find_material("cs-scot-execute")          # ①技能内嵌 ②同一 skills 目录下
    rca = package_scenario.find_material("fault-tree-rca")
    if not slug:
        return None, scenario_out_dir(anchor, stem), \
            "未取到英文短名(技能名需英文)——只生成产物目录;加 --slug 重跑可直接生成技能,或最后跑 package_scenario.py"
    if not (exe / "SKILL.md").exists() or not (rca / "SKILL.md").exists():
        return None, scenario_out_dir(anchor, stem), \
            ("找不到打包素材 cs-scot-execute / fault-tree-rca(技能内嵌与同一 skills/ 目录下都没有)"
             "——退回产物形态,最后跑 package_scenario.py")
    if skill_dir.exists():
        return None, scenario_out_dir(anchor, stem), \
            f"{skill_dir} 已存在但不是完整技能(缺 SKILL.md 或 assets/)——不动它,只生成产物目录;人工清理后重跑可直接生成技能"
    import package_scenario
    package_scenario.scaffold_skill(skill_dir, scenario, stem, exe, rca)
    return skill_dir, skill_dir / "assets", "已搭好技能目录框架(执行器脚本+rca 副本+zip2csv)"


def read_sheets(path: str):
    """读所有页签 → [(页签名, 表头, 行dict列表)]。"""
    import xlsx_reader                                   # 同目录零依赖读取器(不需要 openpyxl)
    return xlsx_reader.iter_sheets(path)


def list_scenarios(tree_sheets, start_level: int) -> Dict[str, List[str]]:
    """场景名(start_level 层,默认 L2)→ 出现在哪些页签。"""
    sc: Dict[str, set] = {}
    for name, header, rows in tree_sheets:
        levels, _, _, fb = sheet_schema(header)
        l2 = next((c for c, l in levels if l == start_level), None)
        if not l2:
            continue
        fb_cols = fb.get(start_level)
        for row in rows:
            v = _lvl_value(row, l2, fb_cols)
            if v:
                sc.setdefault(v, set()).add(name)
    return {k: sorted(v) for k, v in sorted(sc.items())}


def build_index(tree_sheets, scenario: str, start_level: int) -> Dict[str, Any]:
    """跨页签收集 scenario(L2)的行,按路径合并成一棵从 L2 起的子树(扁平权威 index)。
    同一路径节点跨页签/跨行累积:data_source 按源标签累积、meta 累积、sheets 记出处。"""
    nodes: Dict[str, Any] = {}
    root_id: Optional[str] = None
    short: Dict[str, str] = {}
    padded_rows = 0

    def sid(chain: str) -> str:
        if chain not in short:
            short[chain] = "n" + str(len(short) + 1)
        return short[chain]

    solutions: Dict[str, Dict[str, str]] = {}

    for name, header, rows in tree_sheets:
        levels, methods, metas, fb = sheet_schema(header)
        if not levels:
            continue
        # 推荐解决方案列(混血表英文表头,classify 不收):不进树/不进判据,单独按节点收集,
        # 供 RCA 报根因时点查输出——诊断结论"太干巴巴"的补丁,但绝不掺进裁决上下文。
        sol_cols = [(h, "zh" if "chinese" in h.lower() else "en")
                    for h in header if "recommend solution" in h.lower()]
        for row in rows:
            full = [(lvl, _lvl_value(row, c, fb.get(lvl))) for c, lvl in levels]
            full = [(lvl, v) for lvl, v in full if v]
            # 连续同名层折叠:平台 DataExport 常见"向右填充/合并单元格展开"——同一行 L3 之后
            # 几百列全是同一个叶子名,路径变成上千级连环重名链(实测:渲染 md 递归 994 层爆栈)。
            # 只折叠**相邻**同名格,真实树不存在"同名节点直接挂在同名父下"的形态,零误伤。
            deduped = full[:1]
            for it in full[1:]:
                if it[1] != deduped[-1][1]:
                    deduped.append(it)
            if len(deduped) != len(full):
                padded_rows += 1
            full = deduped
            if not full:
                continue
            names = [v for _, v in full]
            sl = start_level
            if len(full) >= sl and full[sl - 1][1] == scenario:   # 优先:场景名在 L2
                path = full[sl - 1:]
            elif scenario in names:                                # 备用做法:在别的层找到
                path = full[names.index(scenario):]
            else:
                continue
            # 本行各源判断 + 元数据(挂到最深节点)
            mth: Dict[str, str] = {}
            for c, tag in methods.items():
                v = _clean_text(row.get(c))
                if v and not _is_skip_judgment(v):
                    mth[tag] = (mth[tag] + "\n" + v) if tag in mth else v
            meta: Dict[str, str] = {}
            for c, tag in metas.items():
                v = _clean_text(row.get(c))
                if v:
                    meta.setdefault(tag, v)
            parent_id, parts = None, []
            for j, (lvl, nm) in enumerate(path):
                parts.append(nm)
                nid = sid("/".join(parts))
                nd = nodes.setdefault(nid, {"name": nm, "parent": parent_id, "level": lvl, "code": None,
                                            "children": [], "condition": None, "combine": None,
                                            "data_source": {}, "pass_through": True, "sheets": [], "meta": {}})
                if parent_id and nid not in nodes[parent_id]["children"]:
                    nodes[parent_id]["children"].append(nid)
                if j == 0:
                    root_id = nid
                if j == len(path) - 1:
                    if name not in nd["sheets"]:
                        nd["sheets"].append(name)
                    for tag, v in mth.items():
                        prev = nd["data_source"].get(tag)
                        nd["data_source"][tag] = (prev + "\n" + v) if (prev and v not in prev) else (prev or v)
                    if nd["data_source"]:
                        cond = "\n".join(f"[{t}] {nd['data_source'][t]}" for t in nd["data_source"])
                        nd["condition"] = cond
                        nd["combine"] = _infer_combine(cond)
                        nd["pass_through"] = False
                    for tag, v in meta.items():
                        nd["meta"].setdefault(tag, v)
                    for c, lang in sol_cols:
                        sv = _clean_text(row.get(c))
                        if sv:
                            solutions.setdefault(nid, {}).setdefault(lang, sv)
                parent_id = nid
    if padded_rows:
        print(f"ℹ 层级列右向填充折叠:{padded_rows} 行存在相邻同名层级格(DataExport 常见填充/"
              "合并单元格展开),已按同一节点折叠——路径深度以折叠后为准")
    return {"scenario": scenario, "root": root_id, "nodes": nodes, "solutions": solutions}


def index_to_markdown(index: Dict[str, Any]) -> str:
    """扁平 index → #/##/### 层级 md;判断条件(按源标签)+ 元数据紧跟节点。
    显式栈迭代而非递归:异常深的树(层级列识别错位/填充数据)不该炸 RecursionError,
    要么正常渲染、要么(万一 index 被外部改出环)明确点名坏在哪个节点。"""
    nodes = index["nodes"]
    lines: List[str] = []
    if index.get("root"):
        stack = [(index["root"], 1)]
        on_path: set = set()                             # 防环:字典树建法本身无环,守卫护手改 index 的情形
        seen_depth: Dict[str, int] = {}
        while stack:
            nid, depth = stack.pop()
            if depth < 0:                                # 回溯标记:该节点子树渲染完,离开路径
                on_path.discard(nid)
                continue
            if nid in on_path:
                chain = "→".join(str(nodes.get(x, {}).get("name") or x) for x in list(seen_depth)[-5:])
                raise SystemExit(f"树结构成环:节点「{nodes.get(nid, {}).get('name') or nid}」"
                                 f"出现在自己的子孙链上(…{chain});tree_index 被改坏,重新建树")
            on_path.add(nid)
            seen_depth[nid] = depth
            nd = nodes[nid]
            lines.append("#" * depth + " " + nd["name"])
            if nd.get("condition"):
                lines.extend(nd["condition"].split("\n"))
            for tag, v in (nd.get("meta") or {}).items():
                lines.append(f"（{tag}）{v}")
            stack.append((nid, -1))                      # 先压回溯标记,再逆序压子节点(保持原渲染顺序)
            for ch in reversed(nd["children"]):
                stack.append((ch, depth + 1))
    return "\n".join(lines) + "\n"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="新格式(多页签)故障树 Excel → md + tree_index")
    ap.add_argument("excel_path")
    ap.add_argument("--start-node", help="场景名(L2);跨页签收集该场景、合并成子树")
    ap.add_argument("--start-level", type=int, default=DEFAULT_START_LEVEL)
    ap.add_argument("--sheet", help="只用指定页签(等价按页签名选);不给=所有树页签")
    ap.add_argument("--list", action="store_true", help="列出所有可输入场景名(L2)+ 出处页签")
    ap.add_argument("--include-old", action="store_true", help="保留名字带 old/旧 的页签(默认跳过)")
    ap.add_argument("--bind-top-levels", action="store_true",
                    help="让 L1/L2 高层节点的判据也参与绑定/阻断(默认不判:高层判据多为场景分类,"
                         "绑成判定会把全树确证卡死)")
    ap.add_argument("--slug", help="英文短名(输出文件/目录名用,如 layer-3-service-issues);"
                                   "默认自动取场景所在层的英文列转小写连字符,取不到则沿用中文名")
    ap.add_argument("-o", "--output")
    ap.add_argument("--out-base")
    args = ap.parse_args()

    sheets = read_sheets(args.excel_path)
    tree, nontree, old = [], [], []
    for n, h, r in sheets:
        if not is_tree_sheet(h):
            nontree.append(n)
        elif _OLD_RE.search(n) and not args.include_old:
            old.append(n)
        elif args.sheet and n != args.sheet:
            continue
        else:
            tree.append((n, h, r))

    if args.list:
        sc = list_scenarios(tree, args.start_level)
        print(f"树页签 {len(tree)} 个 | 非树跳过 {nontree or '无'} | 旧版跳过 {old or '无'}")
        print(f"可输入场景名(L{args.start_level}) 共 {len(sc)}:")
        for name, where in sc.items():
            print(f"  {name}    ←  {where}")
        return

    if not args.start_node:
        print("请用 --start-node「场景名」选场景,或 --list 看有哪些。", file=sys.stderr)
        sys.exit(2)

    index = build_index(tree, args.start_node, args.start_level)
    if not index.get("root"):
        alias = resolve_scenario_alias(tree, args.start_node)
        if alias:
            print(f"ℹ 「{args.start_node}」是英文列里的叫法,主列真名是「{alias}」——已按真名提取"
                  "(--list 显示的是主列名;老版脚本曾列英文名,两代叫法都认)")
            args.start_node = alias
            index = build_index(tree, alias, args.start_level)
    if not index.get("root"):
        print(f"未在任何树页签找到场景「{args.start_node}」(L{args.start_level},主列与英文备选列都查过)。"
              "用 --list 核对名字。", file=sys.stderr)
        sys.exit(1)
    # 深度检查:正常故障树十来层;几十层以上=层级列识别错位/异常数据,明确报出来,别静默产出坏树
    def _depth(nid: str) -> int:
        d, cur = 0, nid
        while cur:
            d += 1
            cur = index["nodes"].get(cur, {}).get("parent")
        return d
    max_d = max((_depth(n) for n in index["nodes"]), default=0)
    if max_d > 32:
        print(f"⚠ 树深 {max_d} 层,远超正常故障树——层级列识别可能错位(--list 核对场景、检查表头),"
              "继续输出但下游多半不可用", file=sys.stderr)
    # 「高层不判断」规则(简单粗暴,用户定案):L1/L2 这种靠根的层级,判据多为场景分类/选路说明,
    # 不是故障条件——绑成判定会把整树确证卡死(实测:根节点"判断属于哪种场景"不成立 → 全树只能疑似)。
    # 场景**根节点不看母表层号一律豁免**(实测:场景在母表更深层时,根的"参考下级节点"判据被派了绑定,
    # 谁也绑不上 → 数据不足挂在全树每条路径上)。
    # 处理:带判据的 L≤2 节点与场景根标 pass_through(不阻断、不参与绑定),判据文本保留供人读;
    # 某棵树确实要在高层判(少见)→ --bind-top-levels 关掉本规则。
    if not args.bind_top_levels:
        topjudge = [nid for nid, nd in index["nodes"].items()
                    if nd.get("condition") and ((nd.get("level") or 99) <= 2 or nid == index.get("root"))]
        for nid in topjudge:
            index["nodes"][nid]["pass_through"] = True
        if topjudge:
            print(f"高层不判断:L1/L2/场景根带判据节点 {len(topjudge)} 个已标 pass_through(不阻断路径、不参与绑定;"
                  "判据保留供人读。确要在高层判 → 重跑加 --bind-top-levels)")
    solutions = index.pop("solutions", {})              # 单独写文件,不进 tree_index(别膨胀树契约)
    md = index_to_markdown(index)

    # 英文短名:--slug 手工指定 > 场景所在层英文列自动转 > 场景名本身(本就是英文时)> 沿用中文名(现状)
    if args.slug:
        slug = slugify(args.slug)
        if not slug:
            print(f"--slug「{args.slug}」不可用:须为英文(会转成小写连字符,如 layer-3-service-issues)",
                  file=sys.stderr)
            sys.exit(2)
    else:
        slug = find_english_slug(tree, args.start_node) or slugify(args.start_node)
    stem = slug or args.start_node
    if slug:
        index["slug"] = slug

    output_path = args.output
    out_dir = skill_dir = None
    skill_note = ""
    if not output_path:
        anchor = Path(args.out_base).expanduser() if args.out_base else Path(args.excel_path).expanduser().resolve().parent
        skill_dir, out_dir, skill_note = default_out(anchor, stem, slug, args.start_node)
        output_path = str(out_dir / "tree" / f"{stem}.md")
    outp = Path(output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(md, encoding="utf-8")
    outp.with_suffix(".tree_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    if solutions:
        sol_path = outp.parent / "solutions.json"
        sol_path.write_text(json.dumps(solutions, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # 重跑没产出方案(改了表头/换了页签/清了格)→ 必须清掉旧表:node_id(nN)按遭遇序分配,
        # 树一变旧表的 nN 就错位,陈旧 solutions 会把别的节点的方案错挂给根因(全链无人再校验它)。
        (outp.parent / "solutions.json").unlink(missing_ok=True)
    print(f"已输出子树: {output_path}")
    print(f"已输出权威 index: {outp.with_suffix('.tree_index.json')}")
    if slug:
        print(f"英文短名(slug): {slug}(文件/目录名用;中文场景名保留在 index.scenario)")
    else:
        print("⚠ 未取到可用英文短名(场景所在层英文列缺失/未翻译):文件与目录沿用中文名。"
              "需要英文名请加 --slug <english-name> 重跑。", file=sys.stderr)
    if solutions:
        print(f"已输出解决方案表: {outp.parent / 'solutions.json'}(节点 {len(solutions)} 个;"
              "RCA 报根因时点查,不进裁决上下文)")
    src_sheets = sorted({s for nd in index["nodes"].values() for s in nd.get("sheets", [])})
    n_cond = sum(1 for nd in index["nodes"].values()
                 if nd.get("condition") and not nd.get("pass_through"))
    print(f"场景「{args.start_node}」合并自页签: {src_sheets};节点数 {len(index['nodes'])},带判据节点 {n_cond}")
    if n_cond == 0:
        print("⚠⚠ 带判据节点为 0:这棵子树没有任何判断条件,多半是格式没被识别"
              "(判据列表头对不上关键词/通用判据列),**停下检查 Excel 表头,别往下绑定**"
              "——0 判据的树绑出来的只能是瞎猜。", file=sys.stderr)
    if out_dir is not None:
        print(f"本场景产物目录: {out_dir}")
        if skill_dir is not None:
            print(f"本场景技能目录: {skill_dir}({skill_note})")
            print("  已是打包技能形态:后续步骤 --out 一律用上面的产物目录(=技能的 assets/),"
                  "流水线跑完技能即成品,**无需再跑 package_scenario 打包**。")
        else:
            print(f"⚠ 未直接生成技能形态:{skill_note}", file=sys.stderr)


if __name__ == "__main__":
    main()
