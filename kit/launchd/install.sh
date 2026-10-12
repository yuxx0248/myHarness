#!/usr/bin/env bash
# 渲染 + （重新）加载一个 launchd 定时任务。幂等，随时可以重跑。
#
#   kit/launchd/install.sh <name> "<command>" [间隔分钟，默认 10] [起始分钟偏移，默认 0]
#   kit/launchd/install.sh my-bot "python3 ~/my-bot/tick.py" 10 5     # 每小时 :05 :15 … :55
#
# 状态目录 ~/Library/Application Support/<name>/，日志 ~/Library/Logs/<name>/（不放 ~/Documents）。
# 停掉：launchctl bootout gui/$(id -u)/com.$USER.<name>
# 立刻跑一次（在真实运行上下文里验证，而不是在会话里）：launchctl kickstart gui/$(id -u)/com.$USER.<name>
set -euo pipefail
NAME=${1:?name}; CMD=${2:?command}; EVERY=${3:-10}; OFFSET=${4:-0}
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.$USER.$NAME"
DST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/Application Support/$NAME" "$HOME/Library/Logs/$NAME" "$HOME/Library/LaunchAgents"
minutes=""
for ((m=OFFSET; m<60; m+=EVERY)); do minutes+="		<dict><key>Minute</key><integer>$m</integer></dict>"$'\n'; done
python3 - "$HERE/agent.plist.tmpl" "$DST" "$LABEL" "$NAME" "$CMD" "$HOME" "$USER" "$minutes" <<'PY'
import sys, html
tmpl, dst, label, name, cmd, home, user, minutes = sys.argv[1:]
s = open(tmpl).read()
for k, v in {"__LABEL__": label, "__NAME__": name, "__CMD__": html.escape(cmd), "__HOME__": home,
             "__USER__": user, "__MINUTES__": minutes.rstrip("\n")}.items():
    s = s.replace(k, v)
open(dst, "w").write(s)
PY
plutil -lint "$DST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DST"
echo "installed $DST"
