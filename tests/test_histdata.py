"""Tests for the source-specific clock and strict bid/ask contract."""
import unittest
import json
import tempfile
import zipfile
from pathlib import Path
from market_diffusion.bls import write_table
from market_diffusion.pipeline import digest
from market_diffusion.histdata_import import import_archives
from market_diffusion.histdata_import import parse_tick

class HistDataTests(unittest.TestCase):
    def test_archive_import_duplicate_policy_and_hash_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); raw=root/'raw'; raw.mkdir()
            events=root/'events.csv'
            write_table(events,[dict(event_id='e',series='CPI',release_utc='2024-07-11T12:30:00Z',
                actual=.1,consensus=.2,consensus_asof_utc='2024-07-11T11:00:00Z',unit='pp',source='fixture')])
            member='DAT_ASCII_EURUSD_T_202407.csv'; archive=raw/'sample.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr(member,'20240711 072859000,1,1.1,0\n20240711 073000250,1,1.1,0\n20240711 073000250,1.01,1.11,0\n20240711 190000000,1,1.1,0\n20240711 180000000,1,1.1,0\n')
            manifest=dict(status='complete',events_sha256=digest(events),files=[dict(file='sample.zip',sha256=digest(archive),csv_member=member,month='202407',symbol='EURUSD')])
            (raw/'manifest.json').write_text(json.dumps(manifest))
            result=import_archives(events,raw,root/'out')
            self.assertEqual(result['quotes'],2)
            self.assertEqual(result['files'][0]['equal_timestamp_overwrites'],1)
            self.assertEqual(len(result['files'][0]['clock_reversals']),1)
            text=(root/'out/quotes.csv').read_text()
            self.assertIn('2024-07-11T12:30:00.250000+00:00,1.01,1.11',text)
            archive.write_bytes(b'changed')
            with self.assertRaises(ValueError): import_archives(events,raw,root/'bad')
            self.assertFalse((root/'bad').exists())
    def test_summer_is_fixed_est_not_new_york_civil_time(self):
        stamp,bid,ask=parse_tick(['20240711 073000250','1.1','1.2','0'])
        self.assertEqual(stamp.isoformat(),'2024-07-11T12:30:00.250000+00:00')
        self.assertEqual((bid,ask),(1.1,1.2))
    def test_winter_and_millisecond_precision(self):
        self.assertEqual(parse_tick(['20220112 083000001','1','1','0'])[0].isoformat(),'2022-01-12T13:30:00.001000+00:00')
    def test_reject_wrong_format_crossed_nonfinite_and_invalid_date(self):
        for row in [['20220112 083000','1','2','0'],['20220112 083000000','2','1','0'],
                    ['20220112 083000000','nan','2','0'],['20220230 083000000','1','2','0'],
                    ['20220112 083000000','1','2','-1']]:
            with self.assertRaises(ValueError): parse_tick(row)

if __name__=='__main__': unittest.main()
