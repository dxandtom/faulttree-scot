#!/usr/bin/env python3
"""场景技能打包:execute 骨架 + 场景诊断产物 + fault-tree-rca 副本 → 一个可上传的场景技能目录。

为什么要它:云端"一个场景一个技能"的用法,此前要手工建 assets/、往里拷三处文件、再改技能名——
每个场景来一遍,步骤多、容易漏。本脚本一条命令出货:

    <slug>/                     ← 技能目录(名=场景英文短名,如 layer-3-service-issues)
      SKILL.md                  ← execute 的执行逻辑(frontmatter 的 name/description 重写,正文不动)
      scripts/  references/     ← execute 的 runner 等
      assets/                   ← 场景诊断产物(plan.json / scripts / tree;已排除 overview、dispatch、
        fault-tree-rca/            runs、result、__pycache__、*.py.bak 等临时物)+ 根因分析规则副本

打包后的用法(云端):`/<slug> 数据在 <zip>,帮我诊断` —— 一句话执行,见 execute SKILL.md
「场景打包形态(assets/)」一节的默认约定。

用法:
    package_scenario.py --out <场景产物目录>
        [--dest <目标根目录,默认=产物目录的父目录>] [--name <技能名覆盖,英文小写连字符>]
        [--execute-skill <目录>] [--rca-skill <目录>] [--force 覆盖已存在的目标]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from excel_to_tree_v2 import slugify  # noqa: E402  与步骤1同一套短名规则,别各造一套

# 不进技能包的临时物(与 generate 步骤4"交付前清场"清单同源;这里再兜一层,清没清都干净)
_SKIP_DIRS = {"__pycache__", "overview", "dispatch", "runs", "result"}


def _ignore(_dirpath, names):
    out = set()
    for n in names:
        if (n in _SKIP_DIRS or n.endswith(".pyc") or n.endswith(".py.bak")
                or n == "conditions_all.json" or ("_conditions" in n and n.endswith(".py"))):
            out.add(n)
    return out


def rewrite_skill_md(path: Path, name: str, scenario: str) -> None:
    """只重写 frontmatter 的 name/description(技能身份),正文(执行逻辑)一字不动。"""
    txt = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", txt, re.S)   # 容忍 CRLF(Windows autocrlf 检出)
    if not m:
        print(f"[package] SKILL.md 缺 frontmatter,无法改名: {path}", file=sys.stderr)
        sys.exit(1)
    desc = (f"「{scenario}」场景故障根因诊断:把数据(zip/目录/文件)给过来即可,自动解析、"
            f"执行 SCoT 计划,输出根因、故障树路径与推荐解决方案(诊断资源内置于本技能 assets/)。"
            f"用户报「{scenario}」相关故障、要做该场景根因定位时用。")
    fm = re.sub(r"(?m)^name:.*$", f"name: {name}", m.group(1), count=1)
    fm = re.sub(r"(?m)^description:.*$", f"description: {desc}", fm, count=1)
    path.write_text(f"---\n{fm}\n---\n" + txt[m.end():], encoding="utf-8")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="场景技能打包(execute 骨架 + 诊断产物 + rca 规则副本)")
    ap.add_argument("--out", required=True, help="场景产物目录(assemble 归集过、且已跑完绑定的那个)")
    ap.add_argument("--dest", help="技能放到哪个目录下;默认=产物目录的父目录")
    ap.add_argument("--name", help="技能名覆盖(英文小写连字符);默认用产物的英文短名(slug)")
    ap.add_argument("--execute-skill", default=str(here.parent.parent / "cs-scot-execute"))
    ap.add_argument("--rca-skill", default=str(here.parent.parent / "fault-tree-rca"))
    ap.add_argument("--force", action="store_true", help="目标技能目录已存在时先删再建")
    args = ap.parse_args()

    out = Path(args.out).expanduser().resolve()
    if not (out / "plan.json").exists():
        print(f"不合格:{out} 下没有 plan.json——先走完 generate 流程(assemble 归集+绑定)再打包,"
              "没有计划的技能包上传了也跑不了。", file=sys.stderr)
        sys.exit(1)

    scenario, slug = None, None
    mf = out / "manifest.json"
    if mf.exists():
        try:
            d = json.loads(mf.read_text(encoding="utf-8"))
            scenario, slug = d.get("scenario"), d.get("slug")
        except Exception:
            pass
    if not scenario:                                     # 老产物没铭牌:从 tree_index 兜底取名
        for ti in sorted((out / "tree").glob("*.tree_index.json")):
            try:
                d = json.loads(ti.read_text(encoding="utf-8"))
                scenario, slug = d.get("scenario"), d.get("slug")
                break
            except Exception:
                continue
    if not scenario:
        print(f"不合格:读不到场景名(manifest.json / tree/*.tree_index.json 都没有)——产物不完整。",
              file=sys.stderr)
        sys.exit(1)

    name = slugify(args.name) if args.name else slug
    if args.name and not name:
        print(f"--name「{args.name}」不可用:须为英文(会转成小写连字符)。", file=sys.stderr)
        sys.exit(2)
    if not name:
        print(f"产物没有英文短名(步骤1没取到英文列):用 --name <english-name> 指定技能名,"
              f"或回步骤1加 --slug 重跑。场景:「{scenario}」", file=sys.stderr)
        sys.exit(2)

    exe = Path(args.execute_skill).expanduser().resolve()
    rca = Path(args.rca_skill).expanduser().resolve()
    for p, what in ((exe, "execute 技能"), (rca, "fault-tree-rca 技能")):
        if not (p / "SKILL.md").exists():
            print(f"找不到{what}({p} 下无 SKILL.md)——用 --execute-skill/--rca-skill 指路。",
                  file=sys.stderr)
            sys.exit(2)

    # 默认放到与「scot产物」平级的「scot技能」容器下——绝不能默认到 out.parent:
    # 产物目录名=slug=技能名,dest 会与产物目录本身同路径,--force 一开就是先删产物再打空包(实测过)。
    if args.dest:
        dest_root = Path(args.dest).expanduser().resolve()
    elif out.parent.name == "scot产物":
        dest_root = out.parent.parent / "scot技能"
    else:
        dest_root = out.parent / "scot技能"
    dest = (dest_root / name).resolve()
    for src, what in ((out, "场景产物目录"), (exe, "execute 技能源"), (rca, "fault-tree-rca 技能源")):
        if dest == src or dest in src.parents or src in dest.parents:
            print(f"目标目录与{what}重叠:{dest} ↔ {src}——继续会把源删掉/套娃,换个 --dest。",
                  file=sys.stderr)
            sys.exit(2)
    if dest.exists():
        if not args.force:
            print(f"目标已存在:{dest}(确认要覆盖就加 --force)", file=sys.stderr)
            sys.exit(2)
        shutil.rmtree(dest)

    shutil.copytree(exe, dest, ignore=_ignore)                       # 执行骨架
    rewrite_skill_md(dest / "SKILL.md", name, scenario)              # 技能身份:名字+场景描述
    shutil.copytree(out, dest / "assets", ignore=_ignore)            # 诊断产物(排除临时物)
    shutil.copytree(rca, dest / "assets" / "fault-tree-rca", ignore=_ignore)   # 根因规则副本

    n_scripts = len(list((dest / "assets" / "scripts").glob("*_analyzer.py")))
    has_sol = (dest / "assets" / "tree" / "solutions.json").exists()
    print(f"已打包场景技能: {dest}")
    print(f"  场景:「{scenario}」  技能名: {name}")
    print(f"  assets: plan.json + 解析脚本 {n_scripts} 个 + tree/"
          + ("(含 solutions.json 解决方案表)" if has_sol else "(无 solutions.json)")
          + " + fault-tree-rca 规则副本")
    print(f"  上传/安装整个目录后,一句话即可用: /{name} 数据在 <zip或目录>,帮我诊断")


if __name__ == "__main__":
    main()
