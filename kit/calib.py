#!/usr/bin/env python3
"""校准 harness：用和线上**同一条流水线**重放金标准 case，逐字段对比，0 不一致才算过。改规则 / prompt 的 CI。

    python3 kit/calib.py --runner my_runner.py --cases calib/cases.json --fields level,branch_id \\
        --watch rules.yaml,src/prompt.py [--fresh] [--only C01,C07] [--workers 6]

runner 模块（你写，约定如下）：
    def run(case) -> dict                       # 跑一条 case，返回预测（含 --fields 里的字段）
    # 可选：多条 case 来自同一个输入单元时，只跑一次再分配
    def group_key(case) -> str
    def run_group(cases) -> {case_id: dict}

cases.json：[{"id": "C01", "input": {...}, "expected": {"level": "partial", ...}, "note": "..."}]
- expected 里值为 null 的字段不比较；expected 为空 / 缺失的 case 跳过（标准答案不明）。
- 标注例外（覆盖、排除、补触发）写在生成 cases.json 的脚本顶部，每条带理由（见 knowledge-system playbook §5）。

缓存：键 = sha1(--watch 列出的所有文件内容)。规则或 prompt 一改，全部重跑；--fresh 绕过缓存。
输出：report.json（与 cases.json 同目录），打印不一致；有不一致时退出码 1。
"""
import argparse
import hashlib
import importlib.util
import itertools
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


# ---------- 文本相似度：把 LLM 产出的多条结果对回 case（摘要会改写原文，所以用无序的字符 bigram） ----------

def bigrams(t):
    t = "".join(ch.lower() for ch in (t or "") if not ch.isspace())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def tsim(a, b):
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / (len(A | B) or 1)


def assign(case_texts, outputs, text_of=lambda o: o.get("summary", "")):
    """case_texts: {case_id: 文本}；outputs: LLM 的多条产出。返回 {case_id: 最匹配的产出或 None}，总相似度最大。"""
    ids = list(case_texts)
    if len(ids) == 1 or len(outputs) < len(ids):
        return {i: (max(outputs, key=lambda o: tsim(text_of(o), case_texts[i])) if outputs else None) for i in ids}
    pool = outputs + [None] * len(ids)
    best, best_s = {}, -1
    for perm in itertools.permutations(range(len(pool)), len(ids)):
        s = sum(tsim(text_of(pool[j]), case_texts[i]) if pool[j] else 0 for i, j in zip(ids, perm))
        if s > best_s:
            best_s, best = s, {i: pool[j] for i, j in zip(ids, perm)}
    return best


# ---------- harness ----------

def watch_hash(paths):
    h = hashlib.sha1()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()[:10]


def load_runner(path):
    spec = importlib.util.spec_from_file_location("calib_runner", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(path).resolve().parent))
    spec.loader.exec_module(mod)
    return mod


def run_all(runner, cases, fields, watch, cache_path, fresh=False, only=None, workers=6):
    h = watch_hash(watch) if watch else "nowatch"
    cache = {} if fresh or not cache_path.exists() else json.loads(cache_path.read_text())
    todo = [c for c in cases if c.get("expected") and (not only or c["id"] in only)]
    if hasattr(runner, "group_key") and hasattr(runner, "run_group"):
        groups = {}
        for c in todo:
            groups.setdefault(runner.group_key(c), []).append(c)
        jobs = [g for g in groups.values() if any(cache.get(c["id"], {}).get("hash") != h for c in g)]
        def work(g):
            try:
                return runner.run_group(g)
            except Exception as e:
                return {c["id"]: {"__error__": str(e)[:300]} for c in g}
    else:
        jobs = [c for c in todo if cache.get(c["id"], {}).get("hash") != h]
        def work(c):
            try:
                return {c["id"]: runner.run(c)}
            except Exception as e:
                return {c["id"]: {"__error__": str(e)[:300]}}
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(work, jobs):
            for cid, got in res.items():
                cache[cid] = {"hash": h, "got": got}
            cache_path.write_text(json.dumps(cache, ensure_ascii=False))
            print(".", end="", flush=True)
    print()
    rows = []
    for c in todo:
        got = cache[c["id"]]["got"]
        if "__error__" in got:
            rows.append({"id": c["id"], "ok": False, "error": got["__error__"]})
            continue
        diffs = {f: {"expected": c["expected"].get(f), "got": got.get(f)} for f in fields
                 if c["expected"].get(f) is not None and got.get(f) != c["expected"].get(f)}
        rows.append({"id": c["id"], "ok": not diffs, "diffs": diffs, "note": c.get("note"),
                     "reason": got.get("reason"), "rule": got.get("rule_applied")})
    per_field = {f: sum(1 for c in todo if c["expected"].get(f) is not None and "__error__" not in cache[c["id"]]["got"]
                        and cache[c["id"]]["got"].get(f) == c["expected"][f]) for f in fields}
    totals = {f: sum(1 for c in todo if c["expected"].get(f) is not None) for f in fields}
    return {"hash": h, "generated_at": datetime.now().isoformat(timespec="seconds"), "n": len(rows),
            "match": {f: f"{per_field[f]}/{totals[f]}" for f in fields},
            "mismatches": [r for r in rows if not r["ok"]], "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runner", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--fields", required=True, help="逗号分隔，要逐字比较的字段")
    ap.add_argument("--watch", default="", help="逗号分隔；这些文件变了缓存就失效（规则、prompt、runner）")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    cases_path = Path(a.cases)
    cases = json.loads(cases_path.read_text())
    watch = [p for p in a.watch.split(",") if p] + [a.runner]
    report = run_all(load_runner(a.runner), cases, a.fields.split(","), watch,
                     cases_path.with_name(".calib-cache.json"), a.fresh, set(filter(None, a.only.split(","))), a.workers)
    (cases_path.with_name("report.json")).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print("  ".join(f"{f} {m}" for f, m in report["match"].items()) + f"  mismatches {len(report['mismatches'])}")
    for r in report["mismatches"]:
        if "error" in r:
            print(f"  {r['id']} ERROR {r['error']}")
        else:
            d = "; ".join(f"{f} {v['got']}≠{v['expected']}" for f, v in r["diffs"].items())
            print(f"  {r['id']} {d} | {r.get('rule') or ''} | {r.get('reason') or ''}")
    return 0 if not report["mismatches"] else 1


if __name__ == "__main__":
    sys.exit(main())
