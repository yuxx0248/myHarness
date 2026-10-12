# kit · 参考实现

playbook 讲方法，kit 是这些方法的**可运行骨架**：一个「只出提案」的分诊 bot 需要的全部通用部件，外加改规则时用的校准 CI。
只依赖 Python 标准库（YAML 规则需要 PyYAML，也可以用 JSON）。不含任何领域内容 —— 消息源、过滤规则、规则导图的分支都由你在自己的仓库里写。

| 文件 | 做什么 | 对应的方法 |
|---|---|---|
| `llm.py` | 非交互 LLM 调用的唯一封装：无工具、无 MCP、不加载用户配置、不持久化会话、JSON schema 输出；后端 `claude` / `codex` / `stub`；额度错误可识别（`e.is_quota`） | automation §3 |
| `rules.py` | 规则即数据：加载 + 校验规则导图（stage0 / levels / gates / branches），渲染成给 LLM 的紧凑文本 | autonomy-and-triage §1–§4 |
| `triage.py` | 一个单元 → 一次 LLM 调用 → 拆 ask、落分支、过闸、定档 + 步骤 + stop_at + needs；事后校验（不在导图里的分支置空） | autonomy-and-triage §1、§7 |
| `stories.py` | 把散在不同会话 / 渠道里的 ask 聚成「一件真实世界的事」；独立的小调用，不碰已校准的主 prompt | automation §2.1 |
| `sessions.py` | 只读检索本机 Claude Code / Codex 会话 transcript：「是不是已经有会话在处理」；区分人开的和自动化起的会话；`until` 防泄漏 | autonomy-and-triage 闸「已在处理」 |
| `bot.py` | tick 骨架：单实例锁 → 确定性 scan → 只对 revision 变了的单元调 LLM（最早的先、每轮上限、额度错误立刻停）→ story 归并 → summary.json | automation §2、§4 |
| `calib.py` | 校准 harness：同一条流水线重放金标准 case，逐字段比对；缓存键 = 规则 / prompt 文件的 hash；多产出用 bigram-Jaccard 对回 case | knowledge-system §5 |
| `stability.sh` | 连续 N 轮全新校准，每轮都必须 0 不一致 | knowledge-system §5 |
| `render_rules.py` | 把规则导图（+ 校准报告 + 历史 case）渲染成给人审的 HTML | — |
| `launchd/` | macOS 定时任务模板 + 幂等安装（状态 / 日志不放 `~/Documents`） | automation §6 |
| `examples/` | 领域无关的规则导图示例、最小的 source 适配器（JSON 收件箱） | — |
| `tests/` | 离线测试（stub 后端，不调用真实模型）；`scripts/build.sh` 会跑 | — |

## 用它搭一个新 bot

1. 在你自己的仓库里写 source 适配器（参考 `examples/inbox_source.py`）：
   - `scan(state, units)`：确定性，不调 LLM。拉新消息（水位 − 重叠窗口，按稳定 id 去重）→ stage0 过滤（纯函数，配真实历史数据的单测）→ 并入已有单元或新开 → 有新触发消息就更新 `revision`。
   - `terms(unit)`（可选）：检索相关本机会话用的关键词。
   - `render(summary)`（可选）：写到你的 viewer。
2. 复制 `examples/rules.example.yaml`，按你的领域改分支；闸和档位通常可以不动。用 `render_rules.py` 渲染出来自己审一遍。
3. tick 入口：
   ```python
   import sys; sys.path.insert(0, "<myHarness>/kit")
   import bot
   bot.tick(MySource(), bot.Config(state_dir="~/Library/Application Support/my-bot", rules_path="rules.yaml",
                                   persona="我是 <角色>，<负责什么>"))
   ```
4. 只出提案，跑一段时间；我在 viewer 里评估，评估结果就是金标准。
5. 写 `calib/build_cases.py` 把评估过的历史重建成线上同形的 case（`cases.json`），例外写在文件顶部并带理由；写 runner：
   ```python
   # calib/runner.py
   def run(case):   # 和线上同一条路径
       out, _ = triage.propose(rules.load(RULES), case["input"]["unit"], PERSONA)
       best = calib.assign({case["id"]: case["input"]["text"]}, out["asks"])[case["id"]] or {"level": "skip"}
       return {"level": best["level"], "branch_id": best.get("branch_id"), "reason": best.get("reason")}
   ```
   ```bash
   python3 kit/calib.py --runner calib/runner.py --cases calib/cases.json --fields level,branch_id --watch rules.yaml
   kit/stability.sh 2 -- python3 kit/calib.py --runner calib/runner.py --cases calib/cases.json --fields level,branch_id --watch rules.yaml
   ```
6. 上 launchd：`kit/launchd/install.sh my-bot "python3 ~/my-bot/tick.py" 10 5`，再 `launchctl kickstart` 在真实上下文里跑一轮、读日志里的正向标记。
7. 某个分支稳定了，再按分支放权（档位上调 / 允许跨过某个 stop_at）。

## 环境变量

| 变量 | 作用 |
|---|---|
| `AGENT_LLM_BACKEND` | `claude`（默认）/ `codex` / `stub` |
| `AGENT_LLM_BIN` | CLI 路径（launchd 里用稳定路径，不要带版本号的） |
| `AGENT_LLM_MODEL` | 默认模型（默认 `sonnet`） |
| `AGENT_LLM_TOKEN_FILE` | 长期 OAuth token 文件（可选；否则用 keychain 登录态） |
| `AGENT_LLM_STUB` | stub 后端的回答文件（一个对象，或按调用顺序轮流的列表；`{"__error__": "..."}` 模拟错误） |
| `AGENT_STORY_MODEL` | story 挂接用的模型（可以用小模型） |
| `AGENT_SESSION_SKIP_DIRS` | 检索会话时跳过的目录片段，`||` 分隔（例如自动化 bot 自己的会话目录） |
| `AGENT_AUTOMATION_PREFIXES` | 自动化会话首句的前缀，`||` 分隔（这些会话不算「有人在处理」） |

`codex` 后端的参数（`--output-schema`、`--output-last-message`、`--sandbox`）按 codex 版本可能不同，装好后用 `codex exec --help` 核对。
