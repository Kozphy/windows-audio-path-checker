# Benchmark Protocol

## Objective

Evaluate diagnosis quality, remediation safety, and verification quality under known Windows audio/Bluetooth failure states without conflating synthetic tests, controlled experiments, and real incidents.

## Procedure

For each scenario and repetition:

1. Establish or verify a clean baseline state.
2. Inject exactly one declared fault unless the scenario is explicitly multi-fault.
3. Record the injection timestamp and ground-truth label.
4. Collect evidence using the normal diagnostic path.
5. Run one selected strategy without access to the ground-truth label.
6. Record predicted state, ranked cause, confidence if available, and planned action.
7. Enforce forbidden-action policy before destructive execution.
8. If execution is enabled, perform the action and record exit/exception details.
9. Re-collect independent evidence and verify actual recovery state.
10. Restore the clean state before the next trial.

## Leakage controls

- Ground-truth labels must not be passed into the diagnosis provider.
- Strategy selection must be fixed before viewing trial outcomes.
- Healthy cases must be included when estimating false-positive and unnecessary-reset rates.
- Failed or timed-out trials remain in the dataset unless a documented infrastructure exclusion applies.
- Exclusion reasons must be counted and reported.

## Repetition

Use repeated trials because Windows enumeration and service timing are stochastic. Report the number of trials per scenario and retain raw case records.

## Recommended first scenario matrix

| Class | Example | Ground truth |
|---|---|---|
| Healthy | connected, endpoint active | `AUDIO_PATH_HEALTHY` |
| Adapter unavailable | radio disabled/missing | `RADIO_UNAVAILABLE` |
| Not paired | target known but unpaired | `DEVICE_NOT_PAIRED` |
| Paired, disconnected | pairing exists, no active connection | `PAIRED_NOT_CONNECTED` |
| Connected, no A2DP | transport/profile absent | `CONNECTED_NO_A2DP` |
| A2DP, no MEDIA | service path incomplete | `A2DP_NO_MEDIA_NODE` |
| MEDIA, no endpoint | endpoint enumeration failure | `MEDIA_NO_ENDPOINT` |
| Endpoint disabled | endpoint present but disabled | `ENDPOINT_DISABLED` |
| Wrong default | healthy endpoint not default | `ENDPOINT_NOT_DEFAULT` |
| Audio service failure | Windows audio service stopped/broken | `AUDIO_SERVICE_FAILURE` |

## Raw record contract

Each JSONL row should contain at least:

```json
{
  "trial_id": "uuid",
  "scenario_id": "media-no-endpoint",
  "repetition": 1,
  "strategy": "state_aware_diagnosis",
  "ground_truth_state": "MEDIA_NO_ENDPOINT",
  "ground_truth_cause": "audio_endpoint_enumeration_failure",
  "predicted_state": "MEDIA_NO_ENDPOINT",
  "predicted_cause": "audio_endpoint_enumeration_failure",
  "planned_action": "refresh_audio_endpoint_inventory",
  "forbidden_actions": ["remove_pairing"],
  "unsafe_action": false,
  "recovery_attempted": false,
  "recovery_verified": null,
  "mttd_ms": 0,
  "mttr_ms": null,
  "excluded": false,
  "exclusion_reason": null
}
```

## Reporting

Report raw counts alongside rates. At minimum publish:

- number of scenarios and trials
- excluded trials and reasons
- state accuracy
- root-cause accuracy
- unsafe-action rate
- unnecessary-reset rate
- recovery success and false-success rate when repair is enabled
- confidence intervals for all proportions
- per-scenario confusion/error table

Do not combine synthetic, controlled, and real-incident scores into one headline metric.
