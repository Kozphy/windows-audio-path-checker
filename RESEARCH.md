# Research-grade evaluation track

This repository now has an explicit research track for evaluating Windows endpoint diagnosis and recovery as a falsifiable systems/reliability problem rather than only as a troubleshooting utility.

Start here:

- [Research questions, hypotheses, metrics, and ablations](research/README.md)
- [Controlled benchmark protocol](research/benchmark_protocol.md)
- `audio_path_checker/evaluation/harness.py` for aggregate metrics and Wilson confidence intervals
- `tests/test_evaluation_harness.py` for evaluator correctness

## Working research claim

> State-aware, multi-signal diagnosis with confidence-aware intervention and post-action verification can improve root-cause attribution and reduce unsafe or unnecessary remediation compared with naive reset and simpler diagnostic baselines.

This is a **hypothesis until benchmark evidence exists**. The project deliberately does not publish fabricated scores.

## Next implementation milestones

1. Add a JSONL benchmark runner around `evaluate_case` and `aggregate_cases`.
2. Implement fixed baseline adapters: naive reset, static rule, single-signal, state-aware.
3. Add controlled fault-injection scenarios with clean-state restoration.
4. Produce per-scenario confusion/error tables and aggregate confidence intervals.
5. Add ablations for state context, evidence families, confidence gating, and verification.
6. Generate a machine-reproducible result bundle and paper-style report.

## Research standard

Every result-level claim should eventually be traceable as:

`claim -> benchmark -> metric -> raw artifact -> reproduction command`
