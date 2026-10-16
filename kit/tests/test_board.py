"""看板（kit/board.py）的离线测试：用临时目录里的 fixture 模拟 Claude Code 与 Codex 的会话文件。"""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

CARD = """改好了。
---
📍 目标：[debug] 查导出失败
✅ 已完成：定位到超时
⏳ 当前：等你确认修复方案
👉 等你：回「1」或「2」"""
DONE_CARD = "📍 目标：[kb] 整理文档\n✅ 已完成：都写好了\n⏳ 当前：已完成\n👉 等你：可以归档"
NO_WAIT_CARD = "📍 目标：重构配置加载\n✅ 已完成：拆完模块\n⏳ 当前：在跑测试\n👉 等你：无，可以继续"


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        r = Path(self.t.name)
        self.dirs = {k: r / k for k in ("projects", "live", "desktop", "codex", "state")}
        for d in self.dirs.values():
            d.mkdir()
        os.environ.update({"AGENT_BOARD_CLAUDE_PROJECTS": str(self.dirs["projects"]), "AGENT_BOARD_CLAUDE_LIVE": str(self.dirs["live"]),
                           "AGENT_BOARD_CLAUDE_DESKTOP": str(self.dirs["desktop"]), "AGENT_BOARD_CODEX": str(self.dirs["codex"]),
                           "AGENT_BOARD_STATE": str(self.dirs["state"]), "AGENT_BOARD_CONFIG": str(r / "board.json"),
                           "AGENT_AUTOMATION_PREFIXES": "你是自动巡检"})
        global board
        import board
        board = importlib.reload(board)
        self.now = time.time()
        self.cfg = r / "board.json"

    def tearDown(self):
        self.t.cleanup()

    def claude(self, sid, prompt, reply, cwd="/work/app", title=None, entry="claude-desktop", turns=2, age_h=1, sched=False):
        ts = self.now - age_h * 3600
        lines = []
        for i in range(turns):
            content = ("<scheduled-task name=x>" if sched else "") + (prompt if i == 0 else "继续")
            lines.append({"type": "user", "turnOrigin": "human", "timestamp": iso(ts + i), "cwd": cwd, "entrypoint": entry,
                          "sessionId": sid, "message": {"role": "user", "content": content}})
            lines.append({"type": "assistant", "timestamp": iso(ts + i + 0.5), "cwd": cwd, "sessionId": sid,
                          "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
        if title:
            lines.append({"type": "custom-title", "customTitle": title, "sessionId": sid})
        d = self.dirs["projects"] / "-work-app"
        d.mkdir(exist_ok=True)
        p = d / f"{sid}.jsonl"
        p.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines))
        os.utime(p, (ts + turns, ts + turns))

    def desktop(self, sid, app_id, title, archived=False):
        (self.dirs["desktop"] / f"{app_id}.json").write_text(json.dumps(
            {"sessionId": app_id, "cliSessionId": sid, "title": title, "isArchived": archived, "lastActivityAt": str(int((self.now - 3600) * 1000)),
             "postTurnSummary": {"status_category": "completed", "status_detail": "根因找到了"}}, ensure_ascii=False))

    def test_buckets_worklines_and_links(self):
        self.claude("s-wait", "导出挂了", CARD, title="[debug] 导出失败")
        self.desktop("s-wait", "local_aaa", "[debug] 导出失败")
        self.claude("s-done", "整理文档", DONE_CARD, title="[kb] 文档")
        self.claude("s-act", "重构配置", NO_WAIT_CARD, cwd="/work/app/.claude/worktrees/x-1", title="配置重构")
        self.claude("s-old", "很久以前的事", NO_WAIT_CARD, title="老任务", age_h=100)
        b = board.build(since_hours=200, now=self.now)
        by = {r["id"]: r for r in b["rows"]}
        self.assertEqual(by["s-wait"]["bucket"], "waiting")
        self.assertEqual(by["s-wait"]["workline"], "debug")
        self.assertEqual(by["s-wait"]["card"]["wait"], "回「1」或「2」")
        self.assertEqual(by["s-done"]["bucket"], "done")
        self.assertEqual(by["s-act"]["bucket"], "active")
        self.assertEqual(by["s-act"]["workline"], "app")           # worktree 路径还原成仓库名
        self.assertEqual(by["s-old"]["bucket"], "done")            # >3 天无活动
        md = board.render(b, md=True)
        self.assertIn(r"[\[debug\] 导出失败](#local_aaa)", md)     # 方括号转义 + app 内链接
        self.assertIn("claude --resume s-act", md)                 # 没有 app 链接的给 resume 命令
        self.assertTrue(md.splitlines()[0].startswith("2 条线 · 4 个会话 · 1 个在等你"))

    def test_filters(self):
        self.claude("s-ok", "正常任务", NO_WAIT_CARD, title="正常")
        self.claude("s-sdk", "分类这条消息", "{}", entry="sdk-cli")
        self.claude("s-sched", "跑日报", "ok", sched=True, title="日报")
        self.claude("s-auto", "你是自动巡检 agent，看一下", "ok", title="巡检")
        self.claude("s-one", "读一下这张图", "图里是…", turns=1)
        self.claude("s-arch", "旧事", NO_WAIT_CARD, title="归档的")
        self.desktop("s-arch", "local_arch", "归档的", archived=True)
        for i in range(3):
            self.claude(f"s-rt{i}", "刷新看板", "ok", title="看板每日刷新")
        b = board.build(now=self.now)
        self.assertEqual([r["id"] for r in b["rows"]], ["s-ok"])
        self.assertEqual(b["dropped"], {"sdk": 1, "scheduled": 1, "automation": 1, "oneshot": 1, "archived": 1, "routine": 3})
        self.assertEqual(len(board.build(include_all=True, now=self.now)["rows"]), 9)

    def test_hide_until_new_activity(self):
        self.claude("s1", "任务", NO_WAIT_CARD, title="任务一")
        board.hide(["s1"])
        self.assertEqual(board.build(now=self.now)["rows"], [])
        self.claude("s1", "任务", NO_WAIT_CARD, title="任务一", age_h=-1)  # 隐藏之后又有新活动
        self.assertEqual([r["id"] for r in board.build(now=self.now + 7200)["rows"]], ["s1"])

    def test_no_card_falls_back_to_summary(self):
        self.claude("s-nc", "看一下这个告警", "查了一半，还没结论", title="告警排查")
        self.desktop("s-nc", "local_nc", "告警排查")
        b = board.build(now=self.now)
        md = board.render(b)
        self.assertIn("无状态卡，待推断", md)
        self.assertIn("根因找到了", md)   # 桌面端每轮摘要优先于最后一段回复

    def test_live_process_marks_running(self):
        self.claude("s-run", "长任务", NO_WAIT_CARD, title="旧标题")
        (self.dirs["live"] / "1.json").write_text(json.dumps(
            {"pid": os.getpid(), "sessionId": "s-run", "status": "busy", "name": "[infra] 新标题", "hostSessionId": "local_run"}))
        r = board.build(now=self.now)["rows"][0]
        self.assertTrue(r["running"])
        self.assertEqual((r["title"], r["app_id"], r["workline"]), ("[infra] 新标题", "local_run", "infra"))

    def test_codex_rollout(self):
        d = self.dirs["codex"] / "2026" / "10" / "15"
        d.mkdir(parents=True)
        ts = self.now - 1800
        lines = [{"timestamp": iso(ts), "type": "session_meta", "payload": {"id": "cx-1", "cwd": "/work/svc", "timestamp": iso(ts)}},
                 {"timestamp": iso(ts + 1), "type": "response_item", "payload": {"type": "message", "role": "user",
                  "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>"}]}},
                 {"timestamp": iso(ts + 2), "type": "response_item", "payload": {"type": "message", "role": "user",
                  "content": [{"type": "input_text", "text": "修一下限流"}]}},
                 {"timestamp": iso(ts + 3), "type": "response_item", "payload": {"type": "message", "role": "assistant",
                  "content": [{"type": "output_text", "text": "📍 目标：[svc] 修限流\n✅ 已完成：复现\n⏳ 当前：写修复\n👉 等你：确认阈值"}]}}]
        (d / "rollout-2026-10-15T10-00-00-cx-1.jsonl").write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines))
        r = board.build(now=self.now)["rows"][0]
        self.assertEqual((r["runtime"], r["id"], r["workline"], r["bucket"]), ("codex", "cx-1", "svc", "waiting"))
        self.assertIn("codex resume cx-1", board.render(board.build(now=self.now), md=False))

    def test_config_workline_and_skip(self):
        self.cfg.write_text(json.dumps({"workline_by_path": {"/work/app": "core"}, "skip_cwd": ["/scratch/"]}))
        self.claude("a", "任务", NO_WAIT_CARD, title="无前缀标题")
        self.claude("b", "任务", NO_WAIT_CARD, title="另一个", cwd="/scratch/x")
        b = board.build(now=self.now)
        self.assertEqual([(r["id"], r["workline"]) for r in b["rows"]], [("a", "core")])

    def test_exclude_self(self):
        self.claude("me", "我回来了", NO_WAIT_CARD, title="where-was-i")
        self.claude("other", "任务", NO_WAIT_CARD, title="别的线")
        self.assertEqual([r["id"] for r in board.build(now=self.now, exclude={"me"})["rows"]], ["other"])

    def test_parse_card_variants(self):
        c = board.parse_card("- 📍 目标: [ops] 迁移\n- ✅ 已完成：A\n- ⏳ 当前：B\n- 👉 等你：无，可以收尾")
        self.assertEqual(c, {"goal": "[ops] 迁移", "done": "A", "now": "B", "wait": "无，可以收尾"})
        self.assertIsNone(board.parse_card("没有卡"))


if __name__ == "__main__":
    unittest.main()
