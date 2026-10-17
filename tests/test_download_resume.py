"""A server rate limit must stop downloads and preserve verified local files."""
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from market_diffusion.bls import write_table
from market_diffusion.pipeline import digest

class ResumeTests(unittest.TestCase):
    def test_rate_limit_stops_and_resume_preserves_original(self):
        script=Path(__file__).resolve().parents[1]/'scripts/fetch_expanded_quotes.py'
        spec=importlib.util.spec_from_file_location('resume_under_test',script)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); module.__file__=str(root/'scripts/fetch_expanded_quotes.py')
            events=root/'data/processed/cpi-2022-2025-headline-v1/events.csv'
            events.parent.mkdir(parents=True)
            write_table(events,[dict(event_id='fixture',series='CPI',release_utc='2024-01-11T13:30:00Z',
                actual=.3,consensus=.2,consensus_asof_utc='2024-01-11T12:00:00Z',unit='pp',source='test fixture')])
            prior=root/'data/raw/dukascopy/cpi-2024-v1'; prior.mkdir(parents=True)
            name='EURUSD-20240111T13.bi5'; (prior/name).write_bytes(b'original fixture bytes')
            (prior/'manifest.json').write_text(json.dumps({'files':[{'file':name,'sha256':digest(prior/name)}]}))
            raw=root/'data/raw/dukascopy/cpi-2022-2025-v2'
            for attempt in [1,2]:
                error=urllib.error.HTTPError('https://example.test',429,'Too Many Requests',{},io.BytesIO(b'rate limited'))
                with patch.object(module.urllib.request,'urlopen',side_effect=error) as download, patch('builtins.print'):
                    module.main()
                self.assertEqual(download.call_count,1)
                manifest=json.loads((raw/'manifest.json').read_text())
                self.assertEqual(manifest['status'],'blocked')
                self.assertEqual(manifest['errors'][0]['http_status'],429)
                self.assertEqual(len(manifest['missing_files']),3)
                self.assertEqual((raw/name).read_bytes(),b'original fixture bytes')
                self.assertTrue((raw/('attempt-%03d.json'%attempt)).exists())
            self.assertFalse((root/'data/interim/dukascopy/cpi-2022-2025-v1/quotes.csv').exists())

if __name__=='__main__': unittest.main()
