# Research Track

## Working title

**Evidence-Calibrated Diagnosis and Verified Recovery for Windows Endpoint Failures**

This research track turns the project from a troubleshooting utility into a falsifiable systems/reliability study. Product behavior remains useful, but research claims must be supported by reproducible experiments rather than architecture alone.

## Research questions

**RQ1 — Diagnosis.** Does multi-signal, state-aware diagnosis improve root-cause attribution over naive reset, static rule, and single-signal baselines?

**RQ2 — Safety.** Can confidence-aware intervention policies reduce unsafe or unnecessary remediation while preserving acceptable recovery coverage?

**RQ3 — Verification.** Does post-action verification reduce false-success reports and repeated incidents compared with fire-and-forget remediation?

## Hypotheses

- **H1:** State-aware multi-signal diagnosis improves macro-F1 and root-cause accuracy over static and single-signal baselines.
- **H2:** Confidence gating plus abstention lowers unsafe-action and unnecessary-reset rates.
- **H3:** Closed-loop verification lowers false-success rate and recurrence after remediation.

## Required baselines

1. `naive_reset` — reset/re-pair-oriented recovery without diagnosis.
2. `rule_based_diagnosis` — deterministic symptom-to-cause rules.
3. `single_signal` — diagnosis using only one evidence family at a time.
4. `state_aware_diagnosis` — current structured pipeline.
5. Future model-based methods must be compared against the above, not only against previous model versions.

## Primary metrics

### Diagnosis
- macro precision / recall / F1
- root-cause top-1 accuracy
- state classification accuracy
- false-positive rate on healthy cases

### Safety and intervention
- unsafe action rate
- unnecessary reset rate
- abstention / human-review rate
- recovery success rate
- false-success rate

### Operational
- MTTD
- MTTR
- evidence collection latency
- recovery verification rate

For all proportion metrics, report numerator, denominator, point estimate, and a confidence interval. Do not publish fabricated or manually selected scores.

## Experimental unit

A benchmark case is one reproducible machine state with:

- scenario identifier
- ground-truth injected fault or healthy state
- evidence snapshot
- expected state
- expected root cause
- forbidden actions
- selected strategy
- planned action
- verification outcome
- timing metadata

## Dataset layers

1. **Synthetic cases** — hand-authored evidence objects for deterministic unit-level coverage.
2. **Controlled fault injection** — repeatable Windows faults with known ground truth.
3. **Real incidents** — opt-in session artifacts, sanitized before inclusion.

Results from these layers must be reported separately. Synthetic correctness is not evidence of real-world recovery performance.

## Ablations

At minimum, measure the impact of removing:

- state-machine context
- one evidence family (Bluetooth, PnP/MEDIA, endpoint, service)
- confidence gating / abstention
- post-remediation verification

## Failure taxonomy

Every benchmark failure should map to a stable category such as:

- ambiguous evidence
- missing telemetry
- conflicting signals
- privilege limitation
- race / enumeration delay
- external dependency
- correct diagnosis, inappropriate action
- correct action, verification failure
- unknown / out-of-distribution fault

## Claim discipline

Every headline claim in the README or paper must map to:

`claim -> benchmark -> metric -> raw result artifact -> reproduction command`

If a claim lacks this chain, label it as a design goal or hypothesis rather than a result.

## Research milestone

A credible first research release should include:

- at least 3 non-trivial baselines
- at least 10 distinct fault/healthy scenario classes
- repeated controlled trials
- aggregate metrics with confidence intervals
- at least 3 ablations
- error analysis and threats to validity
- one command that regenerates the benchmark summary from raw case records
