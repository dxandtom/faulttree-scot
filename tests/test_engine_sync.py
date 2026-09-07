#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引擎副本与发行包重复文件测试:
① migrate/engine/ 副本与同门源文件逐字节一致(sync_engine.py --check);
② package_skill 打出的发行包里不含 migrate/engine/,且全包内没有内容完全相同的重复文件。"""
import hashlib
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PASSED = FAILED = 0


def check(name, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}  {extra}")


print("\n== ① 引擎副本与源一致 ==")
p = subprocess.run([sys.executable, str(REPO / "skills/cs-scot-migrate/scripts/sync_engine.py"), "--check"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
check("sync_engine.py --check 退出码 0(无漂移、无游离文件)", p.returncode == 0, (p.stdout + p.stderr)[-600:])

print("\n== ② 发行包零重复 ==")
out = Path(tempfile.mkdtemp(prefix="pkgdup-"))
p2 = subprocess.run([sys.executable, str(REPO / "skills/cs-scot-generate-optimize/scripts/package_skill.py"),
                     "--out", str(out), "--no-zip"], capture_output=True, text=True, encoding="utf-8",
                    errors="replace", cwd=str(REPO))
pkg = out / "cs-scot-generate-optimize"
check("打包退出码 0", p2.returncode == 0, (p2.stdout + p2.stderr)[-600:])
check("包里没有 migrate/engine/", not (pkg / "migrate" / "engine").exists())
check("包里有同门源(generate/scripts/xlsx_reader.py、cs-scot-execute/scripts/runner.py)",
      (pkg / "generate" / "scripts" / "xlsx_reader.py").exists() and (pkg / "cs-scot-execute" / "scripts" / "runner.py").exists())
by_hash = defaultdict(list)
for f in pkg.rglob("*"):
    if f.is_file() and f.suffix in (".py", ".md", ".json", ".yaml", ".txt") and f.name != "MANIFEST.txt":
        by_hash[hashlib.md5(f.read_bytes()).hexdigest()].append(f.relative_to(pkg).as_posix())
dups = [v for v in by_hash.values() if len(v) > 1]
check("全包内无内容完全相同的重复文件", not dups, repr(dups[:5]))
shutil.rmtree(out, ignore_errors=True)

print(f"\n通过 {PASSED} / {PASSED + FAILED}" + ("  全绿" if not FAILED else "  【有失败!】"))
sys.exit(1 if FAILED else 0)
