"""最小的 source 适配器示例：从一个 JSON 收件箱文件读消息，按会话 + 空闲时长聚成单元。

真实场景里把 scan() 换成你的消息源（聊天工具 CLI、工单系统 API…）；stage0 过滤写成纯函数，配真实历史数据的单测。

收件箱格式：[{"id": "m1", "conv": "群A", "ts": "2026-10-01 10:00", "who": "某人", "text": "...", "to_me": true, "from_bot": false}]

    python3 kit/examples/inbox_source.py inbox.json <state_dir> <rules.yaml>   # 跑一轮（需要可用的 LLM 后端）
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ME = "我"
IDLE_SPLIT_H = 12


def drop_reason(m):
    """stage0：确定性过滤。返回丢弃原因，None = 保留。"""
    if m.get("from_bot"):
        return "F1_bot"
    if m.get("who") == ME:
        return "F2_self"
    if re.search(r"@(all|所有人)", m.get("text", "")):
        return "F3_broadcast"
    if not m.get("to_me"):
        return "F5_not_direct"
    return None


class InboxSource:
    def __init__(self, path):
        self.path = Path(path)

    def scan(self, state, units):
        seen = set(state.get("seen", []))
        msgs = [m for m in json.loads(self.path.read_text(encoding="utf-8")) if m["id"] not in seen]
        units = dict(units)
        for m in sorted(msgs, key=lambda m: m["ts"]):
            seen.add(m["id"])
            if drop_reason(m):
                continue
            ts = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M")
            # 同一会话、距上一条触发 < IDLE_SPLIT_H 小时 → 同一个单元
            key = next((k for k, u in units.items() if u["conv"] == m["conv"]
                        and ts - datetime.strptime(u["last_ts"], "%Y-%m-%d %H:%M") < timedelta(hours=IDLE_SPLIT_H)), None)
            if key is None:
                key = f"{m['conv']}|{m['ts']}"
                units[key] = {"id": key, "conv": m["conv"], "where": m["conv"], "status": "open", "triggers": []}
            u = units[key]
            u["triggers"].append({"id": m["id"], "ts": m["ts"], "who": m["who"], "text": m["text"]})
            u["last_ts"] = m["ts"]
            u["revision"] = ",".join(t["id"] for t in u["triggers"])  # 有新触发消息 → revision 变 → 重新提案
        state["seen"] = sorted(seen)
        return units

    def terms(self, unit):
        text = " ".join(t["text"] for t in unit["triggers"])
        return re.findall(r"https?://\S+|#\d+", text)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import bot
    print(bot.tick(InboxSource(sys.argv[1]), bot.Config(state_dir=sys.argv[2], rules_path=sys.argv[3], persona="我是一名工程师。")))
