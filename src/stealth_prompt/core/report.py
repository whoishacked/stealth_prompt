# ruff: noqa: E501 -- keeping the embedded report template readable is safer than splitting markup
"""Self-contained, script-free HTML evidence reports.

Every value in a session document may ultimately contain hostile model or target
text. Rendering therefore uses text escaping at the final boundary and ships no
JavaScript, external font, image, or stylesheet.
"""

from __future__ import annotations

from html import escape
from typing import Any


def _text(value: object, *, fallback: str = "—") -> str:
    if value is None or value == "":
        return fallback
    return escape(str(value), quote=True)


def _label(value: object) -> str:
    return _text(value).replace("_", " ").title()


def _list(items: object, *, empty: str = "None recorded") -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="muted">{escape(empty)}</p>'
    return "<ul>" + "".join(f"<li>{_text(item)}</li>" for item in items) + "</ul>"


def _code(value: object, *, empty: str = "Not retained") -> str:
    rendered = _text(value, fallback=empty)
    return f'<pre class="evidence">{rendered}</pre>'


#: Status wording that keeps "we checked and found nothing" separate from "we
#: never checked". Collapsing the two would overstate a `not_detected` verdict.
_SCORER_STATUS = {
    "confirmed": ("critical", "Matched"),
    "not_detected": ("success", "No match"),
    "inconclusive": ("warning", "Not applicable"),
    "error": ("warning", "Failed"),
    "cancelled": ("neutral", "Cancelled"),
}


def _scorers(results: object) -> str:
    """Render the per-scorer provenance for one turn.

    Every configured scorer appears, not only the matches, so the reader can
    see the whole basis for the verdict.
    """
    if not isinstance(results, list) or not results:
        return '<p class="muted">No deterministic scorer was configured for this turn.</p>'
    rows: list[str] = []
    for raw in results:
        record = raw if isinstance(raw, dict) else {}
        status = str(record.get("status", "inconclusive"))
        badge, wording = _SCORER_STATUS.get(status, ("neutral", status))
        rows.append(
            "<tr>"
            f"<td>{_text(record.get('scorer_id'))}</td>"
            f"<td>{_label(record.get('scorer_type'))}</td>"
            f'<td><span class="badge {badge}">{escape(wording)}</span></td>'
            f"<td>{'Yes' if record.get('deterministic') else 'No'}</td>"
            f"<td>{_text(record.get('preview'), fallback='—')}</td>"
            f"<td class=\"hash\">{_text(record.get('match_sha256'), fallback='—')}</td>"
            f"<td>{_text(record.get('reason'), fallback='—')}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Scorer</th><th>Type</th><th>Result</th>"
        "<th>Deterministic</th><th>Evidence</th><th>SHA-256</th><th>Reason</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def render_report(document: dict[str, Any]) -> str:
    """Render one exported assistant session as a portable HTML report."""
    configuration = document.get("configuration")
    if not isinstance(configuration, dict):
        configuration = {}
    scenario = configuration.get("scenario")
    if not isinstance(scenario, dict):
        scenario = {}
    turns = document.get("turns")
    if not isinstance(turns, list):
        turns = []
    timeline = document.get("timeline")
    events = timeline.get("events", []) if isinstance(timeline, dict) else []
    if not isinstance(events, list):
        events = []

    verdict = str(document.get("verdict", "inconclusive"))
    verdict_class = {
        "confirmed": "critical",
        "potential": "warning",
        "not_observed": "success",
    }.get(verdict, "neutral")

    turn_sections: list[str] = []
    for index, raw_turn in enumerate(turns, start=1):
        turn = raw_turn if isinstance(raw_turn, dict) else {}
        proposal = turn.get("proposal")
        proposal = proposal if isinstance(proposal, dict) else {}
        evaluation = turn.get("evaluation")
        evaluation = evaluation if isinstance(evaluation, dict) else {}
        turn_sections.append(
            f"""
            <article class="turn">
              <div class="turn-heading">
                <h3>Turn {index}</h3>
                <span class="badge neutral">{_label(evaluation.get("verdict", "not evaluated"))}</span>
              </div>
              <dl class="facts compact">
                <div><dt>Goal</dt><dd>{_text(proposal.get("goal"))}</dd></div>
                <div><dt>Tactic</dt><dd>{_text(proposal.get("tactic"))}</dd></div>
                <div><dt>Hypothesis</dt><dd>{_text(proposal.get("hypothesis"))}</dd></div>
                <div><dt>Risk</dt><dd>{_label(proposal.get("risk"))}</dd></div>
                <div><dt>Approved</dt><dd>{"Yes" if turn.get("approved") else "No"}</dd></div>
                <div><dt>Deterministic</dt><dd>{"Yes" if evaluation.get("deterministic") else "No"}</dd></div>
              </dl>
              <h4>Payload</h4>
              {_code(turn.get("approved_payload") or proposal.get("payload"))}
              <p class="hash">SHA-256: {_text(turn.get("approved_payload_sha256"))}</p>
              <h4>Target response</h4>
              {_code(turn.get("response"))}
              <h4>Assessment</h4>
              <p>{_text(evaluation.get("summary"), fallback="Not evaluated")}</p>
              <h4>Scorer provenance</h4>
              {_scorers(turn.get("scorers"))}
              <div class="columns">
                <div><h4>Observed signals</h4>{_list(evaluation.get("observed_signals"))}</div>
                <div><h4>Suggested next steps</h4>{_list(evaluation.get("suggested_next_steps"))}</div>
              </div>
            </article>
            """
        )

    event_rows: list[str] = []
    for raw_event in events:
        event = raw_event if isinstance(raw_event, dict) else {}
        metadata = event.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        detail = metadata.get("detail") or metadata.get("summary") or ""
        event_rows.append(
            "<tr>"
            f"<td>{_text(event.get('at'))}</td>"
            f"<td>{_text(event.get('kind'))}</td>"
            f"<td>{_text(event.get('source'))}</td>"
            f"<td>{_text(detail)}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
  <title>Stealth Prompt security report</title>
  <style>
    :root {{ color-scheme: light; --ink:#111827; --muted:#64748b; --line:#dbe3ef;
      --surface:#fff; --canvas:#f4f7fb; --accent:#6d5dfc; --critical:#be123c;
      --warning:#a16207; --success:#047857; }}
    * {{ box-sizing:border-box }}
    body {{ margin:0; background:var(--canvas); color:var(--ink); font:14px/1.55
      Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif }}
    main {{ max-width:1040px; margin:0 auto; padding:48px 28px 80px }}
    header {{ color:white; padding:34px; border-radius:20px;
      background:linear-gradient(135deg,#111827,#312e81 72%,#6d5dfc) }}
    .eyebrow {{ margin:0 0 8px; color:#c4b5fd; font-size:12px; font-weight:700;
      letter-spacing:.12em; text-transform:uppercase }}
    h1 {{ margin:0; font-size:32px }} h2 {{ margin:0 0 18px; font-size:20px }}
    h3 {{ margin:0; font-size:17px }} h4 {{ margin:20px 0 8px; font-size:12px;
      letter-spacing:.06em; text-transform:uppercase; color:#475569 }}
    .meta {{ margin:8px 0 0; color:#dbeafe }}
    section,.turn {{ margin-top:20px; padding:24px; background:var(--surface);
      border:1px solid var(--line); border-radius:16px; box-shadow:0 8px 24px #0f172a0a }}
    .verdict {{ display:flex; align-items:center; justify-content:space-between; gap:16px }}
    .badge {{ display:inline-flex; padding:5px 10px; border-radius:999px; font-size:12px;
      font-weight:750; letter-spacing:.02em }}
    .badge.critical {{ background:#ffe4e6; color:var(--critical) }}
    .badge.warning {{ background:#fef3c7; color:var(--warning) }}
    .badge.success {{ background:#d1fae5; color:var(--success) }}
    .badge.neutral {{ background:#e2e8f0; color:#334155 }}
    .facts {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px }}
    .facts.compact {{ grid-template-columns:repeat(2,minmax(0,1fr)); margin-top:16px }}
    .facts div {{ padding:12px; background:#f8fafc; border-radius:10px }}
    dt {{ color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase }}
    dd {{ margin:4px 0 0; overflow-wrap:anywhere }}
    .turn-heading,.columns {{ display:flex; justify-content:space-between; gap:20px }}
    .columns>div {{ flex:1 }}
    .evidence {{ margin:0; padding:14px; border:1px solid #dbe3ef; border-radius:10px;
      background:#0f172a; color:#e2e8f0; font:12px/1.55 ui-monospace,SFMono-Regular,monospace;
      white-space:pre-wrap; overflow-wrap:anywhere }}
    .hash,.muted {{ color:var(--muted); font-size:12px }}
    table {{ width:100%; border-collapse:collapse; font-size:12px }}
    th,td {{ padding:9px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top }}
    th {{ color:var(--muted); text-transform:uppercase; letter-spacing:.04em }}
    footer {{ margin-top:26px; color:var(--muted); text-align:center; font-size:12px }}
    @media(max-width:700px) {{ main{{padding:20px 12px}} .facts,.facts.compact{{grid-template-columns:1fr}}
      .columns,.turn-heading,.verdict{{display:block}} .badge{{margin-top:8px}} }}
    @media print {{ body{{background:white}} main{{max-width:none;padding:0}} section,.turn{{break-inside:avoid;box-shadow:none}} }}
  </style>
</head>
<body>
<main>
  <header>
    <p class="eyebrow">Authorized AI security assessment</p>
    <h1>Stealth Prompt report</h1>
    <p class="meta">Session {_text(document.get("session_id"))} · exported {_text(document.get("exported_at"))}</p>
  </header>
  <section class="verdict">
    <div><h2>Assessment outcome</h2><p class="muted">A confirmed result requires deterministic evidence or explicit operator verification.</p></div>
    <span class="badge {verdict_class}">{_label(verdict)}</span>
  </section>
  <section>
    <h2>Scope and configuration</h2>
    <dl class="facts">
      <div><dt>Target origin</dt><dd>{_text(configuration.get("origin"))}</dd></div>
      <div><dt>Objective</dt><dd>{_label(configuration.get("objective"))}</dd></div>
      <div><dt>Mode</dt><dd>{_label(configuration.get("mode"))}</dd></div>
      <div><dt>Potential finding</dt><dd>{_label(configuration.get("potential_finding_action"))}</dd></div>
      <div><dt>Provider</dt><dd>{_text(configuration.get("provider_label") or configuration.get("provider"))}</dd></div>
      <div><dt>Effective model</dt><dd>{_text(configuration.get("effective_model"))}</dd></div>
      <div><dt>Data sharing</dt><dd>{_label(configuration.get("sharing"))}</dd></div>
      <div><dt>Response source</dt><dd>{_label(configuration.get("response_source"))}</dd></div>
      <div><dt>Turns</dt><dd>{_text(configuration.get("turns"))} / {_text(configuration.get("max_turns") or "Unlimited")}</dd></div>
      <div><dt>Deterministic oracles</dt><dd>{_text(configuration.get("oracles"), fallback="0")}</dd></div>
    </dl>
    <h4>Authorized objective</h4>
    <p>{_text(configuration.get("objective_text"))}</p>
    <h4>Standards mapping</h4>
    {_list(scenario.get("standards"), empty="No mapping recorded")}
    <h4>Recommended controls</h4>
    {_list(scenario.get("remediation"), empty="No remediation recorded")}
    <h4>Interaction binding</h4>
    <p>{_text(configuration.get("binding_summary"))}</p>
  </section>
  <section>
    <h2>Attack chain</h2>
    {"".join(turn_sections) if turn_sections else '<p class="muted">No turns were recorded.</p>'}
  </section>
  <section>
    <h2>Evidence timeline</h2>
    <table><thead><tr><th>Time</th><th>Event</th><th>Source</th><th>Detail</th></tr></thead>
      <tbody>{"".join(event_rows) if event_rows else '<tr><td colspan="4">No events recorded.</td></tr>'}</tbody>
    </table>
  </section>
  <footer>Generated locally by Stealth Prompt. Review target data handling before sharing this file.</footer>
</main>
</body>
</html>"""
