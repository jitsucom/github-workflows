"""Exercise the workflow's actual context-preparation shell without model calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/ai-review.yml'


def run_block(name):
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line == f'      - name: "{name}"')
    start = next(i for i in range(start, len(lines)) if lines[i] == '        run: |') + 1
    end = next((i for i in range(start, len(lines)) if lines[i] and not lines[i].startswith('          ')), len(lines))
    return '\n'.join(line[10:] for line in lines[start:end]) + '\n'


class ReviewContextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.repo = self.path / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.common = self.commit('common.txt')
        self.git('checkout', '-b', 'feature')
        self.head = self.commit('feature.txt')
        self.git('checkout', 'main')
        self.base = self.commit('unrelated-base.txt')
        # Simulate manual dispatch from main, then the workflow's head checkout.
        self.git('checkout', '--detach', self.head)
        self.git('remote', 'add', 'origin', str(self.repo))
        self.bin = self.path / 'bin'
        self.bin.mkdir()
        mock = self.bin / 'gh'
        mock.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ['pr', 'view']:
    print(json.dumps({'title':'Intentional routing change','body':'## Intentional decisions and tradeoffs\\nRemove locality preference.','headRefOid':os.environ.get('CONTEXT_HEAD', os.environ['COMPARE_HEAD'])}))
else:
    assert '--paginate' in args and '--slurp' in args
    query = args[args.index('-f') + 1:]  # inspect query independently of flag order
    text = ' '.join(query)
    if 'reviewThreads(first:100,after:$endCursor)' in text:
        print(json.dumps([{'data':{'repository':{'pullRequest':{'reviewThreads':{'nodes':[{'id':'thread-1','isResolved':True,'path':'feature.txt','line':1}]}}}}}, {'data':{'repository':{'pullRequest':{'reviewThreads':{'nodes':[{'id':'thread-2','isResolved':False,'path':'feature.txt','line':1}]}}}}}]))
    else:
        assert 'comments(first:100,after:$endCursor)' in text
        print(json.dumps([{'data':{'node':{'comments':{'nodes':[{'body':'Initial finding'}]}}}}, {'data':{'node':{'comments':{'nodes':[{'body':'Dismissed: intentional tradeoff'}]}}}}]))
''')
        mock.chmod(0o755)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self, filename):
        (self.repo / filename).write_text(filename + '\n')
        self.git('add', filename)
        self.git('commit', '-m', filename)
        return self.git('rev-parse', 'HEAD')

    def prepare(self, mode='pr', **extra):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                   REVIEW_MODE=mode, PR_NUMBER='7' if mode == 'pr' else '',
                   COMMIT_SHA=self.head if mode == 'commit' else '',
                   COMPARE_BASE=self.base if mode == 'pr' else self.common,
                   COMPARE_HEAD=self.head, SUBJECT_URL='https://example.com/pr/7',
                   REVIEW_INSTRUCTIONS='Focus on routing.\nDo not invent requirements.',
                   REPO='owner/repo', GITHUB_OUTPUT=str(self.path / 'outputs'), **extra)
        return subprocess.run(['bash', '-e', '-c', run_block('🧾 Prepare review context')],
                              cwd=self.repo, env=env, capture_output=True, text=True)

    def test_pr_behind_main_and_paginated_history(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        ctx = self.repo / '.github/codex/context'
        target = (ctx / 'review-target.txt').read_text()
        self.assertIn('compare_base: ' + self.common, target)
        self.assertIn('base_tip: ' + self.base, target)
        self.assertEqual((self.repo / '.github/codex/output/diff-files.txt').read_text(), 'feature.txt\n')
        self.assertEqual(len(json.loads((ctx / 'review-threads.json').read_text())), 2)
        comments = json.loads((ctx / 'review-thread-comments.json').read_text())
        self.assertEqual(len(comments), 2)
        self.assertEqual(comments[0]['comments'][-1]['body'], 'Dismissed: intentional tradeoff')
        self.assertIn('Intentional decisions', json.loads((ctx / 'pr.json').read_text())['body'])
        self.assertIn('\nDo not invent', (ctx / 'additional-instructions.txt').read_text())

    def test_commit_uses_parent_without_pr_context(self):
        result = self.prepare('commit')
        self.assertEqual(result.returncode, 0, result.stderr)
        ctx = self.repo / '.github/codex/context'
        self.assertIn('compare_base: ' + self.common, (ctx / 'review-target.txt').read_text())
        self.assertFalse((ctx / 'pr.json').exists())

    def test_wrong_checkout_fails(self):
        self.git('checkout', '--detach', self.base)
        self.assertNotEqual(self.prepare().returncode, 0)

    def test_newer_pr_context_fails(self):
        self.assertNotEqual(self.prepare(CONTEXT_HEAD=self.base).returncode, 0)

    def test_checkout_and_prompt_contract(self):
        text = WORKFLOW.read_text()
        self.assertIn('ref: ${{ steps.resolve-target.outputs.compare_head }}', text)
        prompt_script = run_block('📄 Write Codex prompt')
        subprocess.run(['bash', '-e', '-c', prompt_script], cwd=self.repo, check=True)
        prompt = (self.repo / '.github/workflows/ai-review.prompt.md').read_text()
        self.assertIn('An empty findings list is valid.', prompt)
        self.assertIn('Intentional tradeoffs are context, not immunity', prompt)
        self.assertIn('Re-raise a dismissed finding only if new changes invalidate', prompt)
        self.assertNotIn('still prefer to post it', prompt)
        self.assertIn('model: gpt-5.6-terra', text)
        self.assertIn('codex-version: 0.149.0', text)


if __name__ == '__main__':
    unittest.main()
