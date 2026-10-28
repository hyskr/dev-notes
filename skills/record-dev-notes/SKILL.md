---
name: record-dev-notes
description: Record the current development session and its reusable conclusions as integrated Dev Notes in Obsidian. Use when the user asks to journal development work or preserve current development knowledge; ordinary file saves do not activate this skill.
---

# Dev Notes｜开发手记

记录当前工作的新增成果，同时沉淀有长期复用价值的知识。Obsidian 只作为输出端，不读取既有笔记正文。

## 记录

1. 创建或复用当前会话的请求：

```bash
python3 "$HOME/plugins/ai-development-journal/scripts/journal.py" force --source codex --cwd "$PWD"
```

Codex 中命令会使用 `CODEX_THREAD_ID` 识别当前会话。在其他环境中传入对应的 `--source` 和 `--session-id`。返回的数据包含本次 `scope`、采集线索、Reference 配置和已有主题标识。

2. 根据当前对话与请求数据提炼新增成果。按 `scope.since`、`after_turn_id` 和已提供的工作线索确定范围；既有结果只在解释新决策确有需要时简述。

日记使用具体的一级标题和简洁结果条目，通常 3–6 条。关键决策写清取舍；验证写已执行的检查和结果。只有存在内容时才增加栏目，省略“无”、空栏目和内部触发过程。

3. 有稳定、可复用的结论时，在同一次记录中生成知识条目。遵循 `references.enabled` 和 `references.automatic`；用户明确要求保存知识时不受 automatic 限制。自动选择最多一篇，用户明确要求时最多两篇。

知识条目写明适用范围、完整结论、必要步骤和适用版本。标题可以调整，`topic` 保持稳定；同主题复用请求中提供的标识。已有主题追加一个可独立阅读的新版本，不读取旧正文。

4. 一次调用保存日记和知识条目。JSON 字符串中的换行用 `\n` 表示：

```bash
python3 "$HOME/plugins/ai-development-journal/scripts/journal.py" save --request-id "<request_id>" <<'DEV_NOTES'
{"journal":"# 具体成果标题\n- 完成的成果。","references":[{"topic":"稳定主题标识","body":"# 知识标题\n适用范围、完整结论与必要步骤。"}]}
DEV_NOTES
```

没有知识条目时使用 `"references": []`。没有值得保存的新增成果时，调用 `skip --request-id "<request_id>"` 推进记录范围。

5. 根据命令返回值报告保存路径。重复提交复用原记录，可以继续保存知识条目。
