#!/usr/bin/env python3
"""断点续接看板（where-was-i 的确定性内核）：扫本机所有 agent 会话，按工作线汇总进度和「等你」。跨 harness。

    python3 kit/board.py                     # 最近 48 小时，Markdown
    python3 kit/board.py --since 7d --text   # 最近 7 天，纯文本（终端 / 没有可点击链接的 runtime）
    python3 kit/board.py --json              # 给别的工具 / 页面用
    python3 kit/board.py --all               # 包含已归档 / 已隐藏 / 自动化会话
    python3 kit/board.py --hide <id> [...]   # 在看板上隐藏（会话有新活动时自动重新出现）

抽象：
- **协议**：每轮末尾的状态卡（📍 目标 / ✅ 已完成 / ⏳ 当前 / 👉 等你）是唯一约定。看板不依赖任何 runtime 的工具。
- **数据源适配器**：每个 harness 一个函数，产出统一的 Session 记录（见 SOURCES）。加新 harness = 加一个适配器。
  - claude：~/.claude/projects/*/*.jsonl（transcript）+ ~/.claude/sessions/*.json（正在运行的进程）
           + ~/Library/Application Support/Claude/claude-code-sessions/**/local_*.json（桌面端：标题、是否归档、app 内链接 id）
  - codex：~/.codex/sessions/**/*.jsonl（rollout 文件，尽力解析）
- **看板逻辑**：过滤（时间窗、自动化 / 定时任务 / 一次性脚本会话、同名例行会话、已归档、已隐藏、配置里的 skip_cwd）→ 分类（等你 / 在跑·可继续 / 可归档）
  → 按工作线分组 → 渲染。没有状态卡的会话只给出最后一段回复，由执行 where-was-i skill 的 agent 推断并标「(推断)」。

工作线：标题开头的 `[xxx]` → 状态卡 📍 行里的 `[xxx]` → ~/.agents/board.json 的 workline_by_path（路径片段 → 工作线）→ 仓库目录名。
路径都可用环境变量覆盖（测试 / 非默认安装）：AGENT_BOARD_CLAUDE_PROJECTS / _CLAUDE_LIVE / _CLAUDE_DESKTOP / _CODEX / _STATE / _CONFIG。
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()


def _p(env, default):
    return Path(os.environ.get(env, default)).expanduser()


CLAUDE_PROJECTS = _p("AGENT_BOARD_CLAUDE_PROJECTS", HOME / ".claude" / "projects")
CLAUDE_LIVE = _p("AGENT_BOARD_CLAUDE_LIVE", HOME / ".claude" / "sessions")
CLAUDE_DESKTOP = _p("AGENT_BOARD_CLAUDE_DESKTOP", HOME / "Library" / "Application Support" / "Claude" / "claude-code-sessions")
CODEX_SESSIONS = _p("AGENT_BOARD_CODEX", HOME / ".codex" / "sessions")
STATE_DIR = _p("AGENT_BOARD_STATE", HOME / ".agents" / "board")
CONFIG = _p("AGENT_BOARD_CONFIG", HOME / ".agents" / "board.json")

CARD_KEYS = {"📍": "goal", "✅": "done", "⏳": "now", "👉": "wait"}


@dataclass
class Session:
    runtime: str
    id: str
    title: str = ""
    title_set: bool = False       # 标题是人 / runtime 起的，而不是从首句截的
    cwd: str = ""
    started: float = 0.0          # epoch 秒
    last_activity: float = 0.0
    kind: str = "human"           # human / automation / scheduled / sdk
    running: bool = False
    archived: bool = False
    app_id: str = ""              # runtime 内可点击跳转的 id（Claude 桌面端的 local_…）
    card: dict = None             # {"goal","done","now","wait"}；没有状态卡为 None
    last_text: str = ""           # 最后一段 assistant 正文（没有状态卡时给 agent 推断用）
    summary: str = ""             # runtime 自带的摘要（如果有）
    resume: str = ""              # 在终端里接着这个会话的命令
    workline: str = ""
    extra: dict = field(default_factory=dict)


# ---------------- 通用解析 ----------------

def parse_card(text):
    """从一段 assistant 正文里取最后一张状态卡。"""
    if not text or "📍" not in text:
        return None
    tail = text[text.rfind("📍"):]
    card = {}
    for line in tail.splitlines():
        line = line.strip().lstrip("-*> ").strip()
        for emo, key in CARD_KEYS.items():
            if line.startswith(emo) and key not in card:
                v = line[len(emo):].strip()
                v = re.sub(r"^[^：:]{0,8}[：:]\s*", "", v, count=1)  # 去掉「目标：」「等你:」这类标签
                card[key] = v
    return card if "goal" in card else None


def _text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(x.get("text", "") for x in content or []
                     if isinstance(x, dict) and x.get("type") in ("text", "output_text", "input_text"))


def _ts(v):
    """ISO 字符串 / 毫秒 / 秒 → epoch 秒。"""
    if v in (None, ""):
        return 0.0
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        v = float(v)
        return v / 1000 if v > 1e11 else v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _automation_prefixes():
    cfg = load_config()
    env = [p for p in os.environ.get("AGENT_AUTOMATION_PREFIXES", "").split("||") if p]
    return tuple(env + cfg.get("automation_prefixes", []))


# ---------------- 适配器：Claude Code ----------------

def _read_claude_transcript(path):
    title, cwd, entry, started, last, first_human, last_text, kind = "", "", "", 0.0, 0.0, None, "", "human"
    human_turns = 0
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"type"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            if d.get("isSidechain"):
                continue
            if t == "custom-title":
                title = d.get("customTitle") or title
                continue
            ts = _ts(d.get("timestamp"))
            if ts:
                started = started or ts
                last = max(last, ts)
            cwd = cwd or d.get("cwd", "")
            entry = entry or d.get("entrypoint", "")
            if t == "user" and (d.get("turnOrigin") == "human" or isinstance((d.get("message") or {}).get("content"), str)):
                human_turns += 1
            if t == "user" and first_human is None:
                txt = _text_of((d.get("message") or {}).get("content"))
                if "<scheduled-task" in txt[:300]:
                    kind = "scheduled"
                txt = re.sub(r"<[a-zA-Z_-]+>.*?</[a-zA-Z_-]+>", "", txt, flags=re.S).strip()
                if txt and not txt.startswith("<"):
                    first_human = txt
            elif t == "assistant":
                txt = _text_of((d.get("message") or {}).get("content"))
                if txt.strip():
                    last_text = txt
    if entry == "sdk-cli":
        kind = "sdk"
    if kind == "human" and first_human and first_human.startswith(_automation_prefixes() or ("\0",)):
        kind = "automation"
    return {"title": title, "cwd": cwd, "started": started, "last": last, "kind": kind,
            "first": (first_human or "")[:200], "last_text": last_text, "human_turns": human_turns}


def claude_sessions(since):
    out = {}
    if CLAUDE_PROJECTS.exists():
        for p in CLAUDE_PROJECTS.glob("*/*.jsonl"):
            try:
                if p.stat().st_mtime < since:
                    continue
            except FileNotFoundError:
                continue
            r = _read_claude_transcript(p)
            s = Session("claude", p.stem, title=r["title"] or r["first"][:60], title_set=bool(r["title"]), cwd=r["cwd"], started=r["started"],
                        last_activity=max(r["last"], p.stat().st_mtime), kind=r["kind"], last_text=r["last_text"][-1500:],
                        card=parse_card(r["last_text"]), extra={"transcript": str(p), "first_prompt": r["first"], "human_turns": r["human_turns"]})
            s.resume = f"cd {_q(s.cwd)} && claude --resume {s.id}" if s.cwd else f"claude --resume {s.id}"
            out[s.id] = s
    # 桌面端元数据：标题、是否归档、app 内链接 id、每轮摘要
    if CLAUDE_DESKTOP.exists():
        for p in CLAUDE_DESKTOP.rglob("local_*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            sid = d.get("cliSessionId")
            if not sid or sid not in out:
                continue
            s = out[sid]
            s.app_id = d.get("sessionId", "")
            if d.get("title"):
                s.title, s.title_set = d["title"], True
            s.archived = bool(d.get("isArchived"))
            s.last_activity = max(s.last_activity, _ts(d.get("lastActivityAt")))
            pts = d.get("postTurnSummary") or {}
            if isinstance(pts, dict):
                s.summary = pts.get("status_detail", "") or s.summary
    # 正在运行的进程：busy / idle、最新标题
    if CLAUDE_LIVE.exists():
        for p in CLAUDE_LIVE.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                os.kill(int(d["pid"]), 0)
            except Exception:
                continue
            s = out.get(d.get("sessionId"))
            if not s:
                continue
            s.running = d.get("status") == "busy"
            if d.get("name"):
                s.title, s.title_set = d["name"], True
            s.app_id = s.app_id or d.get("hostSessionId", "")
    # 一次性脚本会话：只有一轮、没有标题、不在桌面端、没在运行（读一张图、认证探测、管道里的单次调用…）
    for s in out.values():
        if s.kind == "human" and s.extra.get("human_turns", 0) <= 1 and not s.app_id and not s.running and not s.title_set:
            s.kind = "oneshot"
    return list(out.values())


# ---------------- 适配器：Codex ----------------

def codex_sessions(since):
    out = []
    if not CODEX_SESSIONS.exists():
        return out
    for p in CODEX_SESSIONS.rglob("*.jsonl"):
        try:
            if p.stat().st_mtime < since:
                continue
        except FileNotFoundError:
            continue
        sid, cwd, started, last, first, last_text, title = p.stem, "", 0.0, 0.0, None, "", ""
        with open(p, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                pl = d.get("payload") if isinstance(d.get("payload"), dict) else d
                ts = _ts(d.get("timestamp") or pl.get("timestamp"))
                if ts:
                    started = started or ts
                    last = max(last, ts)
                if d.get("type") == "session_meta" or pl.get("type") == "session_meta":
                    sid = pl.get("id") or sid
                    cwd = pl.get("cwd") or cwd
                    title = pl.get("title") or title
                    continue
                if pl.get("type") == "message":
                    txt = _text_of(pl.get("content"))
                    if pl.get("role") == "user" and first is None:
                        clean = re.sub(r"<[a-zA-Z_-]+>.*?</[a-zA-Z_-]+>", "", txt, flags=re.S).strip()
                        if clean and not clean.startswith("<"):
                            first = clean
                    elif pl.get("role") == "assistant" and txt.strip():
                        last_text = txt
        kind = "automation" if first and first.startswith(_automation_prefixes() or ("\0",)) else "human"
        s = Session("codex", sid, title=title or (first or "")[:60], cwd=cwd, started=started,
                    last_activity=max(last, p.stat().st_mtime), kind=kind, last_text=last_text[-1500:],
                    card=parse_card(last_text), resume=f"codex resume {sid}",
                    extra={"transcript": str(p), "first_prompt": (first or "")[:200]})
        out.append(s)
    return out


SOURCES = {"claude": claude_sessions, "codex": codex_sessions}


# ---------------- 看板逻辑 ----------------

def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_hidden():
    try:
        return json.loads((STATE_DIR / "hidden.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def hide(ids):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    h = load_hidden()
    for i in ids:
        h[i] = time.time()
    tmp = STATE_DIR / "hidden.json.tmp"
    tmp.write_text(json.dumps(h, indent=1))
    os.replace(tmp, STATE_DIR / "hidden.json")


def workline(s, cfg):
    for text in (s.title, (s.card or {}).get("goal", "")):
        m = re.match(r"\s*[\[【]([^\]】]{1,20})[\]】]", text or "")
        if m:
            return m.group(1).strip()
    cwd = s.cwd or ""
    for frag, wl in (cfg.get("workline_by_path") or {}).items():
        if frag in cwd:
            return wl
    repo = re.sub(r"/\.claude/worktrees/.*$", "", cwd).rstrip("/")
    return Path(repo).name or "其他"


def classify(s, now, stale_days):
    c = s.card or {}
    if re.search(r"已完成", c.get("now", "")) or re.search(r"归档", c.get("wait", "")):
        return "done", "状态卡写已完成 / 可以归档"
    if now - s.last_activity > stale_days * 86400 and not s.running:
        return "done", f">{stale_days} 天无活动"
    if c.get("wait") and not re.match(r"\s*无", c["wait"]):
        return "waiting", ""
    return "active", ""


def build(since_hours=48, runtimes=("claude", "codex"), include_all=False, stale_days=3, now=None, exclude=()):
    now = now or time.time()
    since = now - since_hours * 3600
    cfg = load_config()
    hidden = load_hidden()
    sessions = []
    for rt in runtimes:
        sessions += SOURCES[rt](since)
    sessions = [s for s in sessions if s.last_activity >= since and s.id not in exclude and s.app_id not in exclude]
    dropped = Counter()
    if not include_all:
        keep = []
        title_count = Counter(s.title for s in sessions if s.title)
        for s in sessions:
            if s.kind != "human":
                dropped[s.kind] += 1
            elif s.archived:
                dropped["archived"] += 1
            elif any(frag in (s.cwd or "") for frag in cfg.get("skip_cwd", [])):
                dropped["skip_cwd"] += 1
            elif s.id in hidden and s.last_activity <= hidden[s.id]:
                dropped["hidden"] += 1
            elif s.title and title_count[s.title] >= 3:
                dropped["routine"] += 1   # 同名 ≥3 个基本是例行任务
            else:
                keep.append(s)
        sessions = keep
    rows = []
    for s in sessions:
        s.workline = workline(s, cfg)
        bucket, why = classify(s, now, stale_days)
        rows.append({"bucket": bucket, "why": why, **asdict(s)})
    rows.sort(key=lambda r: r["last_activity"], reverse=True)
    lines = {r["workline"] for r in rows if r["bucket"] != "done"}
    return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "since_hours": since_hours,
            "counts": {"lines": len(lines), "sessions": len(rows), "waiting": sum(r["bucket"] == "waiting" for r in rows)},
            "dropped": dict(dropped), "rows": rows}


# ---------------- 渲染 ----------------

def _q(p):
    return "'" + p.replace("'", "'\\''") + "'" if re.search(r"[^\w/.\-~]", p) else p


def _md_escape(t):
    return re.sub(r"([\[\]()])", r"\\\1", t or "(无标题)")


def _ago(ts, now):
    m = int((now - ts) / 60)
    return f"{m} 分钟前" if m < 60 else f"{m // 60} 小时前" if m < 1440 else f"{m // 1440} 天前"


def _name(r, md):
    t = r["title"] or "(无标题)"
    if md and r["app_id"]:
        return f"[{_md_escape(t)}](#{r['app_id']})"
    return _md_escape(t) if md else t


def _one(t, n=None):
    t = re.sub(r"\s+", " ", t or "").strip()
    return t[:n] + "…" if n and len(t) > n else t


def _status(r):
    c = r["card"]
    if c:
        return {k: _one(v) for k, v in c.items()}
    return {"goal": _one(r["extra"].get("first_prompt"), 60), "now": _one(r["summary"] or r["last_text"][-200:], 160),
            "wait": "", "inferred": True}


def render(board, md=True):
    now = time.time()
    rows = board["rows"]
    c = board["counts"]
    out = [f"{c['lines']} 条线 · {c['sessions']} 个会话 · {c['waiting']} 个在等你"
           + (f"（已滤掉：{'、'.join(f'{k} {v}' for k, v in board['dropped'].items())}）" if board["dropped"] else "")]
    h = (lambda t: f"\n### {t}") if md else (lambda t: f"\n== {t}")
    def tail(r):
        extra = [] if (md and r["app_id"]) else [f"接着做：{r['resume']}"]
        return ("  ·  " + "  ·  ".join(extra)) if extra else ""
    waiting = [r for r in rows if r["bucket"] == "waiting"]
    if waiting:
        out.append(h(f"👉 等你（{len(waiting)}）"))
        for r in waiting:
            st = _status(r)
            out.append(f"- {_name(r, md)} — 等你：{st['wait']}（⏳ {st.get('now', '')}）{tail(r)}")
    active = [r for r in rows if r["bucket"] == "active"]
    if active:
        out.append(h("🔄 在跑 / 可继续"))
        for wl in sorted({r["workline"] for r in active}):
            out.append(f"**[{wl}]**" if md else f"[{wl}]")
            for r in [x for x in active if x["workline"] == wl]:
                st = _status(r)
                flag = "🔄 " if r["running"] else ""
                mark = "（无状态卡，待推断）" if st.get("inferred") else ""
                out.append(f"- {flag}{_name(r, md)} — 📍{st.get('goal', '')} · ⏳{st.get('now', '')}{mark} · {_ago(r['last_activity'], now)}{tail(r)}")
    done = [r for r in rows if r["bucket"] == "done"]
    if done:
        out.append(h(f"✅ 已完成 / 可归档（{len(done)}）"))
        for r in done:
            out.append(f"- {_name(r, md)} — {r['why']}（{r['id']}）{tail(r)}")
    return "\n".join(out)


def parse_since(s):
    m = re.fullmatch(r"(\d+)([hd])", s)
    if not m:
        raise argparse.ArgumentTypeError("用 48h / 7d 这种格式")
    return int(m.group(1)) * (24 if m.group(2) == "d" else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="断点续接看板（跨 harness）")
    ap.add_argument("--since", type=parse_since, default=48, help="时间窗：48h / 7d（默认 48h）")
    ap.add_argument("--runtime", default="claude,codex")
    ap.add_argument("--all", action="store_true", help="包含已归档 / 已隐藏 / 自动化 / 例行会话")
    ap.add_argument("--stale-days", type=int, default=3)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true")
    g.add_argument("--text", action="store_true")
    ap.add_argument("--hide", nargs="+", metavar="ID")
    ap.add_argument("--exclude", nargs="+", default=[], metavar="ID", help="不列入的会话（例如正在跑 where-was-i 的会话自己）")
    a = ap.parse_args(argv)
    if a.hide:
        hide(a.hide)
        print(f"已在看板上隐藏 {len(a.hide)} 个会话（有新活动时会重新出现）")
        return 0
    board = build(a.since, tuple(x for x in a.runtime.split(",") if x in SOURCES), a.all, a.stale_days, exclude=set(a.exclude))
    if a.json:
        print(json.dumps(board, ensure_ascii=False, indent=1))
    else:
        print(render(board, md=not a.text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
