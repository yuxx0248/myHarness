"""规则即数据：一份 YAML / JSON 规则导图 = bot 的全部判定行为（stage0 / levels / gates / branches）。

格式见 examples/rules.example.yaml。改这份文件 = 改 bot 的行为 → 必须跑校准（calib.py）。

    import rules
    r = rules.load("rules.yaml")       # 读 + 校验
    block = rules.prompt_block(r)      # 渲染成给 LLM 的紧凑文本（stage0 在代码里执行，不进 prompt）
"""
import json
from pathlib import Path

LEVELS = ["full", "partial", "no", "skip"]
LEVEL_TEXT = {  # 默认含义；规则文件里的 levels 段可以覆盖措辞
    "full": "agent 可以独立完成，做到 stop_at，把结果交给我确认",
    "partial": "agent 做调查 / 起草方案，需要我确认或补判断后才能推进",
    "no": "必须我本人处理；只列出来提醒，agent 不动手",
    "skip": "不处理、不提醒（只计数）",
}


class RulesError(ValueError):
    pass


def load(path):
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            raise RulesError("YAML 规则需要 PyYAML（pip install pyyaml），或改用 .json")
        r = yaml.safe_load(text)
    else:
        r = json.loads(text)
    validate(r)
    return r


def validate(r):
    errs = []
    for k in ("version", "gates", "branches"):
        if k not in r:
            errs.append(f"缺少顶层字段 {k}")
    levels = r.get("levels") or LEVEL_TEXT
    for lv in levels:
        if lv not in LEVELS:
            errs.append(f"未知档位 {lv}")
    ids = set()
    for b in r.get("branches") or []:
        bid = b.get("id")
        if not bid:
            errs.append(f"分支缺 id: {b}")
            continue
        if bid in ids:
            errs.append(f"分支 id 重复: {bid}")
        ids.add(bid)
        for k in ("l1", "l2", "l3", "level"):
            if k not in b:
                errs.append(f"{bid} 缺 {k}")
        if b.get("level") not in LEVELS:
            errs.append(f"{bid} 的 level 不合法: {b.get('level')}")
        for rule in b.get("rules") or []:
            if rule.get("then") not in LEVELS:
                errs.append(f"{bid} 的规则 then 不合法: {rule}")
            if not rule.get("if"):
                errs.append(f"{bid} 有空的规则条件")
        if b.get("level") != "skip" and not b.get("stop_at"):
            errs.append(f"{bid} 不是 skip 却没写 stop_at")
    for gid, g in (r.get("gates") or {}).items():
        if not g.get("name") or not g.get("rule"):
            errs.append(f"闸 {gid} 缺 name / rule")
    if errs:
        raise RulesError("; ".join(errs))


def branch_ids(r):
    return {b["id"] for b in r["branches"]}


def prompt_block(r):
    """规则导图的紧凑文本，给 LLM 判定用。"""
    levels = r.get("levels") or LEVEL_TEXT
    lines = ["## 档位 level"] + [f"- {k}: {v}" for k, v in levels.items()]
    lines += ["", "## 闸 gates"] + [f"- {k}（{g['name']}）: {g['rule']}" for k, g in r["gates"].items()]
    lines += ["", "## 分类树（叶子 = 分支）。先判 l1（实际要交付什么），再判 l2/l3。"]
    for b in r["branches"]:
        lines.append(f"- {b['id']} {b['l1']} › {b['l2']} › {b['l3']} | 默认 level={b['level']}")
        if b.get("signals"):
            lines.append(f"    信号: {'; '.join(b['signals'])}")
        for rule in b.get("rules") or []:
            lines.append(f"    规则: 如果「{rule['if']}」→ level={rule['then']}")
        if b.get("note"):
            lines.append(f"    备注: {b['note']}")
        if b.get("steps"):
            lines.append(f"    步骤: {' → '.join(b['steps'])}；停在: {b.get('stop_at', '')}")
    return "\n".join(lines)
