#!/usr/bin/env bash
# 连续 N 轮全新校准（不走缓存），每一轮都必须 0 不一致才算过 —— 模型有随机性，单轮过不算数。
#
#   kit/stability.sh 2 -- python3 kit/calib.py --runner r.py --cases calib/cases.json --fields level --watch rules.yaml
#
# 每轮的报告另存为 report.run<i>.json（与 cases.json 同目录）。
set -uo pipefail
N=${1:?用法: stability.sh N -- <calib 命令>}
shift
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { echo "缺少 calib 命令"; exit 2; }
cases=$(printf '%s\n' "$@" | awk 'prev=="--cases"{print; exit} {prev=$0}')
ok=0
for i in $(seq 1 "$N"); do
  out=$("$@" --fresh 2>&1); rc=$?
  echo "run $i: $(echo "$out" | grep -E 'mismatches' | tail -1)"
  [ -n "$cases" ] && [ -f "$(dirname "$cases")/report.json" ] && cp "$(dirname "$cases")/report.json" "$(dirname "$cases")/report.run$i.json"
  [ $rc -eq 0 ] && ok=$((ok+1))
done
echo "passed $ok/$N"
[ "$ok" = "$N" ]
