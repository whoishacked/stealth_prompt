# Reports and evidence

Reports answer three separate questions: what was configured, what happened, and why a
verdict was assigned.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| Confirmed | A deterministic scorer matched, or the operator explicitly confirmed |
| Potential | The evaluator observed a relevant signal without deterministic proof |
| Not detected | Configured checks ran without finding the expected signal |
| Inconclusive | The run ended without enough reliable evidence for another verdict |

A model assessment alone is capped at **Potential**.

## Local Core reports

Each exported run has its own artifact directory containing:

- `session.json` — structured configuration, turns, evaluations, scorer results, and hashes;
- `report.html` — a self-contained human-readable report;
- `scenario.json` — replayable setup without captured evidence or credentials.

The Reports workspace lists the artifact store on disk and opens JSON results as bounded,
text-only data. HTML is downloaded rather than injected into the extension because it can
contain hostile target output.

## Direct API reports

Direct mode stores up to 50 bounded reports in extension-owned IndexedDB in the current
Chrome profile. Reports can be opened, downloaded as JSON, and deleted individually. The
provider key is never included. Removing the extension removes this browser-local history.

## What to review

For every material turn, check:

1. the objective, tactic, and hypothesis;
2. the exact payload that was approved or automatically authorized;
3. the captured target response;
4. observed signals and evaluator reasoning;
5. every deterministic scorer result, including not-applicable checks;
6. the terminal stop reason and configured limits.

Treat reports as potentially sensitive. They may contain selected target responses and
disclosed data. Keep them under the same retention and access policy as other penetration
testing evidence.
