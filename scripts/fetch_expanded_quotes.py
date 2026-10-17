"""Resume bounded public downloads, reusing validated pilot files.

Stops on the first server error, especially HTTP 429, rather than exhausting a
request queue. Re-run manually only after provider access is restored. Each run
preserves a separate attempt record. Never uses credentials or paid endpoints.
"""
import json
import shutil
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from market_diffusion.data import read_events
from market_diffusion.dukascopy import make_requests, MAX_DOWNLOAD, import_hours
from market_diffusion.pipeline import digest


def main():
    root=Path(__file__).resolve().parents[1]
    events=root/'data/processed/cpi-2022-2025-headline-v1/events.csv'
    raw=root/'data/raw/dukascopy/cpi-2022-2025-v2'
    converted=root/'data/interim/dukascopy/cpi-2022-2025-v1'
    prior=root/'data/raw/dukascopy/cpi-2024-v1'
    requests,_=make_requests(read_events(events))
    raw.mkdir(parents=True,exist_ok=True)
    manifest_path=raw/'manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'status':'running','events_sha256':digest(events),'files':[],'errors':[],
        'created_utc':datetime.now(timezone.utc).isoformat()}
    if manifest['events_sha256']!=digest(events): raise ValueError('Event calendar changed')
    known={r['file']:r for r in manifest['files']}
    for name,row in known.items():
        if digest(raw/name)!=row['sha256']: raise ValueError('Saved file hash mismatch')
    old=json.loads((prior/'manifest.json').read_text())
    wanted={r['file'] for r in requests}
    for row in old['files']:
        name=row['file']
        if name in wanted and name not in known:
            if digest(prior/name)!=row['sha256']: raise ValueError('Pilot file hash mismatch')
            if (raw/name).exists(): raise ValueError('Untracked raw file')
            shutil.copy2(prior/name,raw/name)
            known[name]={**row,'copied_from':str(prior/name),'original_manifest_sha256':digest(prior/'manifest.json')}
    attempt={'started_utc':datetime.now(timezone.utc).isoformat(),'downloaded':[],'errors':[]}
    try:
        for request in requests:
            if request['file'] in known: continue
            try:
                with urllib.request.urlopen(request['url'],timeout=30) as response: content=response.read(MAX_DOWNLOAD+1)
                if not content or len(content)>MAX_DOWNLOAD: raise ValueError('Empty or oversized file')
                with (raw/request['file']).open('xb') as stream: stream.write(content)
                row={**request,'sha256':digest(raw/request['file']),'bytes':len(content)}
                known[request['file']]=row; attempt['downloaded'].append(row)
                print('Saved',len(known),'of',len(requests),flush=True)
                # One request per second at most; no parallel burst or host rotation.
                time.sleep(1)
            except Exception as error:
                failure={'file':request['file'],'url':request['url'],'error_type':type(error).__name__}
                if isinstance(error,urllib.error.HTTPError):
                    failure.update(http_status=error.code,retry_after=error.headers.get('Retry-After'),
                                   server_message=error.read(500).decode('utf8',errors='replace'))
                else: failure['message']=str(error)
                attempt['errors'].append(failure)
                print(json.dumps(failure),flush=True)
                break
    finally:
        manifest.update(files=sorted(known.values(),key=lambda r:r['file']),errors=attempt['errors'],
                        status='complete' if len(known)==len(requests) else 'blocked',
                        missing_files=[r['file'] for r in requests if r['file'] not in known])
        attempt['finished_utc']=datetime.now(timezone.utc).isoformat()
        index=len(list(raw.glob('attempt-*.json')))+1
        with (raw/('attempt-%03d.json'%index)).open('x') as stream: json.dump(attempt,stream,indent=2)
        manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    if manifest['status']!='complete':
        print('Blocked:',len(manifest['missing_files']),'hours missing. Original pilot remains usable.')
        return
    if not converted.exists(): print(import_hours(events,raw,converted))

if __name__=='__main__': main()
