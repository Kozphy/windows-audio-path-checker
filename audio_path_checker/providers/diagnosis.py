"""Diagnosis provider abstraction (rules today; ML/LLM later)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..diagnostics_engine import check_invariants, classify_state, rank_hypotheses


class DiagnosisProvider(ABC):
    """Evidence → feature representation → diagnosis. Never executes PowerShell."""

    @abstractmethod
    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class RuleDiagnosisProvider(DiagnosisProvider):
    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        classification = classify_state(evidence)
        invariants = check_invariants(evidence)
        hypotheses = rank_hypotheses(evidence, classification)
        return {
            "provider": "RuleDiagnosisProvider",
            "classification": classification,
            "invariants": invariants,
            "hypotheses": hypotheses,
        }


class MLDiagnosisProvider(DiagnosisProvider):
    """Placeholder — must not execute remediation."""

    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("MLDiagnosisProvider is not implemented yet")


class LLMDiagnosisProvider(DiagnosisProvider):
    """
    Placeholder for LLM ranking.

    Contract: LLM proposes only. Policy validates. Executor executes. Verifier confirms.
    """

    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("LLMDiagnosisProvider is not implemented yet")


def get_default_provider() -> DiagnosisProvider:
    return RuleDiagnosisProvider()
