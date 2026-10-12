"""kit 的离线测试：真实文件 I/O、真实子进程，LLM 用 stub 后端（确定性回放），不调用真实模型。

    python3 -m unittest discover -s kit/tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
sys.path.insert(0, str(KIT / "examples"))

import bot  # noqa: E402
import calib  # noqa: E402
import llm  # noqa: E402
import render_rules  # noqa: E402
import rules  # noqa: E402
import sessions  # noqa: E402
import stories  # noqa: E402
import triage  # noqa: E402
from inbox_source import InboxSource, drop_reason  # noqa: E402

EXAMPLE = KIT / "examples" / "rules.example.yaml"


def stub(tmp, responses):
    p = Path(tmp) / "stub.json"
    p.write_text(json.dumps(responses, ensure_ascii=False))
    os.environ["AGENT_LLM_BACKEND"] = "stub"
    os.environ["AGENT_LLM_STUB"] = str(p)
    llm._stub_i = 0


def ask(summary="修导出 bug", branch="B02", level="partial", **kw):
    a = {"summary": summary, "requester": "甲", "trigger_ids": [], "branch_id": branch, "gates": ["G2_write"],
         "rule_applied": None, "level": level, "reason": "r", "steps": ["复现"], "stop_at": "合并前",
         "status": "open", "confidence": "high"}
    a.update(kw)
    return a


class TestRules(unittest.TestCase):
    def test_example_loads_and_renders_prompt(self):
        r = rules.load(EXAMPLE)
        self.assertIn("B01", rules.branch_ids(r))
        block = rules.prompt_block(r)
        self.assertIn("G1_outward", block)
        self.assertIn("默认 level=partial", block)

    def test_validate_rejects_bad_rules(self):
        bad = {"version": 1, "gates": {"G1": {"name": "x", "rule": "y"}},
               "branches": [{"id": "B1", "l1": "a", "l2": "b", "l3": "c", "level": "maybe"},
                            {"id": "B2", "l1": "a", "l2": "b", "l3": "c", "level": "full"}]}
        with self.assertRaises(rules.RulesError) as cm:
            rules.validate(bad)
        self.assertIn("B1 的 level 不合法", str(cm.exception))
        self.assertIn("B2 不是 skip 却没写 stop_at", str(cm.exception))

    def test_render_html_marks_uncalibrated_branches(self):
        r = rules.load(EXAMPLE)
        cases = [{"id": "C1", "expected": {"level": "partial", "branch_id": "B02"}, "note": "导出失败"}]
        page = render_rules.render(r, cases, {"match": {"level": "1/1"}, "mismatches": [], "generated_at": "2026-10-12T10:00"})
        self.assertIn("历史 case 1 件", page)
        self.assertIn("规则未经校准", page)
        self.assertIn("level 1/1", page)


class TestLLMAndTriage(unittest.TestCase):
    def test_stub_cycles_and_quota_errors_are_flagged(self):
        with tempfile.TemporaryDirectory() as t:
            stub(t, [{"a": 1}, {"__error__": "usage limit reached"}])
            self.assertEqual(llm.call("p", {})[0], {"a": 1})
            with self.assertRaises(llm.LLMError) as cm:
                llm.call("p", {})
            self.assertTrue(cm.exception.is_quota)

    def test_propose_nulls_invented_branches(self):
        r = rules.load(EXAMPLE)
        unit = {"id": "u1", "where": "群A", "triggers": [{"id": "m1", "ts": "2026-10-01 10:00", "who": "甲", "text": "导出挂了"}]}
        with tempfile.TemporaryDirectory() as t:
            stub(t, {"asks": [ask(branch="B99"), ask(branch="B02", level="whatever")]})
            out, _ = triage.propose(r, unit, persona="我是工程师")
        self.assertIsNone(out["asks"][0]["branch_id"])
        self.assertEqual(out["asks"][1]["level"], "partial")
        prompt = triage.build_prompt(r, unit, "我是工程师")
        self.assertIn("★ [m1]", prompt)


class TestStories(unittest.TestCase):
    def test_link_new_attach_and_unlink(self):
        st = {}
        new = lambda a, c: {"object": "导出", "story_id": None, "title": "甲 要求 修导出", "reason": "新"}
        sid = stories.link(st, ask(), "u1", "群A", "2026-10-01 10:00", ["导出挂了"], linker=new)
        attach = lambda a, c: {"object": "导出", "story_id": c[0]["id"], "title": "甲 要求 修导出", "reason": "催办"}
        sid2 = stories.link(st, ask(summary="问导出进度", requester="乙"), "u2", "私聊 · 乙", "2026-10-02 09:00", ["好了吗"], linker=attach)
        self.assertEqual(sid, sid2)
        self.assertEqual(st[sid]["requesters"], ["甲", "乙"])
        self.assertEqual(st[sid]["where"], ["群A", "私聊 · 乙"])
        hint = stories.unlink_unit(st, "u1")
        self.assertEqual(len(hint), 1)
        self.assertEqual([r["unit_id"] for r in st[sid]["refs"]], ["u2"])

    def test_old_stories_drop_out_of_candidates(self):
        st = {"s1": {"id": "s1", "title": "t", "updated": "2026-09-01 10:00", "refs": []}}
        self.assertEqual(stories.candidates(st, datetime(2026, 10, 1)), [])


RUNNER = '''
import json, os
from pathlib import Path
def run(case):
    n = Path(os.environ["CALLS"]); n.write_text(str(int(n.read_text() or 0) + 1))
    return {"level": case["input"]["answer"], "reason": "stub"}
'''


class TestCalib(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        d = Path(self.t.name)
        (d / "runner.py").write_text(RUNNER)
        (d / "calls").write_text("0")
        os.environ["CALLS"] = str(d / "calls")
        self.cases = d / "cases.json"
        self.cases.write_text(json.dumps([
            {"id": "C1", "input": {"answer": "full"}, "expected": {"level": "full"}},
            {"id": "C2", "input": {"answer": "skip"}, "expected": {"level": "partial"}, "note": "应该 partial"},
            {"id": "C3", "input": {"answer": "no"}, "expected": {}},  # 标准答案不明 → 跳过
        ]))
        self.d = d
        self.args = ["--runner", str(d / "runner.py"), "--cases", str(self.cases), "--fields", "level", "--workers", "1"]

    def tearDown(self):
        self.t.cleanup()

    def test_mismatch_report_exit_code_and_cache(self):
        self.assertEqual(calib.main(self.args), 1)
        rep = json.loads((self.d / "report.json").read_text())
        self.assertEqual(rep["match"]["level"], "1/2")
        self.assertEqual([m["id"] for m in rep["mismatches"]], ["C2"])
        self.assertEqual((self.d / "calls").read_text(), "2")
        calib.main(self.args)  # 缓存命中：不再调用 runner
        self.assertEqual((self.d / "calls").read_text(), "2")
        calib.main(self.args + ["--fresh"])
        self.assertEqual((self.d / "calls").read_text(), "4")

    def test_watch_file_change_invalidates_cache(self):
        w = self.d / "rules.yaml"
        w.write_text("v1")
        calib.main(self.args + ["--watch", str(w)])
        w.write_text("v2")
        calib.main(self.args + ["--watch", str(w)])
        self.assertEqual((self.d / "calls").read_text(), "4")

    def test_stability_requires_every_run_to_pass(self):
        cmd = [str(KIT / "stability.sh"), "2", "--", sys.executable, str(KIT / "calib.py")] + self.args
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("passed 0/2", r.stdout)
        self.assertTrue((self.d / "report.run2.json").exists())

    def test_assign_maximises_total_similarity(self):
        outs = [{"summary": "修复导出任务失败"}, {"summary": "开数据库只读权限"}]
        got = calib.assign({"A": "给我开数据库权限", "B": "导出任务失败了"}, outs)
        self.assertEqual(got["A"]["summary"], "开数据库只读权限")
        self.assertEqual(got["B"]["summary"], "修复导出任务失败")


class TestSessions(unittest.TestCase):
    def test_find_related_skips_automation_and_future(self):
        with tempfile.TemporaryDirectory() as t:
            proj = Path(t) / "claude" / "proj"
            proj.mkdir(parents=True)
            def write(name, first, ts):
                lines = [{"type": "user", "timestamp": ts, "cwd": "/x", "message": {"content": first}},
                         {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "在查 PR #42"}]}}]
                (proj / f"{name}.jsonl").write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines))
            write("human", "看一下 PR #42", "2026-10-01T10:00:00Z")
            write("auto", "你是自动巡检 agent，PR #42", "2026-10-01T10:00:00Z")
            write("future", "PR #42 后续", "2026-10-09T10:00:00Z")
            old_roots, old_pref = sessions.ROOTS, sessions.AUTOMATION_PREFIXES
            sessions.ROOTS = {"claude": Path(t) / "claude", "codex": Path(t) / "codex"}
            sessions.AUTOMATION_PREFIXES = ("你是自动巡检",)
            try:
                hits = sessions.find_related(["#42"], until=datetime(2026, 10, 5))
            finally:
                sessions.ROOTS, sessions.AUTOMATION_PREFIXES = old_roots, old_pref
        self.assertEqual([h["session_id"] for h in hits], ["human"])
        self.assertEqual(hits[0]["last_assistant"], "在查 PR #42")


class TestBotTick(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        d = Path(self.t.name)
        self.inbox = d / "inbox.json"
        self.inbox.write_text(json.dumps([
            {"id": "m1", "conv": "群A", "ts": "2026-10-01 10:00", "who": "甲", "text": "导出挂了 @我", "to_me": True},
            {"id": "m2", "conv": "群A", "ts": "2026-10-01 10:05", "who": "某 bot", "text": "告警", "to_me": True, "from_bot": True},
            {"id": "m3", "conv": "群B", "ts": "2026-10-01 11:00", "who": "乙", "text": "@所有人 周五团建", "to_me": True},
            {"id": "m4", "conv": "私聊乙", "ts": "2026-10-01 12:00", "who": "乙", "text": "能开个库权限吗", "to_me": True},
        ], ensure_ascii=False))
        self.cfg = bot.Config(state_dir=d / "state", rules_path=EXAMPLE, persona="我是工程师", use_stories=False)
        self.d = d

    def tearDown(self):
        self.t.cleanup()

    def test_stage0_filters(self):
        self.assertEqual(drop_reason({"from_bot": True}), "F1_bot")
        self.assertEqual(drop_reason({"who": "乙", "text": "@所有人 x", "to_me": True}), "F3_broadcast")
        self.assertIsNone(drop_reason({"who": "乙", "text": "x", "to_me": True}))

    def test_revision_gating_and_quota_break(self):
        stub(self.d, [{"__error__": "usage limit reached"}])
        stats = bot.tick(InboxSource(self.inbox), self.cfg)
        self.assertEqual(stats["pending"], 2)       # m2 bot、m3 广播被 stage0 丢掉
        self.assertEqual(stats["llm_errors"], 1)    # 额度错误 → 立刻停，第二个单元留到下一轮
        stub(self.d, {"asks": [ask()]})
        stats = bot.tick(InboxSource(self.inbox), self.cfg)
        self.assertEqual(stats["llm_calls"], 2)
        stats = bot.tick(InboxSource(self.inbox), self.cfg)
        self.assertEqual(stats["llm_calls"], 0)     # revision 没变 → 不重跑
        msgs = json.loads(self.inbox.read_text())
        msgs.append({"id": "m5", "conv": "群A", "ts": "2026-10-01 13:00", "who": "甲", "text": "好了吗", "to_me": True})
        self.inbox.write_text(json.dumps(msgs, ensure_ascii=False))
        stats = bot.tick(InboxSource(self.inbox), self.cfg)
        self.assertEqual(stats["llm_calls"], 1)     # 群A 有新触发消息 → 只重跑这一个
        units = json.loads((self.cfg.state_dir / "units.json").read_text())
        self.assertEqual(len(units), 2)

    def test_story_layer_links_across_units(self):
        self.cfg.use_stories = True
        stub(self.d, [{"asks": [ask(summary="修导出")]},
                      {"object": "导出", "story_id": None, "title": "甲 要求 修导出", "reason": "新"},
                      {"asks": [ask(summary="开库权限", branch="B04", level="full")]},
                      {"object": "数据库", "story_id": None, "title": "乙 要求 开库权限", "reason": "对象不同"}])
        bot.tick(InboxSource(self.inbox), self.cfg)
        st = json.loads((self.cfg.state_dir / "stories.json").read_text())
        self.assertEqual(sorted(s["title"] for s in st.values()), ["乙 要求 开库权限", "甲 要求 修导出"])

    def test_refuses_documents_state_dir(self):
        with self.assertRaises(ValueError):
            bot.Config(state_dir=Path.home() / "Documents" / "x", rules_path=EXAMPLE, persona="p")


class TestLaunchd(unittest.TestCase):
    def test_install_script_syntax(self):
        r = subprocess.run(["bash", "-n", str(KIT / "launchd" / "install.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
