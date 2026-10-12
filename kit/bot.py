"""一个「只出提案」的分诊 bot 的 tick 骨架：确定性扫描 → 只对变了的单元调 LLM → story 归并 → 渲染。

你只需要写一个 source 适配器（模块或对象），提供：
    scan(state: dict, units: dict) -> dict[unit_id, unit]
                                                 # 确定性，不调 LLM：拉新消息、stage0 过滤、并入已有单元或新开、更新 revision
                                                 # state 是持久化的水位 / 已见 id，原地修改即可；返回完整的 units
    terms(unit) -> list[str]                     # 可选：用来检索相关本机会话的关键词（链接、ID、PR 号）
    render(summary: dict) -> None                # 可选：把汇总写到你的 viewer；不提供就只写 summary.json

    import bot
    bot.tick(source, bot.Config(state_dir=..., rules_path=..., persona="我是 <角色>"))

单元必须有：id、revision、status（open / closed）、last_ts（"YYYY-MM-DD HH:MM"）、where、triggers（见 triage.py）。

已经内置的惯用法（见 playbooks/automation.md）：
- 单实例锁（上一轮还没跑完就跳过）；整轮异常写日志带 traceback
- 只处理 revision 变了的 open 单元，最早的先处理（后来的追问能挂到更早开的 story 上）；每轮上限
- 额度 / 限流错误 → 立刻停掉本轮，剩下的留到下一轮
- 状态 JSON 原子写；每轮一行统计（调用数、花费、错误数、待处理数）
"""
import fcntl
import json
import os
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import llm
import rules as rulelib
import sessions as sesslib
import stories as storylib
import triage


@dataclass
class Config:
    state_dir: Path
    rules_path: Path
    persona: str
    log_dir: Path = None
    cap_per_tick: int = 6
    session_lookback_days: int = 2
    model: str = None
    use_stories: bool = True
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.state_dir = Path(self.state_dir)
        self.rules_path = Path(self.rules_path)
        self.log_dir = Path(self.log_dir or self.state_dir / "logs")
        if "/Documents/" in str(self.state_dir.resolve()):
            raise ValueError("state_dir 不能放在 ~/Documents：macOS 的 TCC 会拦后台任务")


def load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(p, obj):
    tmp = f"{p}.tmp"
    Path(tmp).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def log(cfg, msg):
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.log_dir / "bot.log", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def tick(source, cfg, no_llm=False):
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    lock = open(cfg.state_dir / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log(cfg, "tick: previous tick still running, skip")
        return None
    try:
        return _tick(source, cfg, no_llm)
    except Exception:
        log(cfg, "tick crashed:\n" + traceback.format_exc())
        raise


def _tick(source, cfg, no_llm):
    t0 = time.time()
    S = cfg.state_dir
    stats = {"llm_calls": 0, "llm_cost_usd": 0.0, "llm_errors": 0}
    state = load_json(S / "state.json", {})
    units = load_json(S / "units.json", {})
    try:
        units = source.scan(state, units)
        save_json(S / "state.json", state)
        save_json(S / "units.json", units)
    except Exception as e:
        log(cfg, f"scan failed: {e}")
        stats["scan_error"] = str(e)[:200]
    props = load_json(S / "proposals.json", {})
    st = storylib.load(S / "stories.json")
    todo = [u for u in units.values() if u.get("status", "open") == "open"
            and props.get(u["id"], {}).get("revision") != u.get("revision")]
    todo.sort(key=lambda u: u["last_ts"])
    stats["pending"] = len(todo)
    if not no_llm and todo:
        r = rulelib.load(cfg.rules_path)
        for u in todo[:cfg.cap_per_tick]:
            others = [{"id": k, "where": units[k].get("where"),
                       "summary": "; ".join(a["summary"] for a in p.get("asks") or [])[:200]}
                      for k, p in props.items() if k != u["id"] and k in units and units[k].get("status", "open") == "open" and p.get("asks")]
            sess = []
            if hasattr(source, "terms"):
                try:
                    since = datetime.strptime(u["last_ts"], "%Y-%m-%d %H:%M") - timedelta(days=cfg.session_lookback_days)
                    sess = sesslib.find_related(source.terms(u), since=since)
                except Exception as e:
                    log(cfg, f"session lookup failed {u['id']}: {e}")
            try:
                out, cost = triage.propose(r, u, cfg.persona, sess, others, model=cfg.model)
            except llm.LLMError as e:
                log(cfg, f"propose failed {u['id']}: {e}")
                stats["llm_errors"] += 1
                props.setdefault(u["id"], {})["error"] = str(e)[:300]
                save_json(S / "proposals.json", props)
                if e.is_quota:
                    break  # 额度用完：剩下的留到下一轮
                continue
            props[u["id"]] = {"revision": u.get("revision"), "asks": out.get("asks", []), "rules_version": r.get("version"),
                              "sessions": [{k: s.get(k) for k in ("session_id", "runtime", "started", "last_update", "first_prompt", "matched")} for s in sess],
                              "proposed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            stats["llm_calls"] += 1
            stats["llm_cost_usd"] += cost or 0
            if cfg.use_stories:
                hint = storylib.unlink_unit(st, u["id"])
                trig = {t["id"]: t["text"] for t in u["triggers"]}
                for a in props[u["id"]]["asks"]:
                    if a.get("level") == "skip" and not a.get("duplicate_of"):
                        continue  # 纯 FYI 类不开 story；重复的挂到它重复的那个 story
                    texts = [trig[i] for i in a.get("trigger_ids") or [] if i in trig] or [t["text"] for t in u["triggers"][-2:]]
                    try:
                        a["story_id"] = storylib.link(st, a, u["id"], u.get("where", ""), u["last_ts"], texts, hint="、".join(hint) or None)
                    except llm.LLMError as e:
                        log(cfg, f"story link failed {u['id']}: {e}")
                storylib.save(st, S / "stories.json")
            save_json(S / "proposals.json", props)
    summary = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "stats": stats,
               "units": units, "proposals": props, "stories": st}
    save_json(S / "summary.json", summary)
    if hasattr(source, "render"):
        source.render(summary)
    log(cfg, f"tick done in {time.time() - t0:.0f}s: {stats}")
    return stats
