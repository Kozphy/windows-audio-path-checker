from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    status: str
    checks: tuple[str, ...]
    failures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checks"] = list(self.checks)
        payload["failures"] = list(self.failures)
        return payload


def verify_remediation(before: dict[str, Any], after: dict[str, Any]) -> VerificationResult:
    """Verify that remediation improved the observed diagnostic state.

    This intentionally evaluates observable outcomes rather than process exit
    codes: a successful command is not considered a successful repair unless
    the post-state is better.
    """
    before_findings = {str(item.get("code")) for item in (before.get("findings") or [])}
    after_findings = {str(item.get("code")) for item in (after.get("findings") or [])}

    critical_before = {
        str(item.get("code"))
        for item in (before.get("findings") or [])
        if item.get("severity") == "critical"
    }
    critical_after = {
        str(item.get("code"))
        for item in (after.get("findings") or [])
        if item.get("severity") == "critical"
    }

    resolved = sorted(before_findings - after_findings)
    introduced = sorted(after_findings - before_findings)
    failures: list[str] = []
    checks: list[str] = []

    checks.append(f"resolved_findings={len(resolved)}")
    checks.append(f"introduced_findings={len(introduced)}")
    checks.append(f"critical_before={len(critical_before)}")
    checks.append(f"critical_after={len(critical_after)}")

    if critical_after:
        failures.append("critical_findings_remain")
    if len(critical_after) > len(critical_before):
        failures.append("critical_state_regressed")
    if introduced and not resolved:
        failures.append("no_observable_improvement")

    passed = not failures and (bool(resolved) or not before_findings)
    return VerificationResult(
        passed=passed,
        status="pass" if passed else "fail",
        checks=tuple(checks),
        failures=tuple(failures),
    )
