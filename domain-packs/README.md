# 领域 pack 规范

框架（core / playbooks / skills / adapters）**不含任何公司、产品、内部工具、人名、客户的内容**。
这些都放在框架之外的「领域 pack」里，安装时挂到 `~/.agents/packs/<name>/`。换工作 / 换领域 = 换 pack，框架不动。

## 一个 pack 的结构

```
<name>-pack/
  README.md        必需。按 _template/README.md 的章节写
  leak-terms.txt   必需。每行一个只属于这个领域的词（公司名、内部系统名、专有表名…），
                   build.sh 用它检查框架里有没有混进领域内容
  hooks/           可选。需要在某些场景强制注入的纪律（Claude 用 hook；Codex 在 README 里写成段落）
  research/        可选。这个领域的素材、抽取报告（不随框架分发）
```

pack 的 skill 和知识库**不必**放进 pack 目录 —— README 里写指针即可（skill 真源在 `~/.agents/skills/`，知识库在它自己的位置）。

## 安装

```bash
scripts/install.sh --pack /path/to/<name>-pack
```

## 判断一条内容该放框架还是 pack

- 换一家公司还成立吗？成立 → 框架；不成立 → pack。
- 里面有专有名词（系统、表、人、客户、内部工具、具体频道）吗？有 → pack，或者在框架里换成通用说法。
- 是方法（怎么查、怎么验收、怎么分级）还是事实（这个系统在哪、谁负责）？方法 → 框架；事实 → pack。
