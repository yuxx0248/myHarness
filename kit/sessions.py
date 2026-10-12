"""只读检索本机 agent 会话 transcript：「这件事是不是已经有会话在处理」（triage 的「已在处理」闸），
也用于断点续接、按集合差找未吸收的会话。

    import sessions
    hits = sessions.find_related(["<链接 / ID / PR 号>", ...], since=datetime(...), until=None)

- Claude Code：~/.claude/projects/*/*.jsonl（解析首句、cwd、最后一段回复，区分人开的会话和自动化起的会话）
- Codex：~/.codex/sessions/**/*.jsonl（尽力解析；格式随版本变化，解析不出就只返回文件 + 命中词）

自动化会话的识别：首句以 AGENT_AUTOMATION_PREFIXES（用 || 分隔）里的任一前缀开头，或是定时任务。
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()
ROOTS = {
    "claude": HOME / ".claude" / "projects",
    "codex": HOME / ".codex" / "sessions",
}
SKIP_DIR_PARTS = tuple(p for p in os.environ.get("AGENT_SESSION_SKIP_DIRS", "").split("||") if p)
AUTOMATION_PREFIXES = tuple(p for p in os.environ.get("AGENT_AUTOMATION_PREFIXES", "").split("||") if p)


def _text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(x.get("text", "") for x in (content or []) if isinstance(x, dict) and x.get("type") in ("text", "input_text"))


def _user_text(d):
    """一行 jsonl 里人打的字；Claude 与 Codex 的结构不同，两种都试。"""
    if d.get("type") == "user":
        return _text_of((d.get("message") or {}).get("content"))
    p = d.get("payload") or d
    if p.get("type") == "message" and p.get("role") == "user":
        return _text_of(p.get("content"))
    return None


def head(f, n=60):
    human, kind, cwd, ts0 = None, "human", None, None
    with open(f, encoding="utf-8", errors="ignore") as fh:
        for i, line in enumerate(fh):
            if i > n:
                break
            try:
                d = json.loads(line)
            except Exception:
                continue
            if "<scheduled-task" in str(d.get("content", ""))[:200]:
                kind = "scheduled"
            ts0 = ts0 or d.get("timestamp") or (d.get("payload") or {}).get("timestamp")
            cwd = cwd or d.get("cwd") or (d.get("payload") or {}).get("cwd")
            if human is None:
                txt = _user_text(d)
                txt = re.sub(r"<[a-z_-]+>.*?</[a-z_-]+>", "", txt or "", flags=re.S).strip()
                if txt and not txt.startswith("<"):
                    human = txt
    if human and AUTOMATION_PREFIXES and human.startswith(AUTOMATION_PREFIXES):
        kind = "automation"
    return {"first_prompt": (human or "")[:200], "kind": kind, "cwd": cwd, "started": ts0}


def last_assistant_text(f, n=400):
    last = ""
    with open(f, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if "assistant" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            msg = d.get("message") or d.get("payload") or {}
            if d.get("type") == "assistant" or msg.get("role") == "assistant":
                t = _text_of(msg.get("content"))
                if t.strip():
                    last = t
    return last[-n:]


def transcript_files(since=None, runtimes=("claude", "codex")):
    cutoff = time.mktime(since.timetuple()) if since else 0
    out = []
    for rt in runtimes:
        root = ROOTS[rt]
        if not root.exists():
            continue
        for p in root.rglob("*.jsonl"):
            if SKIP_DIR_PARTS and any(s in str(p.parent) for s in SKIP_DIR_PARTS):
                continue
            if p.stat().st_mtime >= cutoff:
                out.append((rt, p))
    return out


def find_related(terms, since=None, until=None, limit=3, include_automation=False):
    """terms: 要找的字符串（链接、ID、PR 号…）。until: 本地时间，忽略之后才开始的会话（重放历史时防止泄漏未来）。"""
    files = transcript_files(since)
    if not files or not terms:
        return []
    by_path = {str(p): rt for rt, p in files}
    hits = {}
    for term in dict.fromkeys(terms):
        try:
            r = subprocess.run(["grep", "-l", "-F", term] + list(by_path), capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            continue
        for line in r.stdout.splitlines():
            hits.setdefault(line, set()).add(term)
    out = []
    for f, matched in hits.items():
        h = head(f)
        if h["kind"] != "human" and not include_automation:
            continue
        if until and h["started"]:
            try:
                st = datetime.fromisoformat(str(h["started"]).replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                if st > until:
                    continue
            except ValueError:
                pass
        out.append({"runtime": by_path[f], "session_id": Path(f).stem, "file": f, "matched": sorted(matched), **h,
                    "last_update": time.strftime("%Y-%m-%d %H:%M", time.localtime(Path(f).stat().st_mtime)),
                    "last_assistant": last_assistant_text(f)})
    out.sort(key=lambda x: x["last_update"], reverse=True)
    return out[:limit]
