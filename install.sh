#!/usr/bin/env bash
# FaultTree-SCoT skills installer (quick copy).
#
# 作用:把本仓 skills/ 下的三个 skill 拷贝(或 --link 软链)到外层 harness
# 的 skills 目录,让 Claude Code / OpenClaw 等能加载它们:
#   - cs-scot-generate   把故障树 Excel 编译成场景诊断 skill(解析脚本 + SCoT 计划)
#   - cs-scot-execute    按 plan.json 顺序执行 SCoT 计划
#   - fault-tree-rca     综合根因分析
#   - cs-scot-benchmark  对 generate 产物跑基准(压缩/稳定/可复现/护栏),出报告
#   - cs-scot-eval       合成故障注入测诊断准确率(自带 ground truth)
#
# 用法:
#   ./install.sh                    # 拷贝到 ~/.claude/skills(默认)
#   ./install.sh --link             # 软链代替拷贝(本地开发,改源即生效)
#   ./install.sh --target /path     # 自定义目标目录
#   ./install.sh --harness openclaw # 目标默认 ~/.openclaw/skills
#   ./install.sh --uninstall        # 移除已安装的拷贝/软链
#   ./install.sh --help
#
# 依赖:skill 脚本仅用 Python 标准库;只有读取 .xlsx 才需要 openpyxl
#       (csv 开箱即用)。需要 xlsx 时:pip install openpyxl
set -euo pipefail

MODE="copy"
TARGET=""
HARNESS="claude_code"
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --link) MODE="link"; shift ;;
    --copy) MODE="copy"; shift ;;
    --target) TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#--target=}"; shift ;;
    --harness) HARNESS="$2"; shift 2 ;;
    --harness=*) HARNESS="${1#--harness=}"; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$HARNESS" in
  claude_code|openclaw) ;;
  *) echo "[install] unknown harness '$HARNESS' — supported: claude_code, openclaw" >&2; exit 2 ;;
esac

if [ -z "$TARGET" ]; then
  if [ "$HARNESS" = "openclaw" ]; then
    TARGET="$HOME/.openclaw/skills"
  else
    TARGET="$HOME/.claude/skills"
  fi
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SKILLS_SRC="$SRC/skills"
DIRS=(cs-scot-generate cs-scot-execute fault-tree-rca fault-tree-rca-debate cs-scot-benchmark cs-scot-eval)

echo "[install] source:  $SKILLS_SRC"
echo "[install] target:  $TARGET"
echo "[install] harness: $HARNESS"
echo "[install] mode:    $MODE"

if [ "$UNINSTALL" -eq 1 ]; then
  for d in "${DIRS[@]}"; do
    if [ -e "$TARGET/$d" ] || [ -L "$TARGET/$d" ]; then
      echo "[install] removing $TARGET/$d"
      rm -rf "$TARGET/$d"
    fi
  done
  echo "[install] uninstall complete"
  exit 0
fi

mkdir -p "$TARGET"

for d in "${DIRS[@]}"; do
  if [ ! -d "$SKILLS_SRC/$d" ]; then
    echo "[install] missing source skill: $SKILLS_SRC/$d" >&2
    exit 2
  fi
  if [ -e "$TARGET/$d" ] || [ -L "$TARGET/$d" ]; then
    echo "[install] replacing $TARGET/$d"
    rm -rf "$TARGET/$d"
  fi
  if [ "$MODE" = "link" ]; then
    ln -s "$SKILLS_SRC/$d" "$TARGET/$d"
    echo "[install] linked   $d"
  else
    # rsync 优先(干净地保留权限、排除缓存),没有则退回 cp -R
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --exclude='__pycache__' --exclude='*.pyc' "$SKILLS_SRC/$d/" "$TARGET/$d/"
    else
      cp -R "$SKILLS_SRC/$d" "$TARGET/$d"
    fi
    echo "[install] copied   $d"
  fi
done

cat <<EOF

[install] DONE. 已安装 5 个 skill 到:$TARGET
  - cs-scot-generate   生成场景诊断 skill(故障树 → 解析脚本 + SCoT 计划)
  - cs-scot-execute    执行 SCoT 计划(按 plan.json 顺序跑)
  - fault-tree-rca     综合根因分析
  - cs-scot-benchmark  对 generate 产物跑基准(压缩/稳定/可复现/护栏)
  - cs-scot-eval       合成故障注入测诊断准确率(自带 ground truth)

依赖:skill 脚本仅用 Python 标准库;只有读取 .xlsx 才需要 openpyxl(csv 开箱即用)。
      需要 xlsx 时:pip install openpyxl

下一步:在 harness(如 Claude Code)里直接说,例如:
  "用 cs-scot-generate 把这个故障树 Excel 做成一个场景诊断 skill"
EOF
