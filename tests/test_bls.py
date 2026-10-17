"""Guard against revised-column extraction, DST errors and fabricated consensus."""

import csv
import tempfile
import unittest
from pathlib import Path

from market_diffusion.bls import CONSENSUS_FIELDS, import_releases, join_consensus, parse_release, write_table
from market_diffusion.data import read_events


# Deliberately constructed parser fixture. It is not an empirical source record.
FIXTURE = '''https://www.bls.gov/news.release/archives/cpi_01112024.htm
L176: Transmission of material in this release is embargoed until
L177: 8:30 a.m. (ET) Thursday, January 11, 2024
L182: CONSUMER PRICE INDEX - DECEMBER 2023
L204: Table A. Percent changes in CPI for All Urban Consumers (CPI-U): U.S. city average
L205: Seasonally adjusted changes from preceding month | Un-adjusted 12-mos.
L220: All items | 9.0 | 8.0 | 7.0 | 6.0 | 5.0 | 4.0 | 0.3 | 3.4
L231: All items less food and energy | 9.0 | 8.0 | 7.0 | 6.0 | 5.0 | 4.0 | 0.2 | 3.9
L241: Footnotes
'''


class ParserTests(unittest.TestCase):
    def test_latest_columns_not_revised_history(self):
        record = parse_release(FIXTURE)
        self.assertEqual(record['headline_mom_sa'], 0.3)
        self.assertEqual(record['core_mom_sa'], 0.2)
        self.assertEqual(record['headline_yoy_nsa'], 3.4)
        self.assertEqual(record['reference_month'], '2023-12')

    def test_historical_dst(self):
        self.assertEqual(parse_release(FIXTURE)['release_utc'], '2024-01-11T13:30:00+00:00')
        summer = FIXTURE.replace('01112024', '07112024').replace('January', 'July').replace('DECEMBER 2023', 'JUNE 2024')
        self.assertEqual(parse_release(summer)['release_utc'], '2024-07-11T12:30:00+00:00')

    def test_changed_schema_fails_closed(self):
        for bad in [FIXTURE.replace('| 9.0', ''), FIXTURE.replace('All items |', 'Other items |'),
                    FIXTURE.replace('(ET)', '(UTC)'), FIXTURE.replace('Thursday', 'Friday'),
                    FIXTURE.replace('01112024', '01122024'), FIXTURE.replace('DECEMBER 2023', 'JANUARY 2024')]:
            with self.assertRaises(ValueError):
                parse_release(bad)

    def test_later_tables_do_not_override_table_a(self):
        extra = FIXTURE + 'L500: All items | 99 | 99 | 99\n'
        self.assertEqual(parse_release(extra)['headline_mom_sa'], 0.3)

    def test_import_does_not_fill_unknown_consensus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cpi_test.txt').write_text(FIXTURE)
            result = import_releases(root, root / 'output')
            self.assertFalse(result['ready_for_market_analysis'])
            with (root / 'output/consensus_template.csv').open() as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual(len(records), 2)
            self.assertTrue(all(row['consensus'] == '' for row in records))
            with self.assertRaises(ValueError):
                import_releases(root, root / 'output')

    def test_duplicate_release_fails_without_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['cpi_a.txt', 'cpi_b.txt']:
                (root / name).write_text(FIXTURE)
            with self.assertRaises(ValueError):
                import_releases(root, root / 'output')
            self.assertFalse((root / 'output').exists())


class ConsensusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.actual = parse_release(FIXTURE)
        # Synthetic test expectations are labelled explicitly in their provenance.
        self.consensus = {'event_id': self.actual['event_id'], 'series': 'US_CPI_HEADLINE_MOM_SA',
                          'reference_month': '2023-12', 'unit': 'percentage_points', 'consensus': 0.2,
                          'consensus_asof_utc': '2024-01-11T12:30:00Z', 'source': 'SIMULATED unit-test fixture',
                          'evidence_file': 'evidence.txt'}
        (self.root / 'evidence.txt').write_text('SIMULATED evidence only; no historical claim.')
        write_table(self.root / 'actuals.csv', [self.actual])

    def tearDown(self):
        self.temp.cleanup()

    def join(self, records):
        write_table(self.root / 'consensus.csv', records, CONSENSUS_FIELDS)
        return join_consensus(self.root / 'actuals.csv', self.root / 'consensus.csv', self.root / 'joined', 'headline')

    def test_valid_join_matches_main_event_contract(self):
        self.join([self.consensus])
        events = read_events(self.root / 'joined/events.csv')
        self.assertAlmostEqual(events[0].surprise, 0.1)
        self.assertIn('SIMULATED', events[0].source)
        self.assertTrue((self.root / 'joined/manifest.json').exists())

    def test_blank_unselected_core_is_allowed(self):
        core = dict(self.consensus, series='US_CPI_CORE_MOM_SA', consensus='', consensus_asof_utc='', source='', evidence_file='')
        self.assertEqual(self.join([self.consensus, core])['events'], 1)

    def test_late_consensus_fails_without_writing(self):
        with self.assertRaisesRegex(ValueError, 'predate'):
            self.join([dict(self.consensus, consensus_asof_utc='2024-01-11T13:30:00Z')])
        self.assertFalse((self.root / 'joined').exists())

    def test_wrong_period_rejected(self):
        with self.assertRaisesRegex(ValueError, 'month mismatch'):
            self.join([dict(self.consensus, reference_month='2024-01')])

    def test_wrong_units_rejected(self):
        with self.assertRaisesRegex(ValueError, 'units'):
            self.join([dict(self.consensus, unit='fraction')])

    def test_missing_evidence_rejected(self):
        with self.assertRaisesRegex(ValueError, 'evidence file'):
            self.join([dict(self.consensus, evidence_file='missing.txt')])

    def test_missing_consensus_never_becomes_zero(self):
        with self.assertRaisesRegex(ValueError, 'Missing consensus'):
            self.join([dict(self.consensus, consensus='')])

    def test_duplicate_consensus_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.join([self.consensus, self.consensus])


if __name__ == '__main__':
    unittest.main()
