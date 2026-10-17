"""Rebuild report figures from saved HistData results; never refit the model."""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    study = root / 'results/histdata-expanded-v2'
    inputs = [study / 'primary/analysis.json', study / 'sensitivity/summary.json']
    analysis, sensitivity = [json.loads(p.read_text()) for p in inputs]
    args.output.mkdir(parents=True, exist_ok=False)
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})

    def save(fig, name):
        # Both formats are standalone: PNG for reports and SVG for resizing.
        for ext in ('png', 'svg'):
            fig.savefig(args.output / (name + '.' + ext), dpi=180, bbox_inches='tight')
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.5), layout='constrained')
    years = ['2022', '2023', '2024', '2025']
    bottom = np.zeros(4)
    for status, color in [('accepted', '#167d8d'), ('review', '#e5a23b'), ('excluded', '#c8ced6')]:
        counts = [sum(r['status'] == status and r['release_utc'].startswith(y) for r in analysis['results']) for y in years]
        ax.bar(years, counts, bottom=bottom, label=status.title(), color=color)
        for i, n in enumerate(counts):
            if n: ax.text(i, bottom[i] + n/2, str(n), ha='center', va='center')
        bottom += counts
    ax.set(title='Primary screening: 5 accepted of 70 currency responses', ylabel='Event / currency responses', ylim=(0, 23))
    ax.legend(ncols=3, loc='upper center')
    ax.set_xlabel('Release year — 2025 is the later-period evaluation sample')
    save(fig, 'screening')

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), layout='constrained')
    for symbol, color in [('EURUSD', '#167d8d'), ('GBPUSD', '#a74464')]:
        scenarios = sensitivity['symbols'][symbol]['scenarios']
        names = list(scenarios)
        y = np.arange(len(names))
        axes[0].plot([s['counts'].get('accepted', 0) for s in scenarios.values()], y, 'o-', label=symbol, color=color)
        axes[1].plot([s['common_t50']['median'] for s in scenarios.values()], y, 'o-', label=symbol, color=color)
    for ax in axes:
        ax.set_yticks(y, [n.replace('_', ' ') for n in names]); ax.invert_yaxis(); ax.grid(axis='x', alpha=.2)
    axes[0].set(title='Accepted responses out of 35 per currency', xlabel='Count', xlim=(0, 6))
    axes[1].set(title='Same-event comparison: only n=1 per currency', xlabel='Fitted t50 (seconds)')
    axes[0].legend(); fig.suptitle('Sensitivity to recorded analysis choices; no robust speed ranking')
    save(fig, 'sensitivity')

    fig, axes = plt.subplots(2, 1, figsize=(10, 7), layout='constrained')
    for ax, status in zip(axes, ['accepted', 'review']):
        # Deterministic first chronological example avoids choosing the prettiest fit.
        r = sorted((r for r in analysis['results'] if r['status'] == status), key=lambda r:(r['release_utc'], r['symbol']))[0]
        curve = [p for p in r['curve'] if p['tau'] >= 0]
        ax.plot([p['tau'] for p in curve], [np.nan if p['response'] is None else p['response'] for p in curve], label='Observed normalized response', color='#167d8d')
        ax.plot([p['tau'] for p in curve], [np.nan if p['fitted'] is None else p['fitted'] for p in curve], label='Jump + exponential fit', color='#a74464')
        ax.axhline(1, color='gray', ls=':', lw=1)
        ax.set(title=f"{r['event_id']} / {r['symbol']} — {status}", ylabel='Normalized response D', xlabel='Seconds after release')
        ax.legend(fontsize=8)
    fig.suptitle('First chronological example in each status; responses are not clipped')
    save(fig, 'responses')
    manifest = {'inputs_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                'selection': 'First chronological response by status, breaking ties by symbol',
                'artifacts': sorted(p.name for p in args.output.iterdir())}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
