from audio_path_checker.audit import create_record, verify_chain
from audio_path_checker.decision import build_decision
from audio_path_checker.policy import evaluate_policy
from audio_path_checker.risk import assess_root_cause
from audio_path_checker.verification import verify_remediation


def test_high_confidence_service_failure_becomes_high_risk():
    risk = assess_root_cause(
        {
            "code": "windows-audio-service",
            "probability": 0.98,
        }
    )
    assert risk.level in {"high", "critical"}
    assert risk.score >= 0.45


def test_high_risk_requires_approval():
    decision = evaluate_policy({"level": "high", "score": 0.6})
    assert decision.decision == "require_approval"
    assert decision.requires_human_approval is True
    assert decision.auto_remediation_allowed is False


def test_verification_passes_when_problem_is_removed():
    before = {
        "findings": [
            {"code": "audio-service-stopped", "severity": "critical"},
        ]
    }
    after = {"findings": []}
    result = verify_remediation(before, after)
    assert result.passed is True
    assert result.status == "pass"


def test_verification_fails_when_critical_problem_remains():
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
    assert result.passed is False
    assert "critical_findings_remain" in result.failures


def test_audit_chain_detects_tampering():
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
    assert verify_chain(records) is True

    records[1]["payload"]["decision"] = "observe"
    assert verify_chain(records) is False


def test_build_decision_connects_inference_risk_policy_and_audit():
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
    assert result["snapshot"]["inference"]["top_root_cause"]["code"] == "windows-audio-service"
    assert result["risk"]["top_risk"]["cause_code"] == "windows-audio-service"
    assert result["policy"]["requires_human_approval"] is True
    assert len(result["audit"]["hash"]) == 64
