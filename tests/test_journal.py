from __future__ import annotations

import importlib.util
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = PLUGIN_ROOT / "scripts" / "journal.py"
MANAGER_PATH = PLUGIN_ROOT / "scripts" / "manage_install.py"

SPEC = importlib.util.spec_from_file_location("ai_dev_journal", JOURNAL_PATH)
journal = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = journal
SPEC.loader.exec_module(journal)


class JournalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("initial\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.repo, check=True)

        self.vault = self.base / "vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        self.config = self.base / "config.json"
        cfg = journal.deep_merge(journal.DEFAULT_CONFIG, {
            "vault_path": str(self.vault),
            "thresholds": {
                "min_seconds_between_entries": 0,
                "dirty_turns": 99,
                "changed_files": 1,
                "diff_lines": 9999,
                "tool_calls": 9999,
                "min_activity_events": 1,
                "prompt_retry_seconds": 0,
            },
        })
        self.config.write_text(json.dumps(cfg), encoding="utf-8")
        self.env = os.environ.copy()
        self.env[journal.CONFIG_ENV] = str(self.config)
        self.env[journal.STATE_ENV] = str(self.base / "state")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_journal(self, *args: str, hook_input: dict | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(JOURNAL_PATH), *args],
            cwd=self.repo,
            env=self.env,
            input=json.dumps(hook_input) if hook_input is not None else None,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_secret_and_home_path_scrubbing(self) -> None:
        cfg = journal.deep_merge(journal.DEFAULT_CONFIG, {})
        sample = f"{Path.home()}/app key sk-ant-{'a' * 24} Authorization: Bearer secret"
        result = journal.scrub_text(sample, cfg)
        self.assertNotIn(str(Path.home()), result)
        self.assertNotIn("sk-ant-", result)
        self.assertNotIn("Bearer secret", result)
        self.assertIn("[ANTHROPIC_KEY_REDACTED]", result)

    def test_gate_requests_current_agent_then_appends_daily_note(self) -> None:
        common = {"session_id": "session-1", "cwd": str(self.repo)}
        start = self.run_journal(
            "hook", "--source", "codex",
            hook_input={**common, "hook_event_name": "SessionStart", "source": "startup"},
        )
        self.assertEqual(start.returncode, 0, start.stderr)

        prompt_event = self.run_journal(
            "hook", "--source", "codex",
            hook_input={
                **common,
                "hook_event_name": "UserPromptSubmit",
                "turn_id": "turn-1",
                "prompt": "实现登录并补充验证",
            },
        )
        self.assertEqual(prompt_event.returncode, 0, prompt_event.stderr)

        (self.repo / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
        tool_event = self.run_journal(
            "hook", "--source", "codex",
            hook_input={
                **common,
                "hook_event_name": "PostToolUse",
                "turn_id": "turn-1",
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "auth.py")},
                "tool_response": {"status": "ok"},
            },
        )
        self.assertEqual(tool_event.returncode, 0, tool_event.stderr)

        stop = self.run_journal(
            "hook", "--source", "codex",
            hook_input={
                **common,
                "hook_event_name": "Stop",
                "turn_id": "turn-1",
                "stop_hook_active": False,
                "last_assistant_message": "登录逻辑已经实现，测试通过。",
            },
        )
        self.assertEqual(stop.returncode, 0, stop.stderr)
        decision = json.loads(stop.stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("不读取 Obsidian", decision["reason"])
        match = re.search(r"--request-id\s+([a-f0-9]{20})", decision["reason"])
        self.assertIsNotNone(match)
        request_id = match.group(1)

        body = """# 完成登录验证
## 完成事项
- 实现登录逻辑。
## 关键决策
- 保持接口最小化。
## 问题与修复
- 修复返回值错误。
## 修改范围
- `auth.py`
## 验证
- 单元测试通过。
## 下一步
- [ ] 补充异常分支。
"""
        appended = subprocess.run(
            [sys.executable, str(JOURNAL_PATH), "append", "--request-id", request_id],
            cwd=self.repo,
            env=self.env,
            input=body,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)
        self.assertIn("written:", appended.stdout)

        now = dt.datetime.now()
        daily_file = (
            self.vault / "Dev Notes" / now.strftime("%Y-%m") / f"{now:%Y-%m-%d}.md"
        )
        self.assertTrue(daily_file.exists())
        note = daily_file.read_text(encoding="utf-8")
        self.assertIn("type: ai-development-journal", note)
        self.assertIn(f"date: {now:%Y-%m-%d}", note)
        self.assertIn(f"# {now:%Y-%m-%d} 开发日志", note)
        self.assertRegex(note, r"## \d{2}:\d{2} · repo — 完成登录验证")
        self.assertIn("#dev-journal/project/repo", note)
        self.assertIn("#dev-journal/source/codex", note)
        self.assertIn("#dev-journal/daily", note)
        self.assertIn("## 完成事项", body)
        self.assertIn("### 完成事项", note)
        self.assertIn(f"dev-notes:{request_id}", note)
        self.assertNotIn("USER:", note)

        duplicate = subprocess.run(
            [sys.executable, str(JOURNAL_PATH), "append", "--request-id", request_id],
            cwd=self.repo,
            env=self.env,
            input=body,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(duplicate.returncode, 0)
        self.assertIn("duplicate:", duplicate.stdout)
        self.assertEqual(daily_file.read_text(encoding="utf-8").count(request_id), 2)

    def test_date_first_daily_file_groups_multiple_projects(self) -> None:
        second_repo = self.base / "second-project"
        second_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=second_repo, check=True)

        body = """# Checkpoint
## 完成事项
- Done.
## 关键决策
- 无
## 问题与修复
- 无
## 修改范围
- One file.
## 验证
- Passed.
## 下一步
- 无
"""

        requests = []
        for source, cwd in (("codex", self.repo), ("claude", second_repo)):
            forced = subprocess.run(
                [sys.executable, str(JOURNAL_PATH), "force", "--source", source, "--cwd", str(cwd)],
                cwd=cwd,
                env=self.env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(forced.returncode, 0, forced.stderr)
            request_id = json.loads(forced.stdout)["request_id"]
            requests.append(request_id)
            appended = subprocess.run(
                [sys.executable, str(JOURNAL_PATH), "append", "--request-id", request_id],
                cwd=cwd,
                env=self.env,
                input=body,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(appended.returncode, 0, appended.stderr)

        now = dt.datetime.now()
        month_dir = self.vault / "Dev Notes" / now.strftime("%Y-%m")
        daily_files = list(month_dir.glob("*.md"))
        self.assertEqual(len(daily_files), 1)
        self.assertEqual(daily_files[0].name, f"{now:%Y-%m-%d}.md")
        note = daily_files[0].read_text(encoding="utf-8")
        self.assertEqual(note.count(f"# {now:%Y-%m-%d} 开发日志"), 1)
        self.assertIn("#dev-journal/project/repo", note)
        self.assertIn("#dev-journal/project/second-project", note)
        self.assertIn("#dev-journal/source/codex", note)
        self.assertIn("#dev-journal/source/claude-code", note)
        for request_id in requests:
            self.assertEqual(note.count(request_id), 2)

    def test_stop_hook_does_not_loop_when_already_active(self) -> None:
        common = {"session_id": "loop", "cwd": str(self.repo)}
        self.run_journal(
            "hook", "--source", "codex",
            hook_input={**common, "hook_event_name": "SessionStart"},
        )
        (self.repo / "changed.txt").write_text("x", encoding="utf-8")
        self.run_journal(
            "hook", "--source", "codex",
            hook_input={
                **common,
                "hook_event_name": "PostToolUse",
                "turn_id": "turn-loop",
                "tool_name": "Write",
                "tool_input": {"file_path": str(self.repo / "changed.txt")},
            },
        )
        stop = self.run_journal(
            "hook", "--source", "codex",
            hook_input={**common, "hook_event_name": "Stop", "stop_hook_active": True},
        )
        self.assertEqual(json.loads(stop.stdout), {"continue": True})

    def test_manual_force_creates_appendable_request(self) -> None:
        forced = self.run_journal("force", "--source", "codex", "--cwd", str(self.repo))
        self.assertEqual(forced.returncode, 0, forced.stderr)
        packet = json.loads(forced.stdout)
        self.assertEqual(packet["reasons"], ["manual"])
        self.assertTrue((self.base / "state" / "requests" / f"{packet['request_id']}.json").exists())

    def test_reference_is_append_only_and_links_to_daily_checkpoint(self) -> None:
        forced = self.run_journal("force", "--source", "codex", "--cwd", str(self.repo))
        self.assertEqual(forced.returncode, 0, forced.stderr)
        request_id = json.loads(forced.stdout)["request_id"]
        checkpoint = """# Refresh lock implemented
## 完成事项
- Added a refresh lock.
## 关键决策
- Serialize refresh operations.
## 问题与修复
- Prevented duplicate refresh requests.
## 修改范围
- auth.py
## 验证
- Tests passed.
## 下一步
- 无
"""
        appended = subprocess.run(
            [sys.executable, str(JOURNAL_PATH), "append", "--request-id", request_id],
            cwd=self.repo,
            env=self.env,
            input=checkpoint,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(appended.returncode, 0, appended.stderr)

        reference_body = """# Refresh lock
## 适用范围
- Multiple requests can observe an expired token concurrently.
## 方法
- Serialize refresh operations and share the result.
"""
        reference = subprocess.run(
            [sys.executable, str(JOURNAL_PATH), "reference", "--request-id", request_id],
            cwd=self.repo,
            env=self.env,
            input=reference_body,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(reference.returncode, 0, reference.stderr)
        reference_path = self.vault / "Dev Notes" / "References" / "repo" / "refresh-lock.md"
        self.assertTrue(reference_path.exists())
        note = reference_path.read_text(encoding="utf-8")
        self.assertIn("type: reference-note", note)
        self.assertIn("dev-journal/reference", note)
        self.assertIn("dev-journal/project/repo", note)
        self.assertIn(f"#^j{request_id}", note)
        self.assertIn("### 适用范围", note)

        now = dt.datetime.now()
        daily_path = self.vault / "Dev Notes" / now.strftime("%Y-%m") / f"{now:%Y-%m-%d}.md"
        self.assertIn(f"^j{request_id}", daily_path.read_text(encoding="utf-8"))

        duplicate = subprocess.run(
            [sys.executable, str(JOURNAL_PATH), "reference", "--request-id", request_id],
            cwd=self.repo,
            env=self.env,
            input=reference_body,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
        self.assertIn("duplicate:", duplicate.stdout)
        self.assertEqual(reference_path.read_text(encoding="utf-8").count(request_id), 2)

    def test_normalizer_keeps_only_sections_with_content(self) -> None:
        title, body = journal.normalize_body("# Small change\n## 完成\n- Done", journal.DEFAULT_CONFIG)
        self.assertEqual(title, "Small change")
        self.assertIn("#### 完成事项", body)
        self.assertNotIn("关键决策", body)
        self.assertNotIn("- 无", body)

    def test_project_first_remains_available_as_legacy_layout(self) -> None:
        cfg = journal.deep_merge(journal.DEFAULT_CONFIG, {
            "vault_path": str(self.vault),
            "layout": "project_first",
            "file_mode": "monthly",
        })
        when = dt.datetime(2026, 8, 11, 12, 0).astimezone()
        path = journal.output_path({"project_name": "legacy-project"}, cfg, when)
        self.assertEqual(path, self.vault / "Dev Notes" / "legacy-project" / "2026-08.md")


class InstallerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir(parents=True)
        self.vault = self.home / "Documents" / "Vault"
        (self.vault / ".obsidian").mkdir(parents=True)
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(
            json.dumps({
                "env": {"KEEP_ME": "yes"},
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "/existing/hook"}]}]
                },
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_manager(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(MANAGER_PATH), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_install_is_idempotent_and_preserves_existing_hooks(self) -> None:
        args = (
            "install",
            "--source", str(PLUGIN_ROOT),
            "--home", str(self.home),
            "--vault", str(self.vault),
        )
        first = self.run_manager(*args)
        second = self.run_manager(*args)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_result = json.loads(first.stdout)
        self.assertTrue(
            Path(first_result["backup"]).resolve().is_relative_to(
                (self.vault / "bak" / "Dev Notes").resolve()
            )
        )

        settings = json.loads((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["env"]["KEEP_ME"], "yes")
        stop_commands = [
            handler["command"]
            for group in settings["hooks"]["Stop"]
            for handler in group.get("hooks", [])
        ]
        self.assertIn("/existing/hook", stop_commands)
        self.assertEqual(sum("ai-development-journal" in command for command in stop_commands), 1)

        marketplace = json.loads((self.home / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        ours = [entry for entry in marketplace["plugins"] if entry.get("name") == "ai-development-journal"]
        self.assertEqual(len(ours), 1)
        self.assertEqual(ours[0]["policy"]["installation"], "AVAILABLE")
        self.assertTrue((self.home / "plugins" / "ai-development-journal" / "scripts" / "journal.py").exists())

    def test_install_migrates_version_one_layout_without_resetting_thresholds(self) -> None:
        config_path = self.home / ".config" / "ai-development-journal" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps({
            "version": 1,
            "vault_path": str(self.vault),
            "layout": "project_first",
            "file_mode": "monthly",
            "metadata": {"project_tag_prefix": "project"},
            "thresholds": {"changed_files": 9},
        }), encoding="utf-8")

        installed = self.run_manager(
            "install",
            "--source", str(PLUGIN_ROOT),
            "--home", str(self.home),
            "--vault", str(self.vault),
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["version"], 2)
        self.assertEqual(config["layout"], "date_first")
        self.assertEqual(config["file_mode"], "daily")
        self.assertTrue(config["month_subdirectories"])
        self.assertFalse(config["year_subdirectories"])
        self.assertEqual(config["metadata"]["tag_root"], "dev-journal")
        self.assertNotIn("project_tag_prefix", config["metadata"])
        self.assertEqual(config["thresholds"]["changed_files"], 9)

    def test_uninstall_removes_only_our_hooks_and_never_notes(self) -> None:
        installed = self.run_manager(
            "install",
            "--source", str(PLUGIN_ROOT),
            "--home", str(self.home),
            "--vault", str(self.vault),
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        note = self.vault / "Dev Notes" / "demo" / "note.md"
        note.parent.mkdir(parents=True)
        note.write_text("keep", encoding="utf-8")

        removed = self.run_manager("uninstall", "--home", str(self.home))
        self.assertEqual(removed.returncode, 0, removed.stderr)
        settings = json.loads((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        stop_commands = [
            handler["command"]
            for group in settings["hooks"]["Stop"]
            for handler in group.get("hooks", [])
        ]
        self.assertEqual(stop_commands, ["/existing/hook"])
        self.assertTrue(note.exists())
        self.assertFalse((self.home / "plugins" / "ai-development-journal").exists())


if __name__ == "__main__":
    unittest.main()
