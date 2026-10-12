#!/usr/bin/env bash
# 生成各 runtime 的指令文件并做体检。幂等，随时可跑。
#   dist/codex/AGENTS.md = adapters/codex/header.md + core/AGENTS.md + adapters/codex/footer.md
# Claude 端不需要生成：adapters/claude/CLAUDE.md 用 @import 引用 core。
#
# 领域泄漏检查：读取已安装 pack（~/.agents/packs/*/leak-terms.txt）和 PACK_DIRS 环境变量里列出的 pack，
# 框架文件里出现任何一个领域词都算失败 —— 框架必须保持领域无关。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
CODEX_LIMIT=32768   # Codex 默认 project_doc_max_bytes，超了会被截断
fail=0

mkdir -p "$DIST/codex"
cat "$ROOT/adapters/codex/header.md" "$ROOT/core/AGENTS.md" "$ROOT/adapters/codex/footer.md" > "$DIST/codex/AGENTS.md"

size=$(wc -c < "$DIST/codex/AGENTS.md" | tr -d ' ')
if [ "$size" -gt "$CODEX_LIMIT" ]; then
  echo "✗ dist/codex/AGENTS.md = ${size}B，超过 Codex 默认上限 ${CODEX_LIMIT}B —— 把细节挪进 playbook" ; fail=1
else
  echo "✓ dist/codex/AGENTS.md = ${size}B（上限 ${CODEX_LIMIT}B）"
fi

# core §11 和 skill 里引用的框架内路径必须存在
missing=$(grep -rhoE 'playbooks/[A-Za-z0-9_./-]+\.md' "$ROOT/core" "$ROOT/skills" "$ROOT/adapters" \
  | sort -u | while read -r p; do [ -f "$ROOT/$p" ] || echo "$p"; done || true)
if [ -n "$missing" ]; then echo "✗ 引用了不存在的文件："; echo "$missing" | sed 's/^/    /'; fail=1
else echo "✓ 框架内引用的 playbook 路径都存在"; fi

# 每个 skill 都要有 name + description frontmatter，且 name 与目录名一致
for f in "$ROOT"/skills/*/SKILL.md; do
  d=$(basename "$(dirname "$f")")
  n=$(sed -n 's/^name: *//p' "$f" | head -1)
  grep -q '^description: ' "$f" || { echo "✗ $d 缺 description"; fail=1; }
  [ "$n" = "$d" ] || { echo "✗ $d 的 name 是 '$n'，与目录名不一致"; fail=1; }
done
echo "✓ skill frontmatter 检查完毕"

# 领域泄漏
terms_files=()
for f in "$HOME"/.agents/packs/*/leak-terms.txt; do [ -f "$f" ] && terms_files+=("$f"); done
for d in ${PACK_DIRS:-}; do [ -f "$d/leak-terms.txt" ] && terms_files+=("$d/leak-terms.txt"); done
if [ ${#terms_files[@]} -eq 0 ]; then
  echo "· 没有找到任何 pack 的 leak-terms.txt，跳过领域泄漏检查"
else
  pattern=$(cat "${terms_files[@]}" | sed -e 's/#.*//' -e 's/[[:space:]]*$//' | grep -v '^$' | sort -u | paste -sd'|' -)
  hits=$(grep -rniE "$pattern" "$ROOT" --include='*.md' --include='*.sh' --exclude-dir=dist --exclude-dir=.git || true)
  if [ -n "$hits" ]; then echo "✗ 框架里混进了领域内容："; echo "$hits" | cut -c1-160 | sed 's/^/    /'; fail=1
  else echo "✓ 没有领域泄漏（检查了 ${#terms_files[@]} 个 pack 的词表）"; fi
fi

# 密钥扫描（结构化密钥的常见形态）
secret_re='AKIA[0-9A-Z]{16}|LTAI[0-9A-Za-z]{12,}|sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[abp]-[0-9A-Za-z-]{10,}'
hits=$(grep -rnoE "$secret_re" "$ROOT" --include='*.md' --include='*.sh' --include='*.json' --include='*.yaml' || true)
if [ -n "$hits" ]; then echo "✗ 发现疑似密钥："; echo "$hits" | cut -c1-120 | sed 's/^/    /'; fail=1
else echo "✓ 没有发现疑似密钥"; fi

exit $fail
