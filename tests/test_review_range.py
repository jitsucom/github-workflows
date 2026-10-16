"""Regression checks for coverage selection and artifact-only execution."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_review_context import run_block, WORKFLOW


def load_range(root):
    script = run_block('📐 Select review range').split("<<'PYTHON_EOF'\n", 1)[1].rsplit('\nPYTHON_EOF', 1)[0]
    path = root / 'select_range.py'
    path.write_text(script)
    spec = importlib.util.spec_from_file_location('select_range', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReviewRangeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = load_range(self.root)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.base = self.commit('base.txt')
        self.previous = self.commit('previous.txt')
        self.head = self.commit('fixup.txt')
        self.bot = 'review-bot[bot]'
        self.reviews = []
        self.env = dict(REVIEW_MODE='pr', COMPARE_HEAD=self.head, FULL_BASE=self.base,
                        REVIEW_MODEL='gpt-5.6-terra', REVIEW_EFFORT='high', CODEX_VERSION='0.149.0',
                        REVIEW_BOT_LOGIN=self.bot, INCREMENTAL='true', REPO='owner/repo',
                        PR_NUMBER='7', GITHUB_OUTPUT=str(self.root / 'outputs'))
        self.ctx = self.root / '.github/codex/context'
        self.ctx.mkdir(parents=True)
        (self.root / '.github/codex/output').mkdir()
        (self.root / '.github/workflows').mkdir()
        (self.root / '.github/workflows/ai-review.prompt.md').write_text('Trusted review prompt')
        (self.ctx / 'pr.json').write_text(json.dumps(dict(title='Fix', body='Intent', headRefOid=self.head)))
        (self.ctx / 'additional-instructions.txt').write_text('Focus')
        (self.ctx / 'review-target.txt').write_text('compare_base: ' + self.base + '\n')
        self.original_check_output = subprocess.check_output

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, name):
        (self.root / name).write_text(name)
        self.git('add', name)
        self.git('commit', '-m', name)
        return self.git('rev-parse', 'HEAD')

    def gh(self, args, **kwargs):
        if args[0] == 'gh':
            self.assertIn('--paginate', args)
            return json.dumps([self.reviews[:1], self.reviews[1:]])
        return self.original_check_output(args, **kwargs)

    def select(self):
        old = os.getcwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, self.env), patch.object(self.module.subprocess, 'check_output', self.gh):
                self.module.main()
        finally:
            os.chdir(old)
        return json.loads((self.ctx / 'range.json').read_text())

    def coverage(self, info, **changes):
        values = dict(version=1, base=self.base, head=self.previous, profile=info['review_profile'])
        values.update(changes)
        return dict(user={'login': self.bot}, state='APPROVED', commit_id=values['head'],
                    body='<!-- ai-review-coverage:' + json.dumps(values) + ' -->')

    def test_first_review_full_then_fixup_incremental(self):
        info = self.select()
        self.assertEqual(info['range_kind'], 'full')
        self.reviews = [self.coverage(info)]
        info = self.select()
        self.assertEqual(info['compare_base'], self.previous)
        self.assertEqual(info['range_kind'], 'incremental')
        self.assertEqual((self.root / '.github/codex/output/diff-files.txt').read_text(), 'fixup.txt\n')
        self.assertEqual(json.loads((self.root / '.github/codex/output/range.json').read_text()), info)

    def test_dismissed_completed_review_remains_coverage(self):
        info = self.select()
        # GitHub dismisses a stale approval on push, preserving its completion marker.
        self.reviews = [self.coverage(info, head=self.base),
                        dict(self.coverage(info), state='DISMISSED')]
        info = self.select()
        self.assertEqual(info['range_kind'], 'incremental')
        self.assertEqual(info['compare_base'], self.previous)
        self.assertEqual((self.root / '.github/codex/output/diff-files.txt').read_text(), 'fixup.txt\n')

    def test_force_push_and_changed_base_fall_back_to_full(self):
        info = self.select()
        self.git('checkout', '-b', 'rewritten', self.base)
        rewritten = self.commit('rewritten.txt')
        for state in ('APPROVED', 'DISMISSED'):
            for changes in (dict(head=rewritten), dict(base=rewritten), dict(head=self.head)):
                with self.subTest(state=state, changes=changes):
                    self.reviews = [dict(self.coverage(info, **changes), state=state)]
                    self.assertEqual(self.select()['range_kind'], 'full')

    def test_settings_prompt_and_intent_changes_require_full_review(self):
        info = self.select()
        self.reviews = [self.coverage(info)]
        for key, value in [('REVIEW_MODEL', 'gpt-6-astra'), ('REVIEW_EFFORT', 'medium'), ('CODEX_VERSION', '0.159.1')]:
            with self.subTest(key=key):
                old = self.env[key]
                self.env[key] = value
                self.assertEqual(self.select()['range_kind'], 'full')
                self.env[key] = old
        (self.ctx / 'pr.json').write_text(json.dumps(dict(title='Fix', body='Changed intent', headRefOid=self.head)))
        self.assertEqual(self.select()['range_kind'], 'full')

    def test_review_without_trusted_matching_coverage_is_not_progress(self):
        info = self.select()
        for state in ('APPROVED', 'DISMISSED'):
            cases = [dict(user={'login': self.bot}, state=state, commit_id=self.previous, body='Looks good'),
                     dict(self.coverage(info), state=state, user={'login': 'someone-else'}),
                     dict(self.coverage(info), state=state, commit_id=self.head),
                     dict(self.coverage(info, profile='different-settings'), state=state),
                     dict(self.coverage(info, version=2), state=state)]
            for review in cases:
                with self.subTest(state=state, review=review):
                    self.reviews = [review]
                    self.assertEqual(self.select()['range_kind'], 'full')
        for state in ('PENDING', 'COMMENTED', 'CHANGES_REQUESTED'):
            with self.subTest(state=state):
                self.reviews = [dict(self.coverage(info), state=state)]
                self.assertEqual(self.select()['range_kind'], 'full')

    def test_explicit_full_and_commit_mode_ignore_coverage(self):
        info = self.select()
        self.reviews = [self.coverage(info)]
        self.env['INCREMENTAL'] = 'false'
        self.assertEqual(self.select()['range_kind'], 'full')
        self.env.update(INCREMENTAL='true', REVIEW_MODE='commit')
        self.assertEqual(self.select()['range_kind'], 'full')

    def test_history_api_failure_does_not_silently_advance(self):
        old = os.getcwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, self.env), patch.object(self.module.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, ['gh'])), self.assertRaises(subprocess.CalledProcessError):
                self.module.main()
        finally:
            os.chdir(old)

    def test_native_benchmark_command_and_clean_tree(self):
        home = self.root / '.github/codex/home'
        home.mkdir()
        (home / 'config.toml').write_text('[model_providers.test]\nbase_url="http://localhost:1234"\n')
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'codex').write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GITHUB_WORKSPACE/native-args.txt"\nprintf \'{"type":"turn.completed"}\\n\'\n')
        (bin_dir / 'codex').chmod(0o755)
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                   CODEX_HOME=str(self.root / 'native-home'), RUNNER_TEMP=str(self.root / 'runner'),
                   GITHUB_WORKSPACE=str(self.root), REVIEW_MODE='pr', REVIEW_BASE=self.base,
                   REVIEW_HEAD=self.head, REVIEW_MODEL='gpt-5.6-terra', REVIEW_EFFORT='high')
        subprocess.run(['bash', '-e', '-c', run_block('🔬 Benchmark native review (artifact only)')],
                       cwd=self.root, env=env, capture_output=True, check=True)
        args = (self.root / 'native-args.txt').read_text().splitlines()
        self.assertEqual(args[-3:], ['review', '--base', self.base])
        self.assertIn('model_reasoning_effort="high"', args)
        self.assertEqual(self.git('-C', str(self.root / 'runner/native-review'), 'rev-parse', 'HEAD'), self.head)
        self.assertTrue((self.root / '.github/codex/output/native-events.jsonl').exists())

    def test_execution_settings_are_pinned_and_bounded(self):
        for changes, valid in [({}, True), ({'CODEX_VERSION':'latest'}, False),
                               ({'TIMEOUT_MINUTES':'0'}, False), ({'TIMEOUT_MINUTES':'31'}, False),
                               ({'REVIEW_EFFORT':'unknown'}, False), ({'CODEX_VERSION':'0.163.0'}, True)]:
            env = dict(os.environ, REVIEW_MODEL='gpt-5.6-terra', REVIEW_EFFORT='high',
                       CODEX_VERSION='0.149.0', TIMEOUT_MINUTES='12')
            env.update(changes)
            result = subprocess.run(['bash', '-e', '-c', run_block('⚙️ Validate execution settings')],
                                    cwd=self.root, env=env, capture_output=True)
            self.assertEqual(result.returncode == 0, valid, changes)

    def test_dry_run_gates_credentials_and_publication(self):
        text = WORKFLOW.read_text()
        self.assertIn("INCREMENTAL: ${{ toJSON(inputs.incremental) != 'false' }}", text)
        self.assertIn("env.REVIEW_MODE == 'pr' && !inputs.dry_run && !inputs.benchmark_native", text)
        self.assertIn("if: ${{ !inputs.dry_run && !inputs.benchmark_native }}", text)
        self.assertIn('output-schema:', text)


if __name__ == '__main__':
    unittest.main()
