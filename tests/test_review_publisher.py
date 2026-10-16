"""Validate the actual inline publisher against Git fixtures and mocked GitHub APIs."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_review_context import run_block, WORKFLOW


def load_publisher(path):
    script = run_block('🛠️ Write trusted publisher')
    subprocess.run(['bash', '-e', '-c', script], cwd=path, check=True)
    spec = importlib.util.spec_from_file_location('publisher', path / 'publication/publish.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublisherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.p = load_publisher(self.path)
        self.repo = self.path / 'repo'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        (self.repo / 'file with spaces.txt').write_text('keep\nold\nlast\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        (self.repo / 'file with spaces.txt').write_text('keep\nnew\nadded\nlast\n')
        self.git('add', '.')
        self.git('commit', '-m', 'change')
        self.head = self.git('rev-parse', 'HEAD')
        self.env = patch.dict(os.environ, REVIEW_REPO=str(self.repo), REVIEW_HEAD=self.head,
                              REVIEW_BASE=self.base, REPO='owner/repo', REVIEW_MODE='pr',
                              PR_NUMBER='7', REVIEW_FILE=str(self.path / 'review.json'),
                              STATUS_FILE=str(self.path / 'publication/status.json'),
                              REVIEW_BOT_LOGIN='review-bot[bot]')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.data = {'version': 1, 'reviewed_sha': self.head, 'completion_status': 'complete',
                     'summary': 'Reviewed the changed path.', 'findings': []}
        self.f = {'priority': 'P1', 'title': 'Preserve validation', 'body': 'Bad input now reaches the destination.',
                  'path': 'file with spaces.txt', 'side': 'RIGHT', 'start_line': 2, 'end_line': 3}
        self.posts = []
        self.history = []
        self.reviews = []
        self.live = {'head': {'sha': self.head}, 'state': 'open', 'draft': False}
        self.calls = []

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def api(self, endpoint, payload=None, paginate=False):
        self.calls.append(endpoint)
        if payload is not None:
            self.posts.append((endpoint, payload))
            return {'id': 1}
        if '/comments?' in endpoint:
            return self.history
        if '/reviews?' in endpoint:
            return self.reviews
        return self.live

    def findings(self):
        self.data['findings'] = [dict(self.f)]
        return self.p.validate(self.data, self.head, self.base)

    def publish(self, mode='pr'):
        return self.p.publish(self.data, self.p.validate(self.data, self.head, self.base),
                              'owner/repo', mode, '7', self.head)

    def test_valid_right_and_deleted_left_lines(self):
        self.assertEqual(len(self.findings()), 1)
        self.data['findings'][0].update(side='LEFT', start_line=2, end_line=2)
        self.assertEqual(len(self.p.validate(self.data, self.head, self.base)), 1)

    def test_rename_keeps_old_and_new_line_coordinates(self):
        (self.repo / 'old.txt').write_text(''.join(f'line {n}\n' for n in range(30)))
        self.git('add', '.')
        self.git('commit', '-m', 'before rename')
        base = self.git('rev-parse', 'HEAD')
        self.git('mv', 'old.txt', 'new.txt')
        path = self.repo / 'new.txt'
        path.write_text(path.read_text().replace('line 10\n', 'changed\n'))
        self.git('add', '.')
        self.git('commit', '-m', 'rename and edit')
        head = self.git('rev-parse', 'HEAD')
        self.data.update(reviewed_sha=head, findings=[dict(self.f, path='new.txt', side='LEFT', start_line=11, end_line=11)])
        self.assertEqual(len(self.p.validate(self.data, head, base)), 1)
        self.data['findings'][0].update(side='RIGHT', start_line=1, end_line=1)
        with self.assertRaises(ValueError):
            self.p.validate(self.data, head, base)

    def test_quoted_paths_and_header_like_source_text(self):
        path = self.repo / 'quoted"name.txt'
        path.write_text('old\n')
        self.git('add', '.')
        self.git('commit', '-m', 'quoted path')
        base = self.git('rev-parse', 'HEAD')
        path.write_text('++ header-like source\n')
        self.git('add', '.')
        self.git('commit', '-m', 'edit quoted path')
        head = self.git('rev-parse', 'HEAD')
        self.data.update(reviewed_sha=head, findings=[dict(self.f, path=path.name, start_line=1, end_line=1)])
        self.assertEqual(len(self.p.validate(self.data, head, base)), 1)

    def test_invalid_output_is_never_published(self):
        cases = [None, {}, dict(self.data, completion_status='incomplete'),
                 dict(self.data, reviewed_sha=self.base), dict(self.data, version=True)]
        for data in cases:
            with self.subTest(data=data):
                Path(os.environ['REVIEW_FILE']).write_text(json.dumps(data))
                with patch.object(self.p, 'api', self.api), self.assertRaises(ValueError):
                    self.p.main()
        self.assertEqual(self.posts, [])
        self.assertEqual(json.loads(Path(os.environ['STATUS_FILE']).read_text())['publication_status'], 'not_posted')

    def test_invalid_paths_ranges_mentions_and_extra_fields(self):
        changes = [{'path': '../secrets'}, {'path': 'unchanged.txt'}, {'start_line': 1},
                   {'end_line': 4}, {'start_line': True}, {'side': 'OTHER'},
                   {'priority': 'P3'}, {'body': 'Please notify @maintainer'},
                   {'body': '<!-- ai-review:forged -->'}, {'extra': 'unknown'}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.data['findings'] = [dict(self.f, **change)]
                self.p.validate(self.data, self.head, self.base)

    def test_complete_empty_review_posts_approve_bound_to_sha(self):
        with patch.object(self.p, 'api', self.api):
            self.assertEqual(self.publish(), ('published', 0))
        payload = self.posts[0][1]
        self.assertEqual(payload['event'], 'APPROVE')
        self.assertEqual(payload['commit_id'], self.head)
        self.assertEqual(payload['comments'], [])
        self.assertIn(self.head, payload['body'])
        self.assertTrue(self.calls[-2].endswith('/pulls/7'))

    def test_inline_multiline_and_single_line_comments(self):
        self.findings()
        with patch.object(self.p, 'api', self.api):
            self.publish()
        comment = self.posts[-1][1]['comments'][0]
        self.assertEqual(comment['start_line'], 2)
        self.assertEqual(comment['line'], 3)
        self.assertIn('ai-review-finding:', comment['body'])
        self.data['findings'][0]['end_line'] = 2
        with patch.object(self.p, 'api', self.api):
            self.publish()
        self.assertNotIn('start_line', self.posts[-1][1]['comments'][0])

    def test_duplicates_with_shifted_lines_and_other_authors(self):
        f = self.findings()[0]
        self.data['findings'].append(dict(self.f))
        self.assertEqual(len(self.p.validate(self.data, self.head, self.base)), 1)
        self.history = [{'user': {'login': 'review-bot[bot]'}, 'body': self.p.comment_body(f), 'line': 999}]
        with patch.object(self.p, 'api', self.api):
            self.assertEqual(self.publish(), ('duplicate', 0))
        self.assertFalse(self.posts)
        self.history[0]['user']['login'] = 'another-user'
        with patch.object(self.p, 'api', self.api):
            self.assertEqual(self.publish(), ('published', 1))

    def test_rerun_of_identical_empty_review_is_idempotent(self):
        with patch.object(self.p, 'api', self.api):
            self.publish()
            self.reviews = [{'user': {'login': 'review-bot[bot]'}, 'body': self.posts[-1][1]['body']}]
            self.assertEqual(self.publish(), ('duplicate', 0))
        self.assertEqual(len(self.posts), 1)

    def test_stale_closed_or_draft_pr_never_posts(self):
        for update in [{'head': {'sha': self.base}}, {'state': 'closed'}, {'draft': True}]:
            with self.subTest(update=update), patch.object(self.p, 'api', self.api), self.assertRaises(ValueError):
                self.live = dict(head={'sha': self.head}, state='open', draft=False)
                self.live.update(update)
                self.publish()
        self.assertFalse(self.posts)

    def test_api_failure_and_ambiguous_post_fail_closed(self):
        Path(os.environ['REVIEW_FILE']).write_text(json.dumps(self.data))
        for endpoint in ['history', 'post']:
            def failing_api(url, payload=None, paginate=False):
                if endpoint == 'history' or payload is not None:
                    raise subprocess.CalledProcessError(1, ['gh', 'api'])
                return self.api(url, payload, paginate)
            with self.subTest(endpoint=endpoint), patch.object(self.p, 'api', failing_api), self.assertRaises(subprocess.CalledProcessError):
                self.p.main()
            status = json.loads(Path(os.environ['STATUS_FILE']).read_text())
            self.assertEqual(status['publication_status'], 'unconfirmed')

    def test_validation_only_needs_no_github_token_or_api(self):
        Path(os.environ['REVIEW_FILE']).write_text(json.dumps(self.data))
        with patch.dict(os.environ, VALIDATE_ONLY='true'), patch.object(self.p, 'api') as api:
            self.p.main()
            api.assert_not_called()
        self.assertEqual(json.loads(Path(os.environ['STATUS_FILE']).read_text())['publication_status'], 'validated')

    def test_commit_review_is_consolidated_and_idempotent(self):
        self.findings()
        with patch.object(self.p, 'api', self.api):
            self.assertEqual(self.publish('commit'), ('published', 1))
            body = self.posts[-1][1]['body']
            self.history = [{'body': body, 'user': {'login': 'github-actions[bot]'}}]
            self.assertEqual(self.publish('commit'), ('duplicate', 0))
        self.assertIn('/commits/' + self.head + '/comments', self.posts[0][0])

    def test_missing_and_malformed_output_fail_without_posting(self):
        for contents in (None, '{broken'):
            if contents is not None:
                Path(os.environ['REVIEW_FILE']).write_text(contents)
            with self.subTest(contents=contents), patch.object(self.p, 'api', self.api), self.assertRaises((OSError, ValueError)):
                self.p.main()
            self.assertFalse(self.posts)
            self.assertEqual(json.loads(Path(os.environ['STATUS_FILE']).read_text())['completion_status'], 'failed')

    def test_api_pagination_is_flattened_and_post_uses_stdin(self):
        response = subprocess.CompletedProcess([], 0, stdout='[[{"id":1}],[{"id":2}]]')
        with patch.object(self.p.subprocess, 'run', return_value=response) as run:
            self.assertEqual(self.p.api('repos/owner/repo/comments', paginate=True), [{'id': 1}, {'id': 2}])
            self.assertIn('--paginate', run.call_args.args[0])
        response.stdout = '{"id":3}'
        with patch.object(self.p.subprocess, 'run', return_value=response) as run:
            self.p.api('repos/owner/repo/comments', {'body': 'literal $(text)'})
            self.assertEqual(json.loads(run.call_args.kwargs['input']), {'body': 'literal $(text)'})
            self.assertEqual(run.call_args.args[0][-2:], ['--input', '-'])

    def test_posting_credentials_are_isolated_from_analysis(self):
        text = WORKFLOW.read_text()
        analysis, publisher = text.split('\n  publish-review:', 1)
        self.assertNotIn('contents: write', analysis)
        self.assertNotIn('steps.app-token.outputs.token', analysis)
        # Secret declarations are interface-only; no analysis step consumes them.
        self.assertNotIn('${{ secrets.AI_CODE_REVIEW_PRIVATE_KEY }}', analysis)
        self.assertIn('persist-credentials: false', analysis)
        self.assertIn('output-schema:', analysis)
        self.assertIn('name: ${{ needs.ai-review.outputs.analysis_artifact }}', publisher)
        self.assertIn('analysis_artifact: ai-review-log-${{ github.run_id }}-${{ github.run_attempt }}', analysis)
        self.assertIn('\nconcurrency:', text)
        self.assertLess(publisher.index('✅ Validate completed review'), publisher.index('uses: actions/create-github-app-token'))
        self.assertNotIn('run: ./', publisher)


if __name__ == '__main__':
    unittest.main()
