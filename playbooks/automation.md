# Playbook · 自动化：bot、定时任务、调 LLM 的流水线

---

## 1. 头号敌人：静默失败

多条流水线都曾死了好几天、没有任何报错，唯一的症状是「东西不再更新」。所以：
- **成功要用正向标记判断**（`Run done`、`✓ 追加成功`、真实的 `message_id`），不能靠「没报错」或文件 mtime。
- **stdout 和 stderr 都记**（CLI 的认证错误可能写在 stdout）；关键路径上不许 `2>/dev/null`。
- 每次运行至少记一行带时间戳的摘要（处理了几条、追加了几条、花了多少钱）；连续 N 次「追加 0 + 总数不变」本身就是告警。
- 吞错的写法都是嫌疑：`catch → []`、`coalesce(x, 1.0)`、`|| true` 在非空结果路径上、INNER JOIN 闸门、只在成功时写的台账。
- 在**真实运行上下文**里验证（`launchctl kickstart` 然后读日志）；会话沙盒里验证会得到假阴性。
- 报故障前先重试 / 重查当前状态；不要凭历史 mtime 断言「仍在故障中」。

## 2. 一个 bot 的标准形状

```
collector  确定性，不调 LLM：拉源数据，水位 − 重叠窗口（索引有延迟），按稳定 id 去重
filters    stage0 规则（bot、自己、广播、别的工具负责的频道）
grouper    按线程 / 同一会话 N 小时聚成工作单元；触发消息总要并进去
llm        只处理 revision 变了的单元；每轮上限；无工具、JSON schema 输出；输出再校验（不在枚举里的 → null）
render     写自己的状态目录；viewer 只读
feedback   viewer 追加 feedback.jsonl → 成为校准的标准答案
```
- 规则作为数据（`sop.yaml` / KB markdown）独立于代码，是**唯一的判定规则来源**；改规则 = 改 bot 行为 → 走校准流程。
- 先便宜后贵：零成本预过滤 → 精确命中跳过 LLM → 模糊的交给小模型（批量）→ 只有真活交给大模型。
- 水位只推进到**已处理**的最高项；扫描起点的水位在完整一轮结束后才推进。
- 「每 N 分钟跑一轮」≠「每个对象每 N 分钟被看一次」—— 报告每个对象的实际节奏。
- 截断策略会悄悄变成优先级策略：要显式定义优先级，而不是尾部截断后再打补丁。

## 3. LLM 调用封装（一个文件，唯一的 runtime 相关点）

```bash
# Claude
claude -p --model sonnet --output-format json --tools "" --strict-mcp-config \
       --setting-sources "" --no-session-persistence --json-schema "$SCHEMA" < prompt.txt
# Codex（对应思路：非交互、只读沙盒、结构化输出）
codex exec --sandbox read-only --output-schema schema.json - < prompt.txt
```
- **嵌套调用必须隔离用户配置**：不加载 skill / MCP / 设置，否则子进程可能命中 skill 并递归调用父脚本。
- prompt 走 stdin；heredoc 模板用 `<<'EOF'`（否则反引号被吃掉），渲染后 grep 一下最终 prompt。
- 读 `structured_output`（兜底解析 `result`）；`is_error` 或 `subtype != success` → 抛错；记录每轮花费。
- 模型分层：闸门 / 分类 / 抽取用小模型，批量提案用中模型，深度调查用大模型。模型和二进制路径都可以用环境变量覆盖。
- 凭据走一个 shim：长期 token 用环境变量传入；预检额度；备用账号；额度错误的正则要宽。
- 缓存键 = hash(规则 + prompt)，任何改动自动全部重跑；`--fresh` 绕过缓存。
- schema 字段的 description 本身就是指令（「对方要我做什么，一句话，带客户 / 系统名」）。

## 4. 可靠性惯用法

- **可靠性靠标记文件，不靠进程活着**：`.done / .posted / .resolved` 标记 + sweeper 补做缺失步骤，24h 后放弃。
- **认领要同步做完再放后台**，否则下一轮会重复派活、重复发言。
- 对外消息带幂等 key；有歧义的错误之后**不要重试非幂等的创建**（会建出重复文件夹 / 重复记录），先 list 再决定。
- 单实例锁（PID + 时间戳 + 过期判断）；JSON 原子写（tmp + rename）。
- watchdog 和主任务分开（心跳放在主任务里，主任务死了 watchdog 才能报警）。
- 节流：任务频率提高时（12h → 10min）必须加每对象冷却和每轮上限，否则放大几十倍。
- 默认安静：没有明确信号就不发；所有模块入队，每轮合并成一条摘要私信（P0 → P1 → P2 → 需要你 → 其他 → 统计）；空队列不发。
- 两条降级路径要一致：正文有兜底，@ 也要有兜底，否则「消息照发、无人被点名」。
- 早退路径也要跑完下游阶段。

## 5. 安全开关

- 每个模块都有 `*_ENABLED` / `*_DRY` 开关和单 case 覆盖变量（`*_ONLY_ID=`）；开关放在线上目录的 `config.env`（真实副本，紧急时 `sed` 一下，下一轮生效，不弄脏 git）。
- 首次运行只播种现状，不处理积压（防止一上线就在几十个群里刷屏）；清空 seen 状态 = 重处理积压，慎用。
- 部署目录 = 指向仓库代码的软链 + 真实的配置副本；`deploy.sh --check` 检测漂移；定时任务拒绝运行没部署的代码。
- **绝不在线上软链指向的 checkout 上切分支 / `checkout -- .` / `clean`**（等于替换正在运行的生产代码）；自动 PR 流程用一次性 worktree。

## 6. macOS / launchd 配方

- label `com.<user>.<name>`；环境变量写进 plist 的 `EnvironmentVariables`（launchd 不读 zshrc）；用稳定的二进制路径（不要带版本号的路径）。
- 会派生后台子进程的任务设 `AbandonProcessGroup=true`；`plutil -lint` → `launchctl bootout || true` → `bootstrap`；`launchctl list | grep <user>` 看崩溃循环（PID `-` + 非零退出）。
- **状态放 `~/Library/Application Support/<name>/`，日志放 `~/Library/Logs/<name>/`，绝不放 `~/Documents`**（TCC：「自动必挂、手动必成」）。
  后台任务需要读 `~/Documents` 里的 SOP 时，改读云端镜像。
- 状态文件不进 git（PR 流程里的 `git clean` 会清掉）。
- `install.sh` 幂等：渲染 plist 模板（`__REPO__/__HOME__` 占位符）→ lint → 重载。

## 7. 无人值守任务的 prompt 约定（定时任务 / cron 跑的 agent）

- **自包含**：每次运行都没有上文，prompt 里写全背景、已排除的原因和路径。
- **边界段**：明确不许碰什么（不改脚本、不新建产物、不杀端口、不动线上页面）。
- **数字化的成功判定**（必须出现 `refresh ok`、`✅ 追加成功`、新的 `Run done` 时间戳 > 启动时间）。
- **成功静默，失败只私信一次**（附日志尾部 ~20 行）。
- 幂等：时间戳 stamp、标题已存在检查、幂等 key；非幂等写入不重试。
- 部分失败时**留下可恢复的状态**（写到 `pending/<date>.md`），不要静默丢失。
- 一次性的定时发送：消息文本在 prompt 里固定，写明「用户已在会话里授权」；认证失败不换身份 / 不换 bot，报错即可。
- 预检：脚本是否已部署到线上目录；不在 worktree 里跑。

## 8. Bash 卫生（踩过的坑）

`$(grep -c … || true)` 再 `${V:-0}`（不要 `|| echo 0`，会得到 "0\n0"）· 不靠 `[ ! -t 0 ]` 判断 stdin，用显式开关 ·
不用 `bash -c "$(declare -f)"` 传函数 · 选最新文件用 Python 而不是 `ls -t`（别名会坏事）· `set -euo pipefail` 下的空结果管道补 `|| true`。

## 9. 部署（云函数 / 容器）

- 下载线上包 → 与 git 基线 diff → 把改动覆盖进生产布局 → 只更新代码（**绝不带环境变量参数**，模板里的占位密钥会覆盖生产）→ 校验 checksum + 干净启动日志 → 下载的包就是回滚产物。
- 容器按 image digest 部署，保留环境变量 / VPC。
- 部署前问一句：有未提交 / 未推送的改动，先 commit + push 吗？
- 部署后验证功能性指标（例如在轮询的流水线数量），不是只看「deploy finished」。
