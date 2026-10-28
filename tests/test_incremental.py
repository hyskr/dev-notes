from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_journal as fixtures
from test_journal import journal


class IncrementalTestCase(unittest.TestCase):
    setUp = fixtures.JournalTestCase.setUp
    tearDown = fixtures.JournalTestCase.tearDown
    run_journal = fixtures.JournalTestCase.run_journal

    def hook(self, session, event, **fields):
        result = self.run_journal('hook', '--source', 'codex', hook_input={
            'session_id': session, 'cwd': str(self.repo), 'hook_event_name': event, **fields,
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def force(self, session):
        result = self.run_journal('force', '--cwd', str(self.repo), '--session-id', session)
        self.assertEqual(result.returncode, 0, result.stderr)
        with patch.dict(journal.os.environ, self.env):
            return journal.load_request(json.loads(result.stdout)["request_id"])

    def edit(self, session, name):
        (self.repo / name).write_text('changed\n')
        self.hook(session, 'PostToolUse', turn_id=f'turn-{name}', tool_name='apply_patch',
                  tool_input={'command': f'*** Begin Patch\n*** Add File: {name}\n+x\n*** End Patch'},
                  tool_response='Success')

    def test_pending_and_acknowledgement_are_session_local(self):
        self.hook('a', 'SessionStart')
        self.hook('b', 'SessionStart')
        self.edit('a', 'a.py')
        self.edit('b', 'b.py')
        a, b = self.force('a'), self.force('b')
        self.assertNotEqual(a['request_id'], b['request_id'])
        self.assertEqual(self.force('a')['request_id'], a['request_id'])
        with patch.dict(journal.os.environ, self.env):
            journal.append_request(a['request_id'], '# 完成 A\n- 更新 A。')
            state = journal.load_state(a['project_key'], str(self.repo), 'repo', a['snapshot'])
        self.assertEqual(state['sessions']['codex:b']['pending_request']['request_id'], b['request_id'])
        self.assertEqual(state['sessions']['codex:b']['events'][0]['paths'], ['b.py'])
        self.assertEqual(state['sessions']['codex:a']['events'], [])

    def test_scope_preserves_events_after_request(self):
        self.hook('a', 'SessionStart')
        self.hook('a', 'UserPromptSubmit', turn_id='first', prompt='修改第一处')
        self.edit('a', 'first.py')
        first = self.force('a')
        self.hook('a', 'UserPromptSubmit', turn_id='second', prompt='修改第二处')
        self.edit('a', 'second.py')
        with patch.dict(journal.os.environ, self.env):
            journal.append_request(first['request_id'], '# 第一处完成\n- 已更新。')
        second = self.force('a')
        self.assertEqual(second['scope']['after_turn_id'], 'turn-first.py')
        self.assertEqual(second['context']['变更文件'], ['second.py'])
        self.assertEqual(second['context']['最近用户目标'], ['修改第二处'])
        self.assertGreater(second['scope']['through_event'], first['scope']['through_event'])

    def test_duplicate_daily_allows_reference_and_original_link(self):
        a = self.force('a')
        with patch.dict(journal.os.environ, self.env):
            _, first_path = journal.append_request(a['request_id'], '# 配置完成\n- 已更新。')
        b = self.force('b')
        with patch.dict(journal.os.environ, self.env):
            result = journal.save_request(b['request_id'], {
                'journal': '# 配置完成\n- 已更新。',
                'references': [{'topic': 'config', 'body': '# 配置方法\n适用于本地开发。'}],
            })
            packet = journal.load_request(b['request_id'])
        self.assertEqual(result['status'], 'duplicate')
        self.assertEqual(result['journal'], str(first_path))
        self.assertEqual(packet['canonical_request_id'], a['request_id'])
        reference = Path(result['references'][0]['path']).read_text()
        self.assertIn(f'#^j{a["request_id"]}', reference)
        self.assertNotIn(f'#^j{b["request_id"]}', reference)

    def test_reference_topic_survives_title_change_and_retry(self):
        a = self.force('a')
        with patch.dict(journal.os.environ, self.env):
            journal.append_request(a['request_id'], '# 第一次完成\n- 更新。')
            _, first = journal.append_reference(a['request_id'], '# 刷新锁\n请求共享刷新结果。', 'refresh-lock')
            self.assertEqual(journal.append_reference(a['request_id'], '# 刷新锁\n请求共享刷新结果。', 'refresh-lock')[0], 'duplicate')
        b = self.force('b')
        self.assertEqual(b['topics'][0]['topic'], 'refresh-lock')
        with patch.dict(journal.os.environ, self.env):
            journal.append_request(b['request_id'], '# 第二次完成\n- 补充失败处理。')
            _, second = journal.append_reference(b['request_id'], '# 并发刷新协议\n请求共享结果，失败后清理锁。', 'refresh-lock')
        self.assertEqual(first, second)
        self.assertIn('并发刷新协议', first.read_text())
        self.assertEqual(len(list(first.parent.glob('*.md'))), 1)

    def test_lifecycle_flush_ignores_cooldown_and_skips_empty_work(self):
        cfg = json.loads(self.config.read_text())
        cfg['thresholds']['min_seconds_between_entries'] = 7200
        cfg['fallback']['enabled'] = False
        self.config.write_text(json.dumps(cfg))
        self.hook('a', 'SessionStart')
        self.edit('a', 'a.py')
        self.assertEqual(self.hook('a', 'Stop'), {'continue': True})
        self.hook('a', 'PreCompact')
        with patch.dict(journal.os.environ, self.env):
            state = journal.load_state(journal.project_key(str(self.repo)), str(self.repo), 'repo', {})
            pending = state['sessions']['codex:a']['pending_request']['request_id']
        self.assertEqual(self.hook('a', 'Stop')['decision'], 'block')
        with patch.dict(journal.os.environ, self.env):
            journal.append_request(pending, '# 第一次成果\n- 完成 A。')
        self.edit('a', 'b.py')
        self.hook('a', 'SessionEnd')
        with patch.dict(journal.os.environ, self.env):
            state = journal.load_state(journal.project_key(str(self.repo)), str(self.repo), 'repo', {})
            pending = state['sessions']['codex:a']['pending_request']['request_id']
            packet = journal.load_request(pending)
            self.assertEqual(packet['context']['变更文件'], ['b.py'])
            journal.skip_request(pending)
            session = state['sessions']['codex:a']
            session['events'] = []
            session['baseline_snapshot'] = packet['snapshot']
            self.assertEqual(journal.gate_reasons(state, session, packet['snapshot'], cfg, 'session_end'), [])

    def test_tools_use_canonical_payloads_and_explicit_exit_codes(self):
        session = {'events': [], 'event_seq': 0, 'current_turn_id': 'turn-1'}
        with patch.object(journal, 'repo_snapshot', side_effect=AssertionError('Unexpected Git scan')):
            journal.observe_tool(session, {'tool_name': 'Bash', 'tool_input': {'command': 'pytest'},
                                 'tool_response': 'Process exited with code 0\n0 failed'}, journal.DEFAULT_CONFIG, str(self.repo))
            journal.observe_tool(session, {'tool_name': 'exec_command', 'tool_input': {'cmd': 'npm test'},
                                 'tool_response': {'exit_code': 2}}, journal.DEFAULT_CONFIG, str(self.repo))
            journal.observe_tool(session, {'tool_name': 'apply_patch', 'tool_input': '*** Begin Patch\n*** Update File: auth.py\n@@\n-x\n+y\n*** End Patch',
                                 'tool_response': 'Success'}, journal.DEFAULT_CONFIG, str(self.repo))
        self.assertTrue(session['events'][0]['validation'].startswith('完成:'))
        self.assertTrue(session['events'][1]['validation'].startswith('失败:'))
        self.assertEqual(session['events'][2]['paths'], ['auth.py'])
        self.assertEqual(journal.session_metrics(session)['mutations'], 1)

    def test_same_dirty_file_edit_is_visible_without_content_hashes(self):
        target = self.repo / 'README.md'
        target.write_text('first\n')
        baseline = journal.repo_snapshot(str(self.repo))
        target.write_text('second\n')
        changed = journal.repo_snapshot(str(self.repo))
        self.assertEqual(baseline['status'], changed['status'])
        self.assertEqual(baseline['diff_lines'], changed['diff_lines'])
        self.assertNotEqual(baseline, changed)

    def test_cooldown_stop_does_not_scan_git(self):
        cfg = json.loads(self.config.read_text())
        cfg['thresholds']['min_seconds_between_entries'] = 7200
        self.config.write_text(json.dumps(cfg))
        self.hook('a', 'SessionStart')
        data = {'session_id': 'a', 'cwd': str(self.repo), 'hook_event_name': 'Stop'}
        with patch.dict(journal.os.environ, self.env), patch.object(journal, 'read_hook_input', return_value=data), \
                patch.object(journal, 'run_quiet', side_effect=AssertionError('Unexpected Git call')), \
                contextlib.redirect_stdout(io.StringIO()):
            journal.handle_hook('codex')

    def test_scrubbing_preserves_examples_and_removes_complete_private_key(self):
        sample = 'http://127.0.0.1:8000 user@example.com 192.0.2.1\n-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATE_PAYLOAD\n-----END OPENSSH PRIVATE KEY-----'
        result = journal.scrub_text(sample, journal.DEFAULT_CONFIG)
        self.assertIn('127.0.0.1:8000', result)
        self.assertIn('user@example.com', result)
        self.assertIn('192.0.2.1', result)
        self.assertNotIn('PRIVATE_PAYLOAD', result)
        self.assertNotIn('END OPENSSH', result)
        self.assertIn('[PRIVATE_KEY_REDACTED]', result)

    def test_empty_sections_are_omitted(self):
        title, body = journal.normalize_body('# 标题\n## 完成事项\n- 完成。\n## 验证\n- 无\n## 下一步\n无', journal.DEFAULT_CONFIG)
        self.assertEqual(title, '标题')
        self.assertNotIn('验证', body)
        self.assertNotIn('下一步', body)

    def test_skip_acknowledges_only_its_scope(self):
        self.hook('a', 'SessionStart')
        self.edit('a', 'a.py')
        packet = self.force('a')
        with patch.dict(journal.os.environ, self.env):
            journal.skip_request(packet['request_id'])
            state = journal.load_state(packet['project_key'], str(self.repo), 'repo', {})
            self.assertEqual(journal.load_request(packet['request_id'])['status'], 'skipped')
        self.assertIsNone(state['sessions']['codex:a']['pending_request'])
        self.assertEqual(list(self.vault.rglob('*.md')), [])

    def test_prompt_example_roundtrips_markdown(self):
        packet = self.force('a')
        prompt = journal.continuation_prompt(packet)
        payload = json.loads(next(line for line in prompt.splitlines() if line.startswith('{"journal"')))
        self.assertIn('\n', payload['journal'])

    def test_request_respects_reference_configuration(self):
        cfg = json.loads(self.config.read_text())
        cfg['references']['automatic'] = False
        self.config.write_text(json.dumps(cfg))
        packet = self.force('a')
        self.assertFalse(packet['references']['automatic'])


if __name__ == '__main__':
    unittest.main()
