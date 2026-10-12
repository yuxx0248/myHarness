"""Story 层：把散在不同会话 / 渠道里的 ask 聚成「一件真实世界的事」（一个请求 / 一个问题 / 一个交付）。

设计要点（见 playbooks/automation.md「加一层，不碰已校准的东西」）：
- 每个新 ask 只用一次小的 LLM 调用挂接，只看这个 ask + 最近 N 天的候选 story；已校准的主 prompt 不动。
- 对象不同 → 一定是不同 story；拿不准就新开（宁可拆开，不要错误合并）。
- 单元重新提案前先 unlink，并把它原来所在的 story 作为提示带进重新挂接。

    import stories
    st = stories.load(path)
    hint = stories.unlink_unit(st, unit_id)
    sid = stories.link(st, ask, unit_id=..., where=..., ts=..., trigger_texts=[...], hint="、".join(hint) or None)
    stories.save(st, path)
"""
import hashlib
import json
import os
from datetime import datetime, timedelta

import llm

WINDOW_DAYS = 7
MAX_CANDIDATES = 25

SCHEMA = {
    "type": "object",
    "properties": {
        "object": {"type": "string", "description": "这件事的对象：客户 / 功能 / 系统 / 数据集"},
        "story_id": {"type": ["string", "null"], "description": "挂到哪个已有 story；新开则 null"},
        "title": {"type": "string", "description": "「<发起人> 要求 <做什么>」，一句话，带对象名"},
        "reason": {"type": "string"},
    },
    "required": ["object", "story_id", "title", "reason"],
}


def _parse(ts):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts, fmt)
        except (TypeError, ValueError):
            pass
    try:
        d = datetime.fromisoformat(ts)
        return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
    except Exception:
        return datetime(1970, 1, 1)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(stories, path):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(stories, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def candidates(stories, now, window_days=WINDOW_DAYS):
    out = [s for s in stories.values() if s.get("refs") is not None
           and _parse(s["updated"]) >= now - timedelta(days=window_days)]
    out.sort(key=lambda s: s["updated"], reverse=True)
    return out[:MAX_CANDIDATES]


def build_prompt(ask, where, ts, trigger_texts, cands, hint=None, window_days=WINDOW_DAYS):
    lines = []
    for s in cands:
        asks = "；".join(a["summary"][:80] for a in s.get("ask_log", [])[-3:])
        lines.append(f"- {s['id']}「{s['title']}」 对象: {s.get('object') or '?'} · 发起人: {'、'.join(s.get('requesters', []))}"
                     f" · 出现在: {'、'.join(s.get('where', [])[:4])} · 最近 {s['updated']} · 已有 ask: {asks}")
    return f"""你在帮我把别人找我的事整理成 story。一个 story = 一件真实世界的事（一个请求 / 一个问题 / 一个交付），
可以跨多个会话和渠道：同一件事的催办、补充、转述、进度追问、另一个人的另一路反馈，都属于同一个 story。

判定（按顺序）：
1. 先抽出新 ask 的「对象」：客户 / 功能 / 系统 / 数据集。
2. 只有对象相同、而且是同一个根问题或同一个交付，才挂到已有 story：
   - 同一对象正在处理中的问题，有人来催办、问进度、问有没有备份方案、转述对方反馈 → 挂上去。
   - 同一事故里要我做的不同动作（例如「限期评估方案 A/B」和「把 bug 修掉」）→ 挂上去。
3. 对象不同就一定是不同 story，哪怕两件事提到同一个词。
4. 只是同一个人、同一个渠道，但事情不同 → 不同 story。
5. 拿不准时新开 story（宁可拆开，不要错误合并）。

title：写成「<发起人> 要求 <做什么>」，只描述一件事；如果 title 需要用「；」或「并且」把不同人提的不同事串起来，说明合错了，应该新开。
挂到已有 story 时，可以把 title 改得更能概括这件事（发起人写最早提出的人）。

## 新进来的 ask
- 摘要: {ask.get('summary')}
- 提出人: {ask.get('requester')}
- 出现在: {where} · {ts}
- 分支: {ask.get('branch_id')} · level {ask.get('level')}
- 触发消息: {' / '.join(t[:150] for t in trigger_texts[:3])}
{f'- 提示: 这段对话之前被归到 {hint}' if hint else ''}

## 最近 {window_days} 天的 story
{chr(10).join(lines) or '（无）'}
"""


def link(stories, ask, unit_id, where, ts, trigger_texts, hint=None, linker=None):
    """把 ask 挂到已有 story 或新开一个，返回 story id。linker(ask, cands) 可注入（测试 / 重放用）。"""
    cands = candidates(stories, _parse(ts))
    if linker is None:
        out, _ = llm.call(build_prompt(ask, where, ts, trigger_texts, cands, hint), SCHEMA, model=os.environ.get("AGENT_STORY_MODEL"))
    else:
        out = linker(ask, cands)
    sid = out.get("story_id")
    if sid not in stories:
        sid = "s_" + hashlib.sha1(f"{unit_id}|{ask.get('summary')}|{ts}".encode()).hexdigest()[:8]
        stories[sid] = {"id": sid, "title": out["title"], "created": ts, "updated": ts, "refs": [], "ask_log": [],
                        "requesters": [], "where": []}
    s = stories[sid]
    s["title"] = out.get("title") or s["title"]
    s["object"] = s.get("object") or out.get("object")
    s["updated"] = max(s["updated"], ts)
    s["refs"].append({"unit_id": unit_id, "ask_summary": ask.get("summary"), "trigger_ids": ask.get("trigger_ids") or []})
    s["ask_log"].append({"summary": ask.get("summary"), "ts": ts, "where": where})
    if ask.get("requester") and ask["requester"] not in s["requesters"]:
        s["requesters"].append(ask["requester"])
    if where not in s["where"]:
        s["where"].append(where)
    s["link_reason"] = out.get("reason")
    return sid


def unlink_unit(stories, unit_id):
    """单元重新提案前：去掉它的引用，返回它原来所在的 story（作为重新挂接的提示）。"""
    prev = []
    for sid, s in stories.items():
        before = len(s.get("refs", []))
        s["refs"] = [r for r in s.get("refs", []) if r["unit_id"] != unit_id]
        if len(s["refs"]) != before:
            prev.append(f"{sid}「{s['title']}」")
    return prev
