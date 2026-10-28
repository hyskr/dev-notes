# Dev Notes｜开发手记

记录开发进展，沉淀可复用结论。

Dev Notes 将 Codex 与 Claude Code 的当前工作整理到 Obsidian：每天一篇开发手记，稳定知识按主题保存为 Reference。一次记录同时处理两种内容。

- 日记围绕成果、决策和验证组织，省略空栏目。
- 普通自动记录默认至少间隔 **2 小时**，达到活动阈值后才请当前 agent 总结。
- 工具事件只收集少量本地线索；冷却期内的普通 Stop 不扫描 Git、不调用模型。
- 压缩前暂存未记录增量，会话结束时保存剩余工作。
- 多个会话各自维护请求和记录范围，同一天的输出汇总到每日文件。
- 日记和 Reference 通过一次保存调用写入。
- Obsidian 只作为输出端；不读取既有笔记正文，不将笔记注入上下文。

## 安装

需要 macOS 或 Linux、Python 3.10+、Obsidian vault，以及 Codex CLI / Claude Code 中至少一个。

```bash
git clone https://github.com/hyskr/dev-notes.git
cd dev-notes
./install.sh --vault "/path/to/Obsidian Vault"
```

只有一个常见位置的 vault 时，可以省略 `--vault`。

安装器保留已有配置和其他 hooks，备份后更新程序文件。Codex 插件 ID 为 `ai-development-journal`，界面显示为 Dev Notes。安装路径：

```text
~/plugins/ai-development-journal/
~/.config/ai-development-journal/config.json
~/.local/state/ai-development-journal/
```

新开一个 Codex chat，运行 `/hooks`，审阅并信任当前 hook 定义。插件更新后的 hook 定义也需要按 Codex 提示确认。Claude Code hooks 由安装器合并到全局 settings。

升级会保留自定义阈值；需要降低已有安装的频率时，按下方配置调整。

## 使用

可以说“记录这次开发手记”，或使用 `$record-dev-notes`。记录内容和有价值的知识结论一起保存。

日记示例：

```markdown
## 16:20 · my-app — 登录刷新流程完成

- 增加 token 自动续期，并让并发请求共享刷新结果。
- 使用刷新锁，避免同一时刻重复请求认证服务。

### 验证
- 登录与并发刷新测试通过。

### 下一步
- 补充多设备登录场景。
```

默认目录：

```text
Obsidian Vault/
└── Dev Notes/
    ├── 2026-10/
    │   └── 2026-10-28.md
    └── References/
        └── my-app/
            └── refresh-lock.md
```

既有安装继续使用配置中的日志目录。项目、来源和每日分类通过嵌套标签检索。

## 自动记录与开销

普通 Stop 同时满足以下两类条件时才触发：

1. 距离本次会话开始或上次记录至少 2 小时；
2. 达到一项活动阈值：10 个修改轮次、15 个涉及文件、600 行差异增量、50 次工具调用且有修改、新 commit，或已有增量积累 4 小时。

压缩与结束事件不受普通冷却间隔限制，只处理未记录增量。压缩前先保存请求包，后续 Stop 交由当前 agent 提炼；结束时使用后台兜底路径。

没有新增成果时 agent 可以跳过本次写入并推进已记录范围。自动摘要只携带最近目标、修改、验证线索和当前结果，不回放整段对话；自动生成的 Reference 最多一篇。

配置中的默认值：

```json
{
  "thresholds": {
    "min_seconds_between_entries": 7200,
    "dirty_turns": 10,
    "changed_files": 15,
    "diff_lines": 600,
    "tool_calls": 50,
    "max_checkpoint_age_minutes": 240,
    "commit": true,
    "pre_compact": true,
    "session_end": true,
    "prompt_retry_seconds": 300
  }
}
```

完整配置见 [config.example.json](config.example.json)。手动记录随时可用。

## Reference 主题

Reference 只保存有长期复用价值的配置方法、协议、架构结论或已验证的排障步骤。日常状态和 TODO 留在日记中。

每个主题有稳定的 `topic` 标识，显示标题可以变化。当前请求会列出近期主题供复用；也可以通过 `topics` 命令查看全部主题元数据。新版本追加到同一文件，写明完整结论、适用条件和版本，来源链接指向每日记录。

主题目录和去重状态保存在 vault 外。已有 Reference 可通过原有输出状态中的路径识别，无需读取笔记正文。

```json
{
  "references": {
    "enabled": true,
    "automatic": true,
    "subdirectory": "References",
    "tag_segment": "reference",
    "max_body_chars": 20000
  }
}
```

`automatic: false` 时，只在用户明确要求保存知识时生成 Reference。`enabled: false` 关闭 Reference 输出。

## 命令行

创建或复用当前会话的请求：

```bash
python3 ~/plugins/ai-development-journal/scripts/journal.py force --source codex --cwd "$PWD"
```

Codex 中自动使用 `CODEX_THREAD_ID`。其他环境可传入 `--session-id`；多个活动会话需要明确选择当前会话。

一次保存日记和知识条目：

```bash
python3 ~/plugins/ai-development-journal/scripts/journal.py save --request-id REQUEST_ID <<'DEV_NOTES'
{"journal":"# 完成刷新锁\n- 并发请求共享刷新结果。","references":[{"topic":"refresh-lock","body":"# 刷新锁\n适用于多个请求同时发现 token 失效的场景。\n\n使用单个共享刷新任务，并在成功或失败后清理状态。"}]}
DEV_NOTES
```

无知识条目时使用 `"references": []`。重复提交返回原路径，允许继续添加 Reference。

```bash
python3 ~/plugins/ai-development-journal/scripts/journal.py skip --request-id REQUEST_ID
python3 ~/plugins/ai-development-journal/scripts/journal.py topics --cwd "$PWD"
python3 ~/plugins/ai-development-journal/scripts/journal.py status --cwd "$PWD"
python3 ~/plugins/ai-development-journal/scripts/journal.py doctor
```

单独的 `append` 与 `reference --topic TOPIC` 命令也可使用。

## 数据处理

写入前替换已识别的凭据、Authorization 内容、数据库密码和完整 PEM 私钥块；用户主目录缩写为 `~`。普通 IP 和邮箱保留，以便开发配置和示例可以复用。

每日笔记与 Reference 仅判断是否存在并追加内容。记录范围、请求状态、去重内容和主题目录均保存在本地 sidecar 中，不读取 Obsidian 笔记正文。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

覆盖自动记录间隔、压缩与结束时的增量保存、会话隔离、记录范围、工具采集、去重后的 Reference、主题复用、脱敏，以及安装和卸载。

## 卸载

```bash
~/plugins/ai-development-journal/uninstall.sh
```

卸载保留 Obsidian 笔记、其他 hooks、配置与状态。需要同时删除本工具配置和状态时使用 `--purge-state`。安装备份保存在配置的 `backup_subdirectory` 下。

## 来源与许可证

MIT License。部分设计与实现来自 [journal-recorder-agent](https://github.com/GauravRatnawat/journal-recorder-agent) 和 [obsidian-daily-digest](https://github.com/brianruggieri/obsidian-daily-digest)，版权说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

Hook 接口参考 [Codex Hooks](https://learn.chatgpt.com/docs/hooks) 与 [Claude Code Hooks](https://code.claude.com/docs/en/hooks)。
