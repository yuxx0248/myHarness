#!/usr/bin/env python3
"""把规则导图（+ 可选的校准报告和 case）渲染成给人审的 HTML 页面。

    python3 kit/render_rules.py rules.yaml out.html [--cases calib/cases.json --report calib/report.json]

case 里 expected.branch_id 用来把历史样本挂到对应分支下；没有样本的分支会标「规则未经校准」。
"""
import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rules as rulelib  # noqa: E402

LV = {"full": "可独立", "partial": "起草待确认", "no": "需本人", "skip": "跳过"}
e = lambda s: html.escape(str(s if s is not None else ""))


def leaf(b, cs):
    rules = "".join(f'<li><span class="mut">如果</span> {e(r["if"])} <span class="mut">→</span> <span class="lv {r["then"]}">{r["then"]}</span></li>'
                    for r in b.get("rules") or [])
    steps = " → ".join(e(s) for s in b.get("steps") or []) or "—"
    ex = "".join(f'<li><span class="mono">{e(c["id"])}</span> <span class="lv sm {c["expected"].get("level")}">{e(c["expected"].get("level"))}</span> '
                 f'{e((c.get("note") or c.get("summary") or "")[:70])}</li>' for c in cs)
    return f'''<article class="leaf">
  <header><span class="mono">{e(b["id"])}</span><h4>{e(b["l2"])} <span>› {e(b["l3"])}</span></h4><span class="lv {b["level"]}">{b["level"]} · {LV[b["level"]]}</span></header>
  <p class="mut sm">{e("；".join(b.get("signals") or []))}</p>
  {f'<ul class="rules">{rules}</ul>' if rules else ''}
  <dl><dt>怎么做</dt><dd>{steps}</dd><dt>停在</dt><dd>{e(b.get("stop_at"))}</dd>
  {f'<dt>需要</dt><dd>{e("；".join(b["needs"]))}</dd>' if b.get("needs") else ''}
  {f'<dt>备注</dt><dd>{e(b["note"])}</dd>' if b.get("note") else ''}</dl>
  {f'<details><summary>历史 case {len(cs)} 件</summary><ul class="ex">{ex}</ul></details>' if cs else '<p class="mut sm"><i>历史里没有样本，规则未经校准</i></p>'}
</article>'''


def render(r, cases=(), report=None):
    by_branch = {}
    for c in cases:
        if c.get("expected"):
            by_branch.setdefault(c["expected"].get("branch_id"), []).append(c)
    groups = []
    for b in r["branches"]:
        if not groups or groups[-1][0] != b["l1"]:
            groups.append((b["l1"], []))
        groups[-1][1].append(b)
    tree = "".join(f'<section><h3>{e(g)} <small>{len(bs)} 个分支 · 历史 {sum(len(by_branch.get(b["id"], [])) for b in bs)} 件</small></h3>'
                   f'<div class="leaves">{"".join(leaf(b, by_branch.get(b["id"], [])) for b in bs)}</div></section>' for g, bs in groups)
    gates = "".join(f'<tr><td class="mono">{e(k)}</td><td><b>{e(g["name"])}</b></td><td>{e(g["rule"])}</td></tr>' for k, g in r["gates"].items())
    stage0 = "".join(f'<li><span class="mono">{e(f["id"])}</span> {e(f["rule"])}</li>' for f in r.get("stage0_filters") or [])
    levels = "".join(f'<div class="lvrow"><span class="lv {k}">{k}</span><span>{e(v)}</span></div>' for k, v in (r.get("levels") or rulelib.LEVEL_TEXT).items())
    stat = ""
    if report:
        stat = "".join(f"<span>{e(f)} {e(m)}</span>" for f, m in report.get("match", {}).items())
        stat += f"<span>不一致 {len(report.get('mismatches', []))}</span><span>{e(str(report.get('generated_at', ''))[:16].replace('T', ' '))}</span>"
    title = r.get("title") or "分诊规则导图"
    return f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<style>
:root{{--bg:#f6f7f5;--panel:#fff;--fg:#1d2420;--muted:#66706a;--line:#dfe3df;--accent:#2f6b55;
  --full:#2f7d4f;--full-bg:#e2f2e8;--partial:#a9701a;--partial-bg:#f8eedb;--no:#9a3b3b;--no-bg:#f6e3e1;--skip:#6b6f6c;--skip-bg:#ececea}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#141917;--panel:#1b211e;--fg:#e4e9e5;--muted:#9aa49e;--line:#2c3430;--accent:#7cc2a3;
  --full:#7fd3a0;--full-bg:#1f3428;--partial:#e5b866;--partial-bg:#3a2f1a;--no:#ef9a92;--no-bg:#3a2321;--skip:#a8aca9;--skip-bg:#2a2f2c;color-scheme:dark}}}}
*{{box-sizing:border-box}} body{{background:var(--bg);color:var(--fg);font-family:-apple-system,"PingFang SC","Noto Sans SC",sans-serif;font-size:14px;line-height:1.6;padding:28px 16px 80px;margin:0}}
.wrap{{max-width:1120px;margin:0 auto;display:flex;flex-direction:column;gap:24px}} h1{{font-size:28px;margin:0}}
h2{{font-size:17px;margin:0 0 10px}} h3{{font-size:17px;margin:0 0 10px;color:var(--accent)}} h3 small{{font-size:12px;color:var(--muted);font-weight:400}}
.mut{{color:var(--muted)}} .sm{{font-size:12.5px;margin:0}} .mono{{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--muted)}}
.stat{{display:flex;flex-wrap:wrap;gap:8px}} .stat span{{font-size:12px;border:1px solid var(--line);border-radius:99px;padding:2px 10px;background:var(--panel)}}
.box{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}}
ul.plain{{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:4px}}
.lv{{display:inline-block;font-size:11.5px;padding:0 8px;border-radius:99px;white-space:nowrap}} .lv.sm{{font-size:10.5px;padding:0 6px}}
.lv.full{{background:var(--full-bg);color:var(--full)}} .lv.partial{{background:var(--partial-bg);color:var(--partial)}} .lv.no{{background:var(--no-bg);color:var(--no)}} .lv.skip{{background:var(--skip-bg);color:var(--skip)}}
.lvrow{{display:flex;gap:10px;align-items:baseline}} .lvrow .lv{{min-width:4.5em;text-align:center}}
.leaves{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px}}
.leaf{{background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--accent);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;min-width:0}}
.leaf header{{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}} .leaf h4{{margin:0;font-size:14.5px;flex:1}} .leaf h4 span{{color:var(--muted);font-weight:400}}
.rules{{margin:0;padding-left:18px;font-size:12.5px}} .leaf dl{{display:grid;grid-template-columns:3.5em 1fr;gap:2px 8px;margin:0;font-size:12.5px}}
.leaf dt{{color:var(--muted)}} .leaf dd{{margin:0;overflow-wrap:anywhere}} details summary{{cursor:pointer;font-size:12px;color:var(--accent)}}
.ex{{margin:6px 0 0;padding-left:16px;font-size:12px}} table{{border-collapse:collapse;width:100%;font-size:13px}} td{{border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top}}
.tw{{overflow-x:auto}}
</style>
<div class="wrap">
<header><h1>{e(title)}</h1><p class="mut">规则导图 v{e(r.get("version"))}：每件进来的事怎么判定该不该介入、介入到哪一步。</p><div class="stat">{stat}</div></header>
<section class="box"><h2>档位</h2>{levels}</section>
{f'<section class="box"><h2>stage 0 规则过滤（确定性，不调 LLM）</h2><ul class="plain">{stage0}</ul></section>' if stage0 else ''}
<section style="display:flex;flex-direction:column;gap:20px"><h2>分类树：动作 › 领域 › 模式</h2>{tree}</section>
<section class="box"><h2>闸 gates</h2><div class="tw"><table>{gates}</table></div></section>
</div>'''


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("rules")
    ap.add_argument("out")
    ap.add_argument("--cases")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    r = rulelib.load(a.rules)
    cases = json.loads(Path(a.cases).read_text()) if a.cases else []
    report = json.loads(Path(a.report).read_text()) if a.report else None
    Path(a.out).write_text(render(r, cases, report), encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
