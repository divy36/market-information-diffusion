"""Acquire original monthly ASCII bid/ask archives without accounts or keys.

The site's ordinary download form supplies a transient token. We submit only
that download form, never subscription/payment forms. Originals and hashes are
retained; retries reuse verified files and stop on rate limits.
"""
import argparse
import hashlib
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import zipfile
from html.parser import HTMLParser
from datetime import datetime, timezone
from pathlib import Path
from market_diffusion.data import read_events
from market_diffusion.pipeline import digest

class DownloadForm(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=False; self.fields={}; self.action=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='form':
            self.active=attrs.get('id')=='file_down'
            if self.active: self.action=attrs.get('action')
        if tag=='input' and self.active and 'name' in attrs:
            self.fields[attrs['name']]=attrs.get('value','')
    def handle_endtag(self,tag):
        if tag=='form': self.active=False

def main():
    root=Path(__file__).resolve().parents[1]
    events=root/'data/processed/cpi-2022-2025-headline-v1/events.csv'
    out=root/'data/raw/histdata/cpi-2022-2025-v1'; out.mkdir(parents=True,exist_ok=True)
    path=out/'manifest.json'
    manifest=json.loads(path.read_text()) if path.exists() else dict(events_sha256=digest(events),files=[],attempts=[])
    if manifest['events_sha256']!=digest(events): raise ValueError('Event calendar changed')
    known={row['file']:row for row in manifest['files']}
    for name,row in known.items():
        if digest(out/name)!=row['sha256']: raise ValueError('Original hash changed')
    months=sorted({e.release.strftime('%Y%m') for e in read_events(events)})
    errors=[]
    for month in months:
        for symbol in ['EURUSD','GBPUSD']:
            name='HISTDATA_COM_ASCII_'+symbol+'_T'+month+'.zip'
            if name in known: continue
            url='https://www.histdata.com/download-free-forex-historical-data/?/ascii/tick-data-quotes/'+symbol.lower()+'/'+month[:4]+'/'+str(int(month[4:]))
            try:
                with urllib.request.urlopen(url,timeout=45) as response: page=response.read(2_000_001)
                if len(page)>2_000_000: raise ValueError('Oversized form')
                form=DownloadForm(); form.feed(page.decode('utf8'))
                required=dict(date=month[:4],datemonth=month,platform='ASCII',timeframe='T',fxpair=symbol)
                if form.action!='/get.php' or any(form.fields.get(k)!=v for k,v in required.items()) or not form.fields.get('tk'):
                    raise ValueError('Unexpected download form identity')
                request=urllib.request.Request('https://www.histdata.com/get.php',data=urllib.parse.urlencode(form.fields).encode(),headers={'Referer':url})
                with urllib.request.urlopen(request,timeout=60) as response:
                    data=response.read(150_000_001)
                if len(data)>150_000_000 or not data.startswith(b'PK'): raise ValueError('Not a bounded ZIP')
                # A staging file is promoted only after ZIP structure checks.
                temp=out/(name+'.partial'); temp.write_bytes(data)
                expected='DAT_ASCII_'+symbol+'_T_'+month+'.csv'
                with zipfile.ZipFile(temp) as archive:
                    member=archive.getinfo(expected)
                    if member.file_size>1_000_000_000: raise ValueError('Oversized CSV')
                temp.rename(out/name)
                known[name]=dict(file=name,symbol=symbol,month=month,url=url,sha256=hashlib.sha256(data).hexdigest(),
                                 bytes=len(data),csv_member=expected,downloaded_utc=datetime.now(timezone.utc).isoformat())
                print('Saved %d/%d: %s'%(len(known),len(months)*2,name),flush=True)
            except Exception as error:
                entry=dict(file=name,url=url,error_type=type(error).__name__,message=str(error))
                if isinstance(error,urllib.error.HTTPError): entry['http_status']=error.code
                errors.append(entry); print(entry,flush=True)
            finally:
                manifest.update(status='complete' if len(known)==len(months)*2 else 'incomplete',files=sorted(known.values(),key=lambda r:r['file']),expected_archives=len(months)*2)
                path.write_text(json.dumps(manifest,indent=2)+'\n')
            if errors and errors[-1].get('http_status')==429: break
            time.sleep(.5)
        if errors and errors[-1].get('http_status')==429: break
    manifest['attempts'].append(dict(finished_utc=datetime.now(timezone.utc).isoformat(),errors=errors))
    path.write_text(json.dumps(manifest,indent=2)+'\n')
    if manifest['status']!='complete': raise ValueError('Incomplete acquisition; inspect manifest')

if __name__=='__main__': main()
