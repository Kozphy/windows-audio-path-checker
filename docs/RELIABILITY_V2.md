# Reliability Timeline & Evidence Store

Version 0.6 turns the checker from a point-in-time diagnostic into a small local reliability-observability tool.

## Architecture

```text
Windows audio collectors
        |
        v
Deterministic findings
        |
        v
Root-cause inference
        |
        +--> point-in-time JSON report
        |
        v
Timeline sampler --> semantic state diff --> transition events
        |                                      |
        +------------------+-------------------+
                           v
                      SQLite history
                           |
                           v
                  reliability summary
```

The timeline deliberately compares compact semantic state instead of raw JSON. This reduces noise from fields that change without representing a meaningful audio-path transition.

## Record an incident

Run a 60-second observation with a five-second sampling interval:

```powershell
.\.venv\Scripts\python -m audio_path_checker --no-gui --timeline 60 --interval 5 --root-causes
```

Persist the incident evidence locally:

```powershell
.\.venv\Scripts\python -m audio_path_checker --no-gui --timeline 60 --interval 5 --database audio-history.db --history-summary
```

Save both the final scan and timeline in a JSON report:

```powershell
.\.venv\Scripts\python -m audio_path_checker --no-gui --timeline 60 --interval 5 --database audio-history.db --report incident.json
```

## State transitions

The recorder detects events such as:

- default endpoint changed
- master mute or volume changed
- application default output changed
- Bluetooth endpoint presence changed
- a diagnostic finding opened
- a diagnostic finding resolved
- browser audio session state, volume, mute, or routing changed

Every sample receives a SHA-256 state fingerprint so repeated states can be identified without treating the full raw snapshot as the comparison key.

## Reliability metrics

A timeline reports:

- sample count
- number of unique semantic states
- transition count
- state-change rate
- critical-sample ratio

The SQLite history summary additionally reports historical scan count, critical-scan ratio, transition count, and the most frequently ranked root causes.

These metrics are diagnostic/operational signals, not formal service-level objectives. They are intended to make incident evidence reproducible and comparable across troubleshooting sessions.

## Local evidence schema

SQLite uses two tables:

- `scans`: timestamp, state fingerprint, top root cause, confidence, critical flag, and full JSON snapshot
- `transitions`: timestamp, event type, field/finding code, before value, and after value

The database is local and uses SQLite WAL mode. No telemetry or cloud service is required.

## Development verification

```powershell
python -m unittest discover -s tests -v
python -m compileall -q audio_path_checker
```

The timeline and storage layers use only the Python standard library, so their unit tests are platform-independent even though live Windows audio collection remains Windows-specific.
