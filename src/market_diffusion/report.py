"""Portable HTML research report using local SVG charts; no web dependencies."""

from html import escape
from pathlib import Path


def number(value):
    return "—" if value is None else "%.2f" % value


def chart(result):
    curve = result["curve"]
    points = [point for point in curve if point["response"] is not None]
    if not points:
        return ""
    xmin, xmax = curve[0]["tau"], curve[-1]["tau"]
    ymin = min(-0.1, min(point["response"] for point in points))
    ymax = max(1.2, max(point["response"] for point in points))

    def x(value):
        return 55 + 610 * (value - xmin) / (xmax - xmin)

    def y(value):
        return 205 - 165 * (value - ymin) / (ymax - ymin)

    # Separate polylines at missing observations so gaps stay visible.
    segments, current = [], []
    for point in curve:
        if point["response"] is None:
            if current:
                segments.append(current)
                current = []
        else:
            current.append("%.2f,%.2f" % (x(point["tau"]), y(point["response"])))
    if current:
        segments.append(current)
    observed = "".join('<polyline points="%s" fill="none" stroke="#2563eb" stroke-width="1.5"/>' % " ".join(segment)
                       for segment in segments)
    fitted = " ".join("%.2f,%.2f" % (x(point["tau"]), y(point["fitted"]))
                      for point in curve if point["fitted"] is not None)
    axes = "".join('<line x1="55" x2="665" y1="%.2f" y2="%.2f" stroke="#ddd"/><text x="12" y="%.2f">%s</text>'
                   % (y(level), y(level), y(level) + 4, level) for level in (0, 0.5, 1))
    return ('<svg viewBox="0 0 710 245" role="img" aria-label="Normalized response and fitted curve">'
            + axes + observed + '<polyline points="%s" fill="none" stroke="#d97706" stroke-width="2"/>' % fitted
            + '<line x1="%.2f" x2="%.2f" y1="30" y2="205" stroke="#888" stroke-dasharray="4"/>' % (x(0), x(0))
            + '<text x="55" y="230">%ss</text><text x="625" y="230">%ss</text>' % (xmin, xmax)
            + '<text x="240" y="230">Seconds relative to announcement</text></svg>')


def render(path: Path, payload: dict) -> None:
    mode = "SIMULATED DEMONSTRATION" if payload["mode"] == "simulation" else "USER-SUPPLIED DATA — PROVENANCE NOT INDEPENDENTLY VERIFIED"
    summaries = ""
    for symbol, stats in payload["summary"].items():
        summaries += '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s – %s</td></tr>' % (
            escape(symbol), stats["n"], number(stats["median"]), number(stats["ci_low"]), number(stats["ci_high"]))
    pairs = "".join('<li>%s: median t50 difference %ss (95%% interval %s to %ss; %d paired events)</li>' % (
        escape(label), number(stats["median"]), number(stats["ci_low"]), number(stats["ci_high"]), stats["n"])
        for label, stats in payload["paired_comparisons"].items())
    cards = ""
    for result in payload["results"]:
        fitted = result["fit"] or {}
        flags = ", ".join(result["flags"]) or "No configured flags"
        cards += ('<details><summary>%s · %s — %s | t50 %ss</summary><p>%s</p>%s</details>' % (
            escape(result["event_id"]), escape(result["symbol"]), escape(result["status"]),
            number(fitted.get("t50_seconds")), escape(flags), chart(result)))
    html = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market information diffusion — research report</title><style>
body{font:16px/1.6 system-ui,sans-serif;color:#172033;background:#f5f7fb;margin:0}main{max-width:1050px;margin:40px auto;padding:28px}
h1{font-size:38px;line-height:1.15}h2{margin-top:32px}.badge{background:#fff0cd;padding:12px 18px;border-left:4px solid #c47d00}
table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #ddd}
details{background:white;margin:12px 0;padding:16px;border-radius:8px}summary{cursor:pointer;font-weight:600}svg{width:100%;max-width:850px}svg text{font-size:12px}
.muted{color:#596273}code{background:#e8ecf3;padding:2px 5px}</style><main>
<p class="muted">EVENT STUDIES / REPRODUCIBLE RESEARCH</p><h1>How quickly do prices adjust?</h1>
<p class="badge">MODE</p><p>Retrospective response estimates using a fixed post-event endpoint. These curves measure price adjustment, not directly observed information absorption. No causal attribution or trading profitability is established.</p>
<h2>Run overview</h2><p>COUNTS</p><h2>Median fitted t50 by instrument</h2>
<p>Seconds to 50% of the estimated endpoint move. Only accepted fits enter summaries. Intervals resample events and are conditional on the quality filters; they do not include endpoint or individual-fit uncertainty.</p>
<table><thead><tr><th>Instrument</th><th>Accepted events</th><th>Median t50 (s)</th><th>95% percentile interval</th></tr></thead><tbody>SUMMARIES</tbody></table>
<h2>Paired timing differences</h2><ul>PAIRS</ul><p>A negative A − B difference means A has a lower fitted t50. This comparison does not establish that A causes B to move.</p>
<h2>Event diagnostics</h2><p>Expand each event. Blue: observed response. Orange: fitted response. Dashed vertical line: announcement time. Excluded events have no fitted curve.</p>CARDS
<h2>Interpretation limits</h2><p>The model assumes monotone adjustment toward an estimated endpoint. Simultaneous news, changing liquidity, overshoots, drift, endpoint choice and timestamp errors can invalidate that assumption. The endpoint uses future data, so this report is not a forecasting backtest. The surprise variable is prepared for later analysis; this version does not estimate a causal surprise-response relationship.</p>
<p class="muted">Input hashes, configuration, Python version, code hashes and complete numerical results are saved alongside this report.</p></main></html>'''
    counts = payload["counts"]
    # Replace placeholders in one pass; user-supplied labels cannot become markup.
    replacements = {"MODE": escape(mode), "COUNTS": escape(str(counts)),
                    "SUMMARIES": summaries, "PAIRS": pairs, "CARDS": cards}
    import re
    html = re.sub(r"MODE|COUNTS|SUMMARIES|PAIRS|CARDS", lambda match: replacements[match.group()], html)
    path.write_text(html, encoding="utf-8")
