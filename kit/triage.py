"""请求分诊：一个工作单元（一段对话 / 一张工单）→ 一次 LLM 调用 → 拆 ask → 落分支 → 过闸 → 定档 + 提案。

只出提案，不执行、不回复。只在单元的 revision 变了（有新触发消息）时才调用。

单元的形状（由你的 source 适配器产出）：
    {"id": "...", "revision": "...", "where": "群 · xxx / 私聊 · 某人 / 工单 #123",
     "triggers": [{"id": "m1", "ts": "2026-10-01 10:00", "who": "某人", "text": "..."}],   # 需要判定的消息
     "context":  [{"id": "m0", "ts": "...", "who": "...", "text": "..."}]}                # 可选：周边上下文，含 triggers

    import rules, triage
    r = rules.load("rules.yaml")
    out, cost = triage.propose(r, unit, persona="我是 <角色>", sessions=[...], others=[...])
"""
import llm
import rules as rulelib

SCHEMA = {
    "type": "object",
    "properties": {
        "asks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "对方要我做什么，一句话，带对象（客户 / 系统 / 功能名）"},
                    "requester": {"type": "string"},
                    "trigger_ids": {"type": "array", "items": {"type": "string"}},
                    "branch_id": {"type": ["string", "null"], "description": "规则导图的叶子 id；不属于任何叶子填 null"},
                    "gates": {"type": "array", "items": {"type": "string"}, "description": "命中的闸 id"},
                    "rule_applied": {"type": ["string", "null"], "description": "命中的分支 rules 条目原文；没有则 null"},
                    "level": {"type": "string", "enum": rulelib.LEVELS},
                    "reason": {"type": "string", "description": "为什么是这个分支和档位，一两句"},
                    "steps": {"type": "array", "items": {"type": "string"}, "description": "agent 打算怎么做（no/skip 时可为空）"},
                    "stop_at": {"type": "string"},
                    "needs": {"type": "array", "items": {"type": "string"}, "description": "需要的权限 / 登录"},
                    "draft_reply": {"type": ["string", "null"], "description": "需要回复对方时，用我口吻的简短草稿；不需要则 null"},
                    "duplicate_of": {"type": ["string", "null"], "description": "重复时，指向其他单元的 id"},
                    "status": {"type": "string", "enum": ["open", "in_progress", "resolved"],
                               "description": "resolved=上下文里已闭环；in_progress=已有会话 / PR 在处理"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["summary", "requester", "branch_id", "gates", "level", "reason", "steps",
                             "stop_at", "status", "confidence"],
            },
        }
    },
    "required": ["asks"],
}

PROCEDURE = """## 判定流程
1. 只从带 ★ 的触发消息里拆出 ask（对方要我做什么）。提出人一定是别人；我自己的消息只是上下文。
   交付物不同就拆成不同 ask。对方预告要我做的事（「有个小需求明天发你」）也单独拆成一个 ask。纯寒暄 / FYI / 已完成通知 → 不产出 ask 或 level=skip。
2. 每个 ask 落到一个分支：先按「实际要交付什么」判 l1，不要被句式误导（问进度 ≠ 改代码）。
3. 逐条检查 gates；从分支默认 level 出发，按分支 rules 从上到下取第一条命中的覆盖；丢弃类的闸命中时 level=skip。
4. status 与 level 无关：已闭环 → status=resolved；已有会话 / PR 在处理 → status=in_progress；level 仍按第 3 步判定。
5. steps 写成针对这件事的具体动作；stop_at 写清停在哪一步等我。
严格按规则导图判定，不要自创分支或档位。"""


def build_prompt(r, unit, persona, sessions=(), others=()):
    trig = {t["id"] for t in unit["triggers"]}
    ctx = unit.get("context") or unit["triggers"]
    ctx_lines = [f"{'★' if m['id'] in trig else ' '} [{m['id']}] {m.get('ts', '')} {m.get('who', '?')}: {m['text']}" for m in ctx]
    sess_lines = [f"- {s.get('started')} 起，最后更新 {s.get('last_update')}，首句「{s.get('first_prompt', '')}」；"
                  f"最后回复片段「{(s.get('last_assistant') or '')[:200]}」" for s in sessions] or ["（没找到相关会话）"]
    other_lines = [f"- {o['id']}: {o.get('where', '')} · {o.get('summary', '')}" for o in others] or ["（无）"]
    return f"""{persona}
你在帮我分诊别人找我的事。只出提案给我评估：不执行、不回复。

{rulelib.prompt_block(r)}

{PROCEDURE}

## 这段对话（{unit.get('where', '')}；★ = 触发消息，"我" = 上面说的我）
{chr(10).join(ctx_lines)}

## 本机相关会话（只读检索结果，用于判断「已在处理」）
{chr(10).join(sess_lines)}

## 其他未结单元（用于判断重复）
{chr(10).join(other_lines)}
"""


def propose(r, unit, persona, sessions=(), others=(), model=None):
    out, cost = llm.call(build_prompt(r, unit, persona, sessions, others), SCHEMA, model=model)
    valid = rulelib.branch_ids(r)
    for a in out.get("asks", []):
        if a.get("branch_id") not in valid:  # 事后校验：不在导图里的分支一律置空，不让模型自创
            a["branch_id"] = None
        if a.get("level") not in rulelib.LEVELS:
            a["level"] = "partial"
    return out, cost
