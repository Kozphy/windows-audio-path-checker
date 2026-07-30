# Diagnostic architecture

Version 0.3 introduces the first platform-oriented layer above the Windows audio collectors.
The existing deterministic checks remain the source of truth; the new engine converts their
output into explicit evidence and ranked root-cause hypotheses.

## Data flow

```text
Windows services / Core Audio / WASAPI
                |
                v
         read-only snapshot
                |
                v
      deterministic findings
                |
                v
       normalized evidence
                |
                v
 ranked root-cause hypotheses
                |
                v
 JSON report / compact explanation
```

## Design principles

1. **Deterministic before probabilistic** — confidence scores rank known rules; they do not
   replace evidence collection.
2. **Traceable conclusions** — every hypothesis carries evidence IDs so a reviewer can inspect
   the facts behind the recommendation.
3. **Safe by default** — collection is read-only. Existing remediation remains narrowly scoped
   to recognized browser sessions.
4. **Schema-first reports** — evidence and hypotheses are JSON-serializable and suitable for
   future timelines, replay, fleet aggregation, and dashboards.
5. **Platform-independent analysis** — the ranking engine can be tested on Linux CI using saved
   Windows snapshots.

## Report additions

CLI-generated reports now include a `diagnosis` object:

```json
{
  "engine_version": 1,
  "evidence": [],
  "hypotheses": [],
  "primary_hypothesis": {},
  "summary": {
    "evidence_count": 8,
    "hypothesis_count": 2,
    "critical_hypothesis_count": 1,
    "scan_complete": true
  }
}
```

A hypothesis contains:

- stable root-cause code
- title and severity
- confidence score from 0 to 1
- human-readable explanation
- recommended next action
- supporting evidence IDs
- contradicting evidence IDs

## CLI usage

Create the full machine-readable report:

```powershell
.\.venv\Scripts\python -m audio_path_checker --no-gui --report audio-report.json
```

Print only the ranked explanation:

```powershell
.\.venv\Scripts\python -m audio_path_checker --explain
```

## Next platform milestones

The current architecture intentionally prepares for these increments without pretending they
already exist:

- append-only incident timeline and snapshot diffing
- replay of saved reports through newer rule versions
- collector/plugin interfaces for Bluetooth, USB, and event logs
- policy-gated remediation with preview, approval, execution, and verification
- fleet aggregation with privacy-preserving device identifiers
- ETW and Windows Event Log correlation
