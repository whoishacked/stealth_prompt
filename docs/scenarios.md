# Scenario files and schema

A scenario is replayable assessment configuration, not evidence and not authority to
send. Current exports use schema version 3 and `kind: stealth_prompt_scenario`.

## Version behavior

- versions 1 and 2 remain readable and import with FrameFuzz disabled;
- version 3 adds the optional `framefuzz` object;
- a future or unknown version fails with a named version error;
- unknown fields, oversized files, and credential- or capture-shaped fields fail
  closed instead of being silently discarded.

An import never restores host permission, automatic-send authorization, binding
health, captured responses, verdicts, or session identifiers. The current document is
validated again before any mutation.

## FrameFuzz configuration

```json
{
  "schema_version": 3,
  "kind": "stealth_prompt_scenario",
  "name": "Synthetic framing comparison",
  "objective": "instruction_disclosure",
  "provider": "fake",
  "mode": "auto",
  "response_source": "page",
  "potential_finding_action": "continue",
  "limits": { "max_turns": 5, "max_duration_seconds": 0 },
  "sharing": "none",
  "target_origin": "http://127.0.0.1:8765",
  "binding": null,
  "scorers": [],
  "framefuzz": {
    "enabled": true,
    "strategies": [
      "clean_control",
      "explicit",
      "integrity_signature",
      "required_config",
      "trusted_destination"
    ],
    "template_pack": "framefuzz-core",
    "template_version": 1,
    "protected_value_label": "synthetic canary",
    "safe_destination": "https://audit.example.invalid/collect",
    "case_order": "randomized",
    "random_seed": "replace-with-a-local-seed",
    "require_context_reset": true
  }
}
```

Clean control and explicit are mandatory, and at least one framed strategy must be
present. Strategy names, template pack, and template version are closed values. The
protected-value field accepts a descriptive label only; the safe destination accepts
bounded HTTP(S) test data without credentials or executable schemes.

Case outcomes, transcripts, scorer results, and FrameFuzz conclusions belong in
`session.json`, not in a scenario. See [FrameFuzz](framefuzz.md) for the experiment and
[Reports and evidence](reports.md) for result semantics.
