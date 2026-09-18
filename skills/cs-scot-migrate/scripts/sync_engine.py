#!/usr/bin/env python3
"""维护用:把内置引擎目录 engine/ 与同一 skills 目录下其他技能的当前版本对齐。
engine/ 里全是这些技能文件的**副本**,只在 cs-scot-migrate 被单独部署(旁边没有其他技能)时作备用;
其他技能在场时 cs-scot-migrate 一律优先使用它们的源文件。副本分两类:
  · 单文件脚本(SRC):cs-scot-generate / cs-scot-execute / datacom-fault-tree-tester 的引擎脚本,
    用于补全产物目录的 scripts/;
  · 整目录素材(DIRS):fault-tree-rca、zip2csv 两个技能的整个目录 + cs-scot-execute 的 SKILL.md,
    用于刷新打包技能的 assets/fault-tree-rca(含树视图 render_tree.py)、assets/zip2csv、SKILL.md 正文。
副本靠手工维护必然出现漂移(实测 runner.py 曾落后源文件 4 天),所以副本只能由本脚本生成、不许手改:
    python skills/cs-scot-migrate/scripts/sync_engine.py          # 从源覆盖副本(多余文件一并删)
    python skills/cs-scot-migrate/scripts/sync_engine.py --check  # 只核对,有漂移/游离文件退出码 1(测试用)
发行包 cs-scot-generate-optimize 里这些技能都在包的根目录,engine/ 整个不进包(package_skill 排除),
包内没有重复文件。日常使用**不需要**运行本脚本。"""
import fnmatch
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                  # …/skills/cs-scot-migrate/scripts
ENGINE = HERE.parent / "engine"
SKILLS = HERE.parents[1]                                # …/skills
SRC = {
    "condition_kernels.py": SKILLS / "cs-scot-generate" / "scripts",
    "xlsx_reader.py": SKILLS / "cs-scot-generate" / "scripts",
    "custom_lib.py": SKILLS / "cs-scot-generate" / "scripts",
    "export_tree_excel.py": SKILLS / "cs-scot-generate" / "scripts",
    "import_tree_excel.py": SKILLS / "cs-scot-generate" / "scripts",
    "assemble_scenario.py": SKILLS / "cs-scot-generate" / "scripts",
    "runner.py": SKILLS / "cs-scot-execute" / "scripts",
    "check_data.py": SKILLS / "cs-scot-execute" / "scripts",
    "tree_prune.py": SKILLS / "cs-scot-execute" / "scripts",
    # datacom-fault-tree-tester 侧:契约检查脚本(导入前检查用)+ 共享解析库(交付包缺共享模块时作备用补齐)
    "check_contract.py": SKILLS / "datacom-fault-tree-tester" / "scripts",
    "search_config.py": SKILLS / "datacom-fault-tree-tester" / "scripts",
    "search_log.py": SKILLS / "datacom-fault-tree-tester" / "scripts",
    "search_cmd.py": SKILLS / "datacom-fault-tree-tester" / "scripts",
    "search_diag_txt.py": SKILLS / "datacom-fault-tree-tester" / "scripts",
}
# 整目录素材:engine/<名字>/ ← 源目录;只收录 only 列出的相对路径(None=整目录),按 ignore 排除
_IGNORE = ("__pycache__", "*.pyc", "*.py.bak", "*.bak", "runs", "result*", ".DS_Store")
DIRS = {
    "fault-tree-rca": (SKILLS / "fault-tree-rca", None),
    "zip2csv": (SKILLS / "zip2csv", None),
    "cs-scot-execute": (SKILLS / "cs-scot-execute", ("SKILL.md",)),
}


def _ignored(rel: Path) -> bool:
    return any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in _IGNORE)


def _dir_files(src: Path, only):
    """源目录里应纳入副本的文件:{相对路径: 源文件}。"""
    if only is not None:
        return {Path(r): src / r for r in only}
    return {q.relative_to(src): q for q in sorted(src.rglob("*"))
            if q.is_file() and not _ignored(q.relative_to(src))}

def main() -> int:
    check = "--check" in sys.argv
    changed, drift = [], []
    for name, d in SRC.items():
        src = d / name
        if not src.exists():
            sys.exit(f"源文件缺失:{src}(在技能仓根目录下跑本脚本)")
        dst = ENGINE / name
        same = dst.exists() and dst.read_bytes() == src.read_bytes()
        if same:
            continue
        if check:
            drift.append(f"engine/{name}  ≠  {src.relative_to(SKILLS)}")
        else:
            shutil.copy2(src, dst)
            changed.append(name)
    n_dir = 0
    for dname, (src_dir, only) in DIRS.items():
        if not src_dir.is_dir():
            sys.exit(f"源目录缺失:{src_dir}(在技能仓根目录下跑本脚本)")
        want = _dir_files(src_dir, only)
        n_dir += len(want)
        ddir = ENGINE / dname
        for rel, src in want.items():
            if not src.exists():
                sys.exit(f"源文件缺失:{src}")
            dst = ddir / rel
            if dst.exists() and dst.read_bytes() == src.read_bytes():
                continue
            if check:
                drift.append(f"engine/{dname}/{rel.as_posix()}  ≠  {src.relative_to(SKILLS).as_posix()}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                changed.append(f"{dname}/{rel.as_posix()}")
        if ddir.is_dir():                                # 副本里多出来的文件:源已删除/改名 → 副本也删除
            for q in sorted(ddir.rglob("*")):
                if q.is_file() and q.relative_to(ddir) not in want and not _ignored(q.relative_to(ddir)):
                    if check:
                        drift.append(f"engine/{dname}/{q.relative_to(ddir).as_posix()}  源里没有(游离)")
                    else:
                        q.unlink()
                        changed.append(f"删 {dname}/{q.relative_to(ddir).as_posix()}")
    extra = sorted(q.name for q in ENGINE.glob("*.py") if q.name not in SRC)
    extra += sorted(q.name for q in ENGINE.iterdir() if q.is_dir() and q.name not in DIRS
                    and q.name != "__pycache__")
    if extra:
        print("engine/ 里有不在同步清单的文件/目录(要么加进 SRC/DIRS,要么删掉):" + ", ".join(extra))
    if check:
        if drift:
            print("引擎副本漂移(不带 --check 跑同一命令即同步):\n  " + "\n  ".join(drift))
        else:
            print(f"引擎副本与源一致({len(SRC)} 个平铺脚本 + {n_dir} 个素材文件)")
        return 1 if (drift or extra) else 0
    print(f"内置引擎已对齐:{'刷新 ' + ', '.join(changed) if changed else '全部一致,无需动'}")
    return 1 if extra else 0


if __name__ == "__main__":
    sys.exit(main())
