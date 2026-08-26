from __future__ import annotations

import unittest

from audio_path_checker.audit import create_record, verify_chain
from audio_path_checker.decision import build_decision
from audio_path_checker.policy import evaluate_policy
from audio_path_checker.risk import assess_root_cause
from audio_path_checker.verification import verify_remediation


class DecisionGovernanceTests(unittest.TestCase):
    def test_high_confidence_service_failure_becomes_high_risk(self) -> None:
        risk = assess_root_cause(
            {
                "code": "windows-audio-service",
                "probability": 0.98,
            }
        )
        self.assertIn(risk.level, {"high", "critical"})
        self.assertGreaterEqual(risk.score, 0.45)

    def test_high_risk_requires_approval(self) -> None:
        decision = evaluate_policy({"level": "high", "score": 0.6})
        self.assertEqual(decision.decision, "require_approval")
        self.assertTrue(decision.requires_human_approval)
        self.assertFalse(decision.auto_remediation_allowed)

    def test_verification_passes_when_problem_is_removed(self) -> None:
        before = {
            "findings": [
                {"code": "audio-service-stopped", "severity": "critical"},
            ]
        }
        after = {"findings": []}
        result = verify_remediation(before, after)
        self.assertTrue(result.passed)
        self.assertEqual(result.status, "pass")

    def test_verification_fails_when_critical_problem_remains(self) -> None:
        before = {
            "findings": [
                {"code": "audio-service-stopped", "severity": "critical"},
            ]
        }
        after = {
            "findings": [
                {"code": "audio-service-stopped", "severity": "critical"},
            ]
        }
        result = verify_remediation(before, after)
        self.assertFalse(result.passed)
        self.assertIn("critical_findings_remain", result.failures)

    def test_audit_chain_detects_tampering(self) -> None:
        first = create_record(
            "evt-1",
            "decision",
            {"decision": "observe"},
            observed_at="2026-08-26T00:00:00+00:00",
        )
        second = create_record(
            "evt-2",
            "decision",
            {"decision": "require_approval"},
            previous_hash=first.hash,
            observed_at="2026-08-26T00:01:00+00:00",
        )
        records = [first.to_dict(), second.to_dict()]
        self.assertTrue(verify_chain(records))

        records[1]["payload"]["decision"] = "observe"
        self.assertFalse(verify_chain(records))

    def test_build_decision_connects_inference_risk_policy_and_audit(self) -> None:
        snapshot = {
            "findings": [
                {
                    "code": "audio-service-stopped",
                    "severity": "critical",
                    "detail": "Windows Audio is stopped",
                }
            ]
        }
        result = build_decision(snapshot, event_id="inc-1")
        self.assertEqual(
            result["snapshot"]["inference"]["top_root_cause"]["code"],
            "windows-audio-service",
        )
        self.assertEqual(
            result["risk"]["top_risk"]["cause_code"],
            "windows-audio-service",
        )
        self.assertTrue(result["policy"]["requires_human_approval"])
        self.assertEqual(len(result["audit"]["hash"]), 64)


if __name__ == "__main__":
    unittest.main()
