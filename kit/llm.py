"""非交互 LLM 调用的唯一封装：无工具、无 MCP、不加载用户配置、不持久化会话、按 JSON schema 输出。

脚本 / bot 里所有 LLM 调用都走这里 —— runtime 相关的东西只在这一个文件。

    from llm import call, LLMError
    obj, cost = call(prompt, schema)                 # 后端由 AGENT_LLM_BACKEND 决定，默认 claude
    obj, cost = call(prompt, schema, model="haiku")  # 小模型做闸门 / 分类

后端（环境变量 AGENT_LLM_BACKEND）：
  claude  `claude -p`（默认）。AGENT_LLM_BIN 覆盖二进制路径；AGENT_LLM_TOKEN_FILE 指向长期 OAuth token 文件（可选）
  codex   `codex exec`（参数名按 codex 版本可能不同，装好后用 `codex exec --help` 核对）
  stub    离线测试 / dry-run：从 AGENT_LLM_STUB 指向的 JSON 文件读回答（一个对象，或一个列表按调用顺序轮流返回）
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

DEFAULT_MODEL = os.environ.get("AGENT_LLM_MODEL", "sonnet")
QUOTA_WORDS = ("limit", "quota", "rate", "usage", "额度")


class LLMError(RuntimeError):
    @property
    def is_quota(self):
        """额度 / 限流类错误：调用方应停掉本轮，剩下的留到下一轮，而不是逐条重试。"""
        return any(w in str(self).lower() for w in QUOTA_WORDS)


def call(prompt, schema, model=None, timeout=300, backend=None):
    backend = backend or os.environ.get("AGENT_LLM_BACKEND", "claude")
    if backend == "claude":
        return _claude(prompt, schema, model or DEFAULT_MODEL, timeout)
    if backend == "codex":
        return _codex(prompt, schema, model, timeout)
    if backend == "stub":
        return _stub()
    raise LLMError(f"unknown backend {backend}")


def _claude(prompt, schema, model, timeout):
    env = dict(os.environ)
    tok_file = os.environ.get("AGENT_LLM_TOKEN_FILE")
    if tok_file and Path(tok_file).is_file():
        tok = Path(tok_file).read_text().strip()
        if tok:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    cmd = [os.environ.get("AGENT_LLM_BIN", "claude"), "-p", "--model", model, "--output-format", "json",
           "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
           "--json-schema", json.dumps(schema, ensure_ascii=False)]
    # cwd 放在一个空目录：防止子进程读到调用方仓库的 CLAUDE.md / skill 而递归调用自己
    with tempfile.TemporaryDirectory() as cwd:
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise LLMError(f"claude timeout after {timeout}s")
    try:
        d = json.loads(r.stdout)
    except Exception:
        # 认证错误可能写在 stdout，两个都要带上，否则就是静默失败
        raise LLMError(f"claude non-JSON rc={r.returncode}: {(r.stderr or r.stdout)[:400]}")
    if d.get("is_error") or d.get("subtype") != "success":
        raise LLMError(f"claude error: {str(d.get('result'))[:400]}")
    out = d.get("structured_output")
    if out is None:
        try:
            out = json.loads(d.get("result") or "")
        except Exception:
            raise LLMError("claude: no structured output")
    return out, d.get("total_cost_usd", 0) or 0


def _codex(prompt, schema, model, timeout):
    with tempfile.TemporaryDirectory() as cwd:
        sp, op = Path(cwd, "schema.json"), Path(cwd, "out.json")
        sp.write_text(json.dumps(schema, ensure_ascii=False))
        cmd = [os.environ.get("AGENT_LLM_BIN", "codex"), "exec", "--sandbox", "read-only", "--skip-git-repo-check",
               "--output-schema", str(sp), "--output-last-message", str(op)]
        if model:
            cmd += ["--model", model]
        cmd.append("-")  # prompt 走 stdin
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise LLMError(f"codex timeout after {timeout}s")
        if r.returncode != 0:
            raise LLMError(f"codex rc={r.returncode}: {(r.stderr or r.stdout)[:400]}")
        try:
            return json.loads(op.read_text()), 0
        except Exception:
            raise LLMError(f"codex: no structured output: {(r.stdout or r.stderr)[-400:]}")


_stub_i = 0


def _stub():
    global _stub_i
    p = os.environ.get("AGENT_LLM_STUB")
    if not p:
        raise LLMError("stub backend needs AGENT_LLM_STUB=<json file>")
    data = json.loads(Path(p).read_text())
    if isinstance(data, list):
        out = data[_stub_i % len(data)]
        _stub_i += 1
    else:
        out = data
    if isinstance(out, dict) and out.get("__error__"):
        raise LLMError(out["__error__"])
    return out, 0
