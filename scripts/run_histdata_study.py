"""Execute the frozen 2022-2025 study offline from audited local inputs.

Use --freeze-only before fitting; repeat without that option to evaluate.
Choose a new output directory for each reproduction. Existing runs are immutable.
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from market_diffusion.alignment import Config
from market_diffusion.holdout import freeze, evaluate, code_hashes
from market_diffusion.model_checks import compare_models
from market_diffusion.pipeline import run, digest
from market_diffusion.sensitivity import run_sensitivity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--freeze-only',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    events=root/'data/processed/cpi-2022-2025-headline-v1/events.csv'
    quotes=root/'data/interim/histdata/cpi-2022-2025-v1/quotes.csv'
    news=root/'data/interim/consensus-2022-2025-v1/news_audit_v2.json'
    plan=root/'config/expanded-sensitivity-v1.json'
    output=args.output.resolve()
    protocol=output/'protocol.json'
    if not output.exists():
        output.mkdir(parents=True)
        freeze(events,root/'config/default.json','2025-01-01T00:00:00Z',protocol,'supplied')
        # Freeze sensitivity choices and calendar exclusions too, not only baseline.
        spec={'created_utc':datetime.now(timezone.utc).isoformat(),
              'inputs':{str(p):digest(p) for p in [events,news,plan,Path(__file__).resolve()]},
              'code_sha256':code_hashes(),
              'interpretation':'HistData-only source revision. One January 2022 sample inspected for ingestion. 2024 was inspected using Dukascopy. 2022-2024 development; 2025 later-period evaluation with unchanged model settings. Descriptive, not a trading backtest.'}
        (output/'study-freeze.json').write_text(json.dumps(spec,indent=2)+'\n')
    if args.freeze_only:
        print('Study choices frozen at '+str(output),flush=True)
        return
    spec=json.loads((output/'study-freeze.json').read_text())
    if spec['code_sha256']!=code_hashes() or any(digest(Path(p))!=h for p,h in spec['inputs'].items()):
        raise ValueError('Frozen study inputs or code changed')
    completion=output/'completion.json'
    if completion.exists(): raise ValueError('Choose a new output for reproduction')
    status={'status':'running','quotes_sha256':digest(quotes)}
    completion.write_text(json.dumps(status,indent=2))
    try:
        for phase in ['development','holdout']:
            print('Evaluating '+phase,flush=True)
            evaluate(events,quotes,protocol,phase,output/phase,output/'development' if phase=='holdout' else None)
        print('Running primary and sensitivity analyses',flush=True)
        primary=run(events,quotes,output/'primary',Config(),'supplied')
        run_sensitivity(events,quotes,plan,output/'sensitivity','supplied')
        compare_models(output/'primary/analysis.json',output/'model-checks')
        # Keep the full event file for prior-only history while selecting dates.
        audit=json.loads(news.read_text())
        cohorts={}
        for name,field in [('no-listed-news-5m','other_news_primary'),('no-listed-news-30m','other_news_extended')]:
            ids=[r['event_id'] for r in audit['records'] if not r[field]]
            if ids:
                config=Config(post_seconds=1800) if name.endswith('30m') else Config()
                result=run(events,quotes,output/name,config,'supplied',event_ids=ids)
                cohorts[name]={'events':len(ids),'counts':result['counts'],'summary':result['summary']}
            else: cohorts[name]={'events':0,'status':'no_eligible_events'}
        annual={year:dict(Counter(r['status'] for r in primary['results'] if r['release_utc'].startswith(year)))
                for year in ['2022','2023','2024','2025']}
        status.update(status='complete',primary_counts=primary['counts'],annual_counts=annual,news_cohorts=cohorts,
                      completed_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        status.update(status='failed',error_type=type(error).__name__)
        raise
    finally:
        completion.write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps(status,indent=2))

if __name__=='__main__': main()
