"""Exercise actual workflow cost accounting with synthetic Codex session logs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_review_context import run_block


class ReviewCostTest(unittest.TestCase):
    def estimate(self, model, requests=(), *, duplicate=False, events=None, malformed=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if events is None:
                events, totals = [], dict.fromkeys(
                    ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens'), 0)
                for request in requests:
                    usage = dict(zip(totals, request))
                    totals = {key: totals[key] + usage[key] for key in totals}
                    event = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                        'total_token_usage': dict(totals), 'last_token_usage': usage}}}
                    events.append(event)
                    if duplicate:
                        events.append(event)
            if events:
                (root / 'session.jsonl').write_text(
                    '\n'.join(json.dumps(event) for event in events) + ('\n{"type":' if malformed else '\n'))
            output = root / 'outputs'
            env = dict(os.environ, CODEX_HOME=tmp, MODEL=model, GITHUB_OUTPUT=str(output))
            subprocess.run(['bash', '-e', '-c', run_block('💰 Estimate token usage and cost')],
                           env=env, check=True, capture_output=True)
            return dict(line.split('=', 1) for line in output.read_text().splitlines())

    def test_current_models_and_legacy_rates(self):
        # One 100K-input request: 50K uncached + 50K cached + 10K output.
        expected = {'gpt-6-astra': '1.050000', 'gpt-6.1-sol': '0.205000',
                    'gpt-6-sol': '0.210000', 'gpt-6-luna': '0.010500',
                    'gpt-5.6-sol': '0.420000', 'gpt-5.6-terra': '0.230000',
                    'gpt-5.6-luna': '0.023000', 'gpt-5.3-codex': '0.236250',
                    'gpt-4o': '0.287500'}
        for model, cost in expected.items():
            with self.subTest(model=model):
                data = self.estimate(model, [(100000, 50000, 0, 10000)])
                self.assertEqual(data['estimated_cost_usd'], cost)
                self.assertEqual(data['usage_found'], 'true')
                self.assertEqual(data['price_source'], 'https://developers.openai.com/api/docs/pricing')
                self.assertEqual(data['price_verified_at'], '2026-10-16')

    def test_cache_writes_replace_uncached_tokens_and_reasoning_is_not_extra(self):
        # 25K uncached * $10 + 50K cached * $1 + 25K writes * $12.5 + 10K output * $50.
        data = self.estimate('gpt-6-astra', [(100000, 50000, 25000, 10000)])
        self.assertEqual(data['estimated_cost_usd'], '1.112500')
        self.assertEqual(data['cache_write_input_tokens'], '25000')
        event = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'input_tokens': 100000, 'output_tokens': 10000,
                                  'reasoning_output_tokens': 8000}}}}
        self.assertEqual(self.estimate('gpt-6-astra', events=[event])['estimated_cost_usd'], '1.500000')

    def test_many_short_requests_and_duplicate_events(self):
        data = self.estimate('gpt-5.6-terra', [(100000, 50000, 0, 10000)] * 10, duplicate=True)
        self.assertEqual(data['input_tokens'], '1000000')
        self.assertEqual(data['total_tokens'], '1100000')
        self.assertEqual(data['estimated_cost_usd'], '2.300000')

    def test_long_context_boundary_and_mixed_requests(self):
        # Exactly 272K is short, one more token makes the entire next request long.
        short = self.estimate('gpt-6-astra', [(272000, 0, 0, 1000)])
        self.assertEqual(short['estimated_cost_usd'], '2.770000')
        long = self.estimate('gpt-6-astra', [(272001, 0, 0, 1000)])
        self.assertEqual(long['estimated_cost_usd'], '5.515020')
        mixed = self.estimate('gpt-6-astra', [(100000, 50000, 25000, 10000),
                                                       (300000, 100000, 50000, 10000)])
        # Long request: 150K*$20 + 100K*$2 + 50K*$25 + 10K*$75 = $5.20.
        self.assertEqual(mixed['estimated_cost_usd'], '6.312500')

    def test_no_long_context_premium_for_flat_rate_models(self):
        self.assertEqual(self.estimate('gpt-4.1', [(300000, 0, 0, 10000)])['estimated_cost_usd'], '0.680000')

    def test_unknown_identifier_never_reuses_another_models_rates(self):
        for model in ('unknown', 'gpt-6-astra-future', 'gpt-4o-unverified-snapshot', 'gpt-reserve'):
            data = self.estimate(model, [(100000, 0, 0, 10000)])
            self.assertEqual(data['usage_found'], 'true')
            self.assertEqual(data['estimated_cost_usd'], '')
            self.assertEqual(data['price_source'], '')
            self.assertIn('No verified rates', data['cost_unavailable_reason'])

    def test_unpriced_cache_category_is_unavailable(self):
        data = self.estimate('gpt-5.5', [(100000, 0, 1000, 10000)])
        self.assertEqual(data['estimated_cost_usd'], '')
        self.assertIn('No published rate', data['cost_unavailable_reason'])
        self.assertEqual(self.estimate('gpt-5.5-pro', [(100000, 0, 0, 10000)])['estimated_cost_usd'], '4.800000')

    def test_missing_or_incomplete_usage(self):
        for events in ([], [{'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None}}]):
            data = self.estimate('gpt-6-astra', events=events)
            self.assertEqual(data['usage_found'], 'false')
            self.assertEqual(data['estimated_cost_usd'], '')
        data = self.estimate('gpt-6-astra', [(100000, 0, 0, 10000)], malformed=True)
        self.assertEqual(data['estimated_cost_usd'], '')
        self.assertEqual(data['cost_unavailable_reason'], 'Incomplete session log')

    def test_reset_and_inconsistent_counters_are_not_negative_costs(self):
        for requests in ([(100000, 0, 0, 10000), (-50000, 0, 0, 1000)], [(1000, 2000, 0, 0)]):
            data = self.estimate('gpt-6-astra', requests)
            self.assertEqual(data['estimated_cost_usd'], '')
            self.assertIn('inconsistent', data['cost_unavailable_reason'])

    def test_missing_request_boundaries_do_not_choose_wrong_context_tier(self):
        event = {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'total_token_usage': {'input_tokens': 1000000, 'output_tokens': 10000},
            'last_token_usage': {'input_tokens': 100000, 'output_tokens': 1000}}}}
        data = self.estimate('gpt-6-astra', events=[event])
        self.assertEqual(data['usage_found'], 'true')
        self.assertEqual(data['estimated_cost_usd'], '')
        self.assertIn('Request boundaries', data['cost_unavailable_reason'])


if __name__ == '__main__':
    unittest.main()
