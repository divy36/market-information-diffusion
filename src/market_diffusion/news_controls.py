"""Audit observed scheduled news; absence from one calendar is not proof of isolation.

All countries are retained conservatively because FX can react to global news.
Headline/core/yearly CPI rows are one US announcement bundle, not independent
events. The calendar is prospective printed evidence retrieved retrospectively.
"""
import json
import re
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .data import read_events
from .pipeline import digest


def calendar_rows(text, release):
    """Extract rows with explicit country, day and Eastern clock; preserve labels."""
    local = release.astimezone(ZoneInfo('America/New_York'))
    output = []
    for line in text.splitlines():
        match = re.search(r'\b([A-Z]{2})\s+(\d{2}[-/]\d{2})\s+(\d{2}:\d{2})\s+(.+)', line)
        if not match:
            continue
        country, day, clock, label = match.groups()
        if day.replace('/', '-') != local.strftime('%m-%d'):
            continue
        hour, minute = map(int, clock.split(':'))
        stamp = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        offset = (stamp - local).total_seconds()
        bundle = country == 'US' and label.startswith('CPI ')
        output.append(dict(country=country, clock_eastern=clock, offset_seconds=offset,
                           label=label, us_cpi_bundle=bundle))
    return output


def audit_news(events_path, pdf_directory, output):
    """Export a checkable calendar and prespecified primary/extended exclusion sets."""
    import pdfplumber
    records = []
    for event in read_events(events_path):
        day = event.release.date().isoformat()
        names = ['dailypoints'+day.replace('-','')+'.pdf', 'dailypoints_'+day+'.pdf']
        pdf = next((pdf_directory/name for name in names if (pdf_directory/name).exists()), None)
        if pdf is None:
            raise ValueError('Missing consensus calendar for '+event.event_id)
        with pdfplumber.open(pdf) as doc:
            rows = calendar_rows(doc.pages[0].extract_text() or '', event.release)
        if not any(row['us_cpi_bundle'] and row['offset_seconds']==0 for row in rows):
            raise ValueError('Calendar extraction did not recover the CPI event')
        other = [row for row in rows if not row['us_cpi_bundle']]
        records.append(dict(event_id=event.event_id, source_file=str(pdf.resolve()),
                            sha256=digest(pdf), rows=rows,
                            other_news_primary=any(-60 <= r['offset_seconds'] <= 300 for r in other),
                            other_news_extended=any(-60 <= r['offset_seconds'] <= 1800 for r in other)))
    result = dict(events_sha256=digest(events_path), records=records,
                  limitation='One printed calendar, not an exhaustive news feed. Unscheduled news remains uncontrolled.')
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
    return result
