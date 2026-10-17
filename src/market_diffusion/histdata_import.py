"""Import HistData ASCII ticks using fixed EST (UTC-05), without DST.

Never mix this source with Dukascopy. Scan originals without extracting ZIP paths;
retain event windows only, and keep the final source state for equal timestamps.
All retained prices and clocks are validated before the output is promoted.
"""
import csv
import io
import json
import math
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .data import read_events
from .pipeline import digest

EST=timezone(timedelta(hours=-5))

def parse_tick(row):
    if len(row)!=4 or not re.fullmatch(r'\d{8} \d{9}',row[0]):
        raise ValueError('Invalid ASCII tick schema')
    stamp=datetime.strptime(row[0],'%Y%m%d %H%M%S%f').replace(tzinfo=EST).astimezone(timezone.utc)
    bid,ask,volume=map(float,row[1:])
    if not all(math.isfinite(v) for v in [bid,ask,volume]) or not 0<bid<=ask or volume<0:
        raise ValueError('Invalid bid/ask/volume')
    return stamp,bid,ask

def import_archives(events_path,raw,output):
    if output.exists(): raise ValueError('Output already exists')
    manifest=json.loads((raw/'manifest.json').read_text())
    if manifest['status']!='complete' or manifest['events_sha256']!=digest(events_path):
        raise ValueError('Require complete acquisition with matching event calendar')
    events=read_events(events_path)
    windows={}
    # Lexical timestamp bounds let us skip unrelated monthly rows efficiently.
    for event in events:
        start=(event.release-timedelta(seconds=65)).astimezone(EST)
        end=(event.release+timedelta(seconds=1800)).astimezone(EST)
        if start.date()!=end.date(): raise ValueError('Event window crosses fixed-EST date')
        windows[start.strftime('%Y%m%d')]=(start.strftime('%Y%m%d %H%M%S%f')[:-3],end.strftime('%Y%m%d %H%M%S%f')[:-3],event.event_id)
    points={}; audit=[]
    for entry in manifest['files']:
        path=raw/entry['file']
        if digest(path)!=entry['sha256']: raise ValueError('Raw hash mismatch')
        rows=retained=collisions=0; previous=''; event_counts={}; reversals=[]
        with zipfile.ZipFile(path) as archive:
            member=archive.getinfo(entry['csv_member'])
            if member.file_size>1_000_000_000: raise ValueError('Oversized member')
            with archive.open(member) as stream:
                for row in csv.reader(io.TextIOWrapper(stream,encoding='utf-8-sig')):
                    rows+=1
                    if len(row)!=4 or not re.fullmatch(r'\d{8} \d{9}',row[0]): raise ValueError('Invalid row shape')
                    if not row[0].startswith(entry['month']): raise ValueError('Archive month mismatch')
                    if row[0]<previous:
                        # A rollback outside all retained windows cannot affect
                        # event matching. Preserve evidence; fail if its interval
                        # intersects any event window rather than guessing a clock.
                        intersects=any(row[0]<=end and previous>=start for start,end,_ in windows.values())
                        if intersects: raise ValueError('Source clock reversal intersects an event window')
                        reversals.append(dict(previous=previous,current=row[0],scope='outside_all_event_windows'))
                    previous=row[0]
                    bounds=windows.get(row[0][:8])
                    if bounds is None or not bounds[0]<=row[0]<=bounds[1]: continue
                    stamp,bid,ask=parse_tick(row)
                    key=(entry['symbol'],stamp)
                    collisions+=key in points
                    points[key]=(bid,ask); retained+=1
                    event_counts[bounds[2]]=event_counts.get(bounds[2],0)+1
        audit.append(dict(file=entry['file'],sha256=entry['sha256'],scanned_rows=rows,retained_rows=retained,
                          equal_timestamp_overwrites=collisions,event_counts=event_counts,clock_reversals=reversals))
        print('Validated '+entry['file']+': '+str(retained)+' event-window rows',flush=True)
    if not points: raise ValueError('No event quotes')
    output.mkdir(parents=True)
    with (output/'quotes.csv').open('x',newline='') as stream:
        writer=csv.writer(stream); writer.writerow(['symbol','timestamp_utc','bid','ask'])
        for (symbol,stamp),(bid,ask) in sorted(points.items()): writer.writerow([symbol,stamp.isoformat(),bid,ask])
    coverage=[]
    for event in events:
        for symbol in ['EURUSD','GBPUSD']:
            times=sorted(t for s,t in points if s==symbol and event.release-timedelta(seconds=65)<=t<=event.release+timedelta(seconds=1800))
            coverage.append(dict(event_id=event.event_id,symbol=symbol,quotes=len(times),
                                 maximum_interquote_gap_seconds=max(((b-a).total_seconds() for a,b in zip(times,times[1:])),default=None)))
    result=dict(status='complete',provider='HistData Generic ASCII ticks',timezone='fixed UTC-05:00; no DST',
                events_sha256=digest(events_path),raw_manifest_sha256=digest(raw/'manifest.json'),
                quotes_sha256=digest(output/'quotes.csv'),code_sha256=digest(Path(__file__)),quotes=len(points),
                pre_seconds=65,post_seconds=1800,files=audit,coverage=coverage,
                validation_scope='Full archive schema/month/ZIP CRC and order audit; clock reversals touching event windows fail; retained prices and dates validated',
                limitation='Provider provenance and timestamps do not establish dissemination or receipt latency; gap exclusions remain active')
    (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
