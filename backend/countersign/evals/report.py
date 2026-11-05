"""The report in three forms: JSON (the source of truth, served at /api/evals/latest), Markdown (for review in
the repository) and a self-contained HTML page."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from countersign.evals.graders import CRITERIA

LABEL = {
    "task_completion": "Task completion",
    "groundedness": "Groundedness",
    "relevance": "Relevance",
    "tool_accuracy": "Tool accuracy",
    "safety": "Safety",
    "failure_handling": "Failure handling",
}


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _ci(ci: list[float]) -> str:
    return f"[{_pct(ci[0])}, {_pct(ci[1])}]"


def markdown(r: dict[str, Any]) -> str:
    lines = [
        "# Countersign evaluation report",
        "",
        f"> {r['honesty']}",
        "",
        f"- Model: `{r['model']['name']}` ({'simulated, offline' if r['model']['simulated'] else r['model']['provider']})",
        f"- Cases: {r['n_cases']}, trials per case: {r['trials']}, generated {r['generated_at']}",
        f"- Fingerprint: `{r['fingerprint']['combined'][:16]}` (dataset `{r['fingerprint']['dataset'][:8]}`, prompt "
        f"`{r['fingerprint']['prompt'][:8]}`, tools `{r['fingerprint']['tools'][:8]}`, policy `{r['fingerprint']['policy'][:8]}`, "
        f"graders `{r['fingerprint']['graders'][:8]}`)",
        f"- Runs: {r['runs']['total']} through the real runtime in {r['runs']['wall_seconds']} s "
        f"(p95 {r['runs']['p95_trial_ms']} ms per run)",
        "",
        "## Criteria",
        "",
        "| Criterion | Mean | 95% interval | Cases passing every trial | Wilson 95% |",
        "|---|---|---|---|---|",
    ]
    for c in CRITERIA:
        s = r["criteria"][c]
        lines.append(
            f"| {LABEL[c]} | {_pct(s['mean'])} | {_ci(s['ci95'])} | {s['cases_all_pass']}/{s['n_cases']} | {_ci(s['cases_all_pass_wilson'])} |"
        )
    lines += [
        "",
        "Intervals are over cases, the independent unit: a cluster bootstrap of per-case means (Wilson over cases "
        "where the bootstrap is degenerate at 0 or 1), and a Wilson interval on the share of cases that pass every trial.",
        "",
        "pass^k (all six criteria jointly, k trials in a row): "
        + ", ".join(f"k={k}: {_pct(v)}" for k, v in r["pass_hat_k"].items()),
        "",
        f"Minimum detectable effect: {r['mde']['discordant_flips']} one-directional case flips "
        f"({_pct(r['mde']['share_of_cases'] or 0)} of the dataset) for an exact two-sided McNemar test at alpha "
        f"{r['mde']['alpha']}.",
        "",
    ]
    m = r.get("mutation")
    if m:
        lines += [
            "## Evaluating the evaluator: six frozen mutants",
            "",
            f"Mutation score {m['killed']}/{m['total']}; grader false positives on the reference policy: "
            f"{m['reference_false_positives']}.",
            "",
            "| Mutant | Planted fault | Expected grader | Caught by | Cases flipped (expected grader) | p |",
            "|---|---|---|---|---|---|",
        ]
        for name, info in m["mutants"].items():
            g = info["expected_grader"]
            lines.append(
                f"| {name} | {info['description']} | {LABEL[g]} | {', '.join(LABEL[c] for c in info['caught_by']) or 'nothing'} "
                f"| {info['discordant_cases'][g]} | {info['p_value'][g]:.2g} |"
            )
        lines += ["", "What each expected grader said, first case it failed:", ""]
        for name, info in m["mutants"].items():
            ex = info["example"].get(info["expected_grader"], "")
            lines.append(f"- **{name}**: {ex}")
        lines.append("")
    cal = r.get("calibration")
    if cal:
        cov = cal["coverage"]
        lines += [
            "## Calibration with the seeded simulator",
            "",
            f"{cal['simulator']}; noise {cal['noise']}, {cal['trials']} trials per case.",
            "",
            f"- Task completion: true {_pct(cal['truth']['task_completion_mean'])}, measured "
            f"{_pct(cal['estimate']['task_completion_mean'])} {_ci(cal['estimate']['ci95'])}; covered: {cal['covered']} "
            f"(this draw's slip rate {_pct(cal['realized']['rate'])} against {_pct(cal['noise'])}, z = {cal['realized']['z']})",
            "- pass^k true vs estimated: "
            + ", ".join(
                f"k={k}: {_pct(cal['truth']['pass_hat_k'][k])} vs {_pct(cal['estimate']['pass_hat_k'][k])}"
                for k in cal["truth"]["pass_hat_k"]
            ),
            f"- Interval coverage over {cov['replicates']} simulated replicates (nominal {_pct(cov['nominal'])}, "
            f"Monte Carlo SE {_pct(cov['monte_carlo_se'])}): mean interval {_pct(cov['mean_interval_coverage'])}, "
            f"all-pass Wilson {_pct(cov['all_pass_wilson_coverage'])}",
            f"- The slips touch task completion only: other criteria unaffected = {cal['other_criteria_unaffected']}",
            "",
        ]
    b = r["baseline"]
    lines += [
        "## Baseline",
        "",
        (
            f"Compared {b['compared']} cases with `{b['path']}` (fingerprint `{str(b.get('fingerprint'))[:16]}`): "
            f"{len(b['regressions'])} regressions, {len(b['improvements'])} improvements. {b.get('method', '')}"
            if b["exists"]
            else "No baseline yet."
        ),
        "",
        "## Cases",
        "",
        "| Case | Request | Faults | Status | Proposed | Result |",
        "|---|---|---|---|---|---|",
    ]
    for c in r["cases"]:
        result = "all pass" if c["passed_all"] else "fails " + ", ".join(c["failures"])
        lines.append(
            f"| `{c['id']}` | {c['request']} | {', '.join(c['faults']) or '-'} | {', '.join(c['statuses'])} "
            f"| {c['run']['proposed']} | {result} |"
        )
    lines.append("")
    return "\n".join(lines)


_CSS = """
body{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:#0b0d10;color:#e6e8eb;margin:0;padding:32px}
main{max-width:1100px;margin:auto}h1{font-size:26px}h2{font-size:19px;margin-top:36px;border-bottom:1px solid #222;padding-bottom:6px}
.note{background:#15191e;border-left:3px solid #f5a524;padding:12px 16px;border-radius:6px}
table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border-bottom:1px solid #1f242b;padding:6px 8px;text-align:left;vertical-align:top}
th{color:#9aa4af;font-weight:600}code{font-family:ui-monospace,Menlo,monospace;font-size:13px;color:#b8c4d0}
.ok{color:#3ecf8e}.bad{color:#ff6b6b}.cell{text-align:center}.hit{background:#3b1d1d;color:#ffb3b3}.miss{color:#56606b}
"""


def page(r: dict[str, Any]) -> str:
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(LABEL[c])}</td><td>{_pct(s['mean'])}</td><td>{_ci(s['ci95'])}</td>"
        f"<td>{s['cases_all_pass']}/{s['n_cases']}</td><td>{_ci(s['cases_all_pass_wilson'])}</td></tr>"
        for c, s in r["criteria"].items()
    )
    matrix = ""
    m = r.get("mutation")
    if m:
        head = "".join(f"<th class=cell>{e(LABEL[c])}</th>" for c in CRITERIA)
        body = ""
        for name, info in m["mutants"].items():
            cells = "".join(
                f"<td class='cell {'hit' if c in info['caught_by'] else 'miss'}'>"
                f"{'caught ' + str(info['discordant_cases'][c]) if c in info['caught_by'] else '-'}"
                f"{' *' if c == info['expected_grader'] else ''}</td>"
                for c in CRITERIA
            )
            body += f"<tr><td><b>{e(name)}</b><br><small>{e(info['description'])}</small></td>{cells}</tr>"
        matrix = (
            f"<h2>Mutation matrix</h2><p>Score {m['killed']}/{m['total']}; reference false positives "
            f"{m['reference_false_positives']}. Cells count the cases a grader flipped; * marks the grader each "
            f"mutant was built to be caught by.</p><table><tr><th>Mutant</th>{head}</tr>{body}</table>"
        )
    cases = "".join(
        f"<tr><td><code>{e(c['id'])}</code></td><td>{e(c['request'])}</td><td>{e(', '.join(c['faults']) or '-')}</td>"
        f"<td>{e(', '.join(c['statuses']))}</td><td class={'ok' if c['passed_all'] else 'bad'}>"
        f"{'all pass' if c['passed_all'] else 'fails ' + e(', '.join(c['failures']))}</td></tr>"
        for c in r["cases"]
    )
    return (
        "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Countersign evaluation report</title><style>{_CSS}</style><main>"
        f"<h1>Countersign evaluation report</h1><p class=note>{e(r['honesty'])}</p>"
        f"<p>Model <code>{e(r['model']['name'])}</code>, {r['n_cases']} cases x {r['trials']} trials, fingerprint "
        f"<code>{e(r['fingerprint']['combined'][:16])}</code>, generated {e(r['generated_at'])}.</p>"
        f"<h2>Criteria</h2><table><tr><th>Criterion</th><th>Mean</th><th>95% interval</th><th>All-pass cases</th>"
        f"<th>Wilson 95%</th></tr>{rows}</table>"
        f"<p>pass^k: {e(', '.join(f'k={k}: {_pct(v)}' for k, v in r['pass_hat_k'].items()))}</p>"
        f"{matrix}<h2>Cases</h2><table><tr><th>Case</th><th>Request</th><th>Faults</th><th>Status</th><th>Result</th></tr>"
        f"{cases}</table></main></html>\n"
    )


def write(report: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    (out / "latest.md").write_text(markdown(report))
    (out / "latest.html").write_text(page(report))
