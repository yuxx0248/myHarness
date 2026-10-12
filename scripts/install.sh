#!/usr/bin/env bash
# 把框架装到本机的 Claude Code 和 Codex。默认 dry-run，只打印要做什么；加 --apply 才真的改。
#
#   ~/.agents/framework        → 本框架目录（软链）
#   ~/.agents/skills/<x>       → framework/skills/<x>      （跨 runtime 的 skill 真源位置）
#   ~/.claude/skills/<x>       → ~/.agents/skills/<x>
#   ~/.codex/skills/<x>        → ~/.agents/skills/<x>
#   ~/.agents/memory/MEMORY.md   跨项目共享 memory 的索引（不存在才创建）
#   ~/.claude/CLAUDE.md        → adapters/claude/CLAUDE.md （原文件先备份）
#   ~/.codex/AGENTS.md         → dist/codex/AGENTS.md      （原文件先备份）
#   ~/.agents/packs/<name>     → --pack 指定的领域 pack 目录（可多次）
#
# 用法：scripts/install.sh [--apply] [--pack DIR]...
#
# 回滚：把 *.bak-<时间戳> 改回原名；删掉上面这些软链。
set -euo pipefail

APPLY=0
PACKS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --pack)  PACKS+=("$(cd "$2" && pwd)"); shift ;;
    *) echo "未知参数：$1"; exit 2 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TS=$(date +%Y%m%d-%H%M%S)

case "$ROOT" in
  */.claude/worktrees/*)
    echo "⚠ 框架当前在一个临时 worktree 里：$ROOT"
    echo "  worktree 会被清理，软链会断。建议先移到独立仓库（例如 ~/myHarness）再安装。"
    [ $APPLY -eq 1 ] && [ "${FORCE:-0}" != "1" ] && { echo "  拒绝 --apply（确实要装就加 FORCE=1）"; exit 1; } ;;
esac

run() { if [ $APPLY -eq 1 ]; then "$@"; else echo "  [dry-run] $*"; fi; }

link() {  # link <target> <linkpath>：已是同一目标就跳过；是别的真实文件 / 目录就不碰并提示
  local target=$1 path=$2
  if [ -L "$path" ] && [ "$(readlink "$path")" = "$target" ]; then echo "  = $path"; return; fi
  if [ -e "$path" ] && [ ! -L "$path" ]; then echo "  ! $path 已存在且不是软链，跳过（手动处理）"; return; fi
  run mkdir -p "$(dirname "$path")"
  run ln -sfn "$target" "$path"
  echo "  + $path → $target"
}

backup_and_link() {  # 对单个配置文件：真实文件先备份再换成软链
  local target=$1 path=$2
  if [ -L "$path" ] && [ "$(readlink "$path")" = "$target" ]; then echo "  = $path"; return; fi
  if [ -e "$path" ] || [ -L "$path" ]; then run mv "$path" "$path.bak-$TS"; echo "  ↺ 备份 $path → $path.bak-$TS"; fi
  run mkdir -p "$(dirname "$path")"
  run ln -sfn "$target" "$path"
  echo "  + $path → $target"
}

echo "== 构建"
PACK_DIRS="${PACKS[*]:-}" "$ROOT/scripts/build.sh"

echo "== 框架根目录"
link "$ROOT" "$HOME/.agents/framework"

echo "== skills"
for d in "$ROOT"/skills/*/; do
  name=$(basename "$d")
  link "$HOME/.agents/framework/skills/$name" "$HOME/.agents/skills/$name"
  link "$HOME/.agents/skills/$name" "$HOME/.claude/skills/$name"
  link "$HOME/.agents/skills/$name" "$HOME/.codex/skills/$name"
done

echo "== 领域 pack"
if [ ${#PACKS[@]} -eq 0 ]; then echo "  （未指定 --pack）"; fi
for p in "${PACKS[@]:-}"; do
  [ -z "$p" ] && continue
  [ -f "$p/README.md" ] || { echo "  ! $p 没有 README.md，跳过"; continue; }
  link "$p" "$HOME/.agents/packs/$(basename "$p")"
done

echo "== 共享 memory"
if [ -f "$HOME/.agents/memory/MEMORY.md" ]; then echo "  = ~/.agents/memory/MEMORY.md"
else
  run mkdir -p "$HOME/.agents/memory"
  if [ $APPLY -eq 1 ]; then
    printf '# 跨项目共享 memory 索引\n\n格式见 ~/.agents/framework/playbooks/knowledge-system.md §2。每行：- [标题](文件.md) — 一句话\n\n' > "$HOME/.agents/memory/MEMORY.md"
  else echo "  [dry-run] 创建 ~/.agents/memory/MEMORY.md"; fi
fi

echo "== Claude Code 全局指令"
backup_and_link "$ROOT/adapters/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"

echo "== Codex 全局指令"
backup_and_link "$ROOT/dist/codex/AGENTS.md" "$HOME/.codex/AGENTS.md"

[ $APPLY -eq 1 ] && echo "完成。" || echo "以上是 dry-run。确认无误后：scripts/install.sh --apply"
