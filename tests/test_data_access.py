"""Offline contract tests: no account credentials or billable API calls."""

import csv
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from market_diffusion.bls import write_table
from market_diffusion.consensus_pdf import parse_calendar
from market_diffusion.data import read_quotes, utc
from market_diffusion.databento_api import (EPOCH, ceil_microsecond, client_from_environment,
                                           convert_csv, download, estimate, make_plan)


class ConsensusPDFTests(unittest.TestCase):
    text = '''January 11, 2024 @ 7:15 EST
Country Date Time Indicator Period BNS Consensus Latest
US 01-11 08:30 CPI (m/m) Dec 0.3 0.2 0.1
US 01-11 08:30 CPI ex. Food & Energy (m/m) Dec 0.4 0.3 0.3
'''
    actual = {'event_id':'US-CPI-2024-01-11', 'release_utc':'2024-01-11T13:30:00Z', 'reference_month':'2023-12'}

    def test_consensus_not_bank_forecast(self):
        values, audit = parse_calendar(self.text, self.actual)
        self.assertEqual(values, {'headline': 0.2, 'core': 0.3})
        self.assertEqual(audit['availability_upper_bound_utc'], '2024-01-11T12:15:00+00:00')

    def test_summer_est_label_withheld_when_later_bound_is_after_release(self):
        text = self.text.replace('January 11', 'September 11').replace('7:15', '7:35').replace('01-11', '09-11').replace('Dec', 'Aug')
        actual = dict(self.actual, release_utc='2024-09-11T12:30:00Z', reference_month='2024-08')
        _, audit = parse_calendar(text, actual)
        self.assertTrue(audit['timezone_ambiguity'])
        self.assertTrue(audit['status'].startswith('withheld'))

    def test_earlier_summer_report_is_safe_under_both_interpretations(self):
        text = self.text.replace('January 11', 'July 11').replace('7:15', '6:50').replace('01-11', '07-11').replace('Dec', 'Jun')
        actual = dict(self.actual, release_utc='2024-07-11T12:30:00Z', reference_month='2024-06')
        _, audit = parse_calendar(text, actual)
        self.assertEqual(audit['availability_upper_bound_utc'], '2024-07-11T11:50:00+00:00')
        self.assertTrue(audit['status'].startswith('usable'))

    def test_mismatched_period_or_clock_rejected(self):
        for text in [self.text.replace('Dec', 'Jan'), self.text.replace('08:30', '09:30'), self.text.replace('BNS Consensus', 'Consensus BNS')]:
            with self.assertRaises(ValueError):
                parse_calendar(text, self.actual)


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        actuals = self.root / 'actuals.csv'
        write_table(actuals, [{'event_id':'CPI-2024-01-11', 'release_utc':'2024-01-11T13:30:00Z'}])
        self.plan = self.root / 'plan.json'
        make_plan(actuals, self.plan)

    def tearDown(self):
        self.temp.cleanup()

    def fake_client(self, price=0.01):
        calls = []
        def get_cost(**kwargs):
            calls.append(('cost', kwargs))
            return price
        def get_range(**kwargs):
            calls.append(('prices', kwargs))
            kwargs['path'].write_bytes(b'SIMULATED SDK RESPONSE FOR UNIT TEST')
            request = kwargs
            class Store:
                def to_csv(self, path, **options):
                    assert options == dict(pretty_ts=False, pretty_px=True, map_symbols=True, mode='x')
                    ns = int(utc(request['start']).timestamp()) * 10**9
                    write_table(path, [dict(ts_recv=ns, rtype=1, instrument_id=123,
                                           bid_px_00=100, ask_px_00=101, bid_sz_00=3, ask_sz_00=4, flags=128)])
            return Store()
        return SimpleNamespace(metadata=SimpleNamespace(get_cost=get_cost), timeseries=SimpleNamespace(get_range=get_range)), calls

    def test_plan_covers_only_bounded_windows(self):
        plan = json.loads(self.plan.read_text())
        self.assertEqual(len(plan['requests']), 2)
        self.assertEqual(plan['requests'][0]['symbols'], ['ES.v.0'])
        self.assertEqual(plan['requests'][0]['start'], '2024-01-11T13:28:58+00:00')

    def test_cost_query_never_downloads(self):
        client, calls = self.fake_client()
        self.assertEqual(estimate(self.plan, client)['estimated_usd'], 0.02)
        self.assertTrue(all(kind == 'cost' for kind, _ in calls))

    def test_above_ceiling_blocks_before_download(self):
        client, calls = self.fake_client()
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            download(self.plan, self.root/'raw', self.root/'output', 0, client)
        self.assertFalse((self.root/'raw').exists())
        self.assertTrue(all(kind == 'cost' for kind, _ in calls))

    def test_offline_end_to_end_download_contract(self):
        client, calls = self.fake_client()
        result = download(self.plan, self.root/'raw', self.root/'output', 0.02, client)
        self.assertEqual(result['quote_rows'], 2)
        quotes = read_quotes(self.root/'output/quotes.csv')
        self.assertEqual(set(quotes), {'ES', 'ZN'})
        self.assertEqual(json.loads((self.root/'output/manifest.json').read_text())['status'], 'complete')

    def test_cost_error_text_is_not_exposed(self):
        def bad(**kwargs):
            raise RuntimeError('SENSITIVE_TEST_TOKEN')
        client = SimpleNamespace(metadata=SimpleNamespace(get_cost=bad))
        with self.assertRaises(ValueError) as caught:
            estimate(self.plan, client)
        self.assertNotIn('SENSITIVE_TEST_TOKEN', str(caught.exception))

    def test_missing_key_is_actionable_without_sdk_import(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'DATABENTO_API_KEY'):
                client_from_environment()


class QuoteConversionTests(unittest.TestCase):
    start='2024-01-11T13:29:00Z'
    end='2024-01-11T14:01:00Z'
    base_ns=int(utc(start).timestamp())*10**9

    def record(self, **kwargs):
        return dict(dict(ts_recv=self.base_ns, rtype=1, instrument_id=123,
                         bid_px_00=100, ask_px_00=101, bid_sz_00=3, ask_sz_00=4, flags=128), **kwargs)

    def convert(self, records):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'quotes.csv'
            write_table(path, records)
            return convert_csv(path, 'ES', self.start, self.end)

    def test_nanoseconds_round_up_not_back_into_past(self):
        self.assertEqual(ceil_microsecond(1001), EPOCH+timedelta(microseconds=2))
        self.assertEqual(ceil_microsecond(1000), EPOCH+timedelta(microseconds=1))

    def test_collision_retains_latest_available_observation(self):
        records = [self.record(ts_recv=self.base_ns+1), self.record(ts_recv=self.base_ns+900, bid_px_00=100.5)]
        result, audit = self.convert(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['bid'], 100.5)
        self.assertEqual(audit['microsecond_collisions'], 1)

    def test_nonfinal_records_do_not_create_book_snapshots(self):
        result, audit = self.convert([self.record(flags=0), self.record(ts_recv=self.base_ns+1000)])
        self.assertEqual(len(result), 1)
        self.assertEqual(audit['nonfinal_rows'], 1)

    def test_bad_clock_book_and_empty_quotes_rejected(self):
        for update in [{'flags':136}, {'flags':132}, {'flags':130}, {'bid_sz_00':0}, {'ask_px_00':99}, {'rtype':0}]:
            with self.assertRaises(ValueError):
                self.convert([self.record(**update)])

    def test_contract_roll_and_out_of_order_records_rejected(self):
        for records in [[self.record(), self.record(instrument_id=456)],
                        [self.record(ts_recv=self.base_ns+1), self.record()]]:
            with self.assertRaises(ValueError):
                self.convert(records)

    def test_exclusive_end_is_enforced(self):
        with self.assertRaises(ValueError):
            self.convert([self.record(ts_recv=int(utc(self.end).timestamp())*10**9)])


if __name__ == '__main__':
    unittest.main()
