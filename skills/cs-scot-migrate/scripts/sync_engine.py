#!/usr/bin/env python3
"""维护用:把内置引擎 engine/ 与同仓 cs-scot-generate / cs-scot-execute 的当前版对齐。
引擎文件(内核/函数库/调优表往返/骨架/runner)在 generate/execute 侧改了之后跑一次本脚本,
迁移技能就继续自包含。日常使用**不需要**跑它。"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                  # …/skills/cs-scot-migrate/scripts
ENGINE = HERE.parent / "engine"
SKILLS = HERE.parents[1]                                # …/skills
SRC = {
    "condition_kernels.py": SKILLS / "cs-scot-generate" / "scripts",
    "custom_lib.py": SKILLS / "cs-scot-generate" / "scripts",
    "export_tree_excel.py": SKILLS / "cs-scot-generate" / "scripts",
    "import_tree_excel.py": SKILLS / "cs-scot-generate" / "scripts",
    "assemble_scenario.py": SKILLS / "cs-scot-generate" / "scripts",
    "runner.py": SKILLS / "cs-scot-execute" / "scripts",
}

def main() -> None:
    changed = []
    for name, d in SRC.items():
        src = d / name
        if not src.exists():
            sys.exit(f"源文件缺失:{src}(在技能仓根目录下跑本脚本)")
        dst = ENGINE / name
        if not dst.exists() or dst.read_text(encoding="utf-8") != src.read_text(encoding="utf-8"):
            shutil.copy2(src, dst)
            changed.append(name)
    print(f"内置引擎已对齐:{'刷新 ' + ', '.join(changed) if changed else '全部一致,无需动'}")

if __name__ == "__main__":
    main()
