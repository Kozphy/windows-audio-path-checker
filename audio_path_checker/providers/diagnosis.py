"""Diagnosis provider abstraction (rules today; ML/LLM later).

Providers translate evidence into classification, invariant results, and
hypotheses. They must not execute PowerShell or remediation actions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..diagnostics_engine import check_invariants, classify_state, rank_hypotheses


class DiagnosisProvider(ABC):
    """Abstract diagnosis backend: evidence → structured diagnosis.

    Implementations produce classification, invariants, and hypotheses without
    side effects on the system under test.
    """

    @abstractmethod
    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Run diagnosis on collected evidence.

        Args:
            evidence: Normalized evidence document.

        Returns:
            Diagnosis dict (provider-specific schema).

        Raises:
            NotImplementedError: Subclasses must implement.
        """
        raise NotImplementedError


class RuleDiagnosisProvider(DiagnosisProvider):
    """Deterministic rule-based diagnosis via the diagnostics engine."""

    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Classify state, check invariants, and rank hypotheses.

        Args:
            evidence: Normalized evidence document.

        Returns:
            Dict with ``provider``, ``classification``, ``invariants``, and
            ``hypotheses`` keys.
        """
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
    """Placeholder ML backend — must not execute remediation."""

    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Raise until an ML model is wired.

        Args:
            evidence: Normalized evidence document.

        Raises:
            NotImplementedError: Always; provider not implemented.
        """
        raise NotImplementedError("MLDiagnosisProvider is not implemented yet")


class LLMDiagnosisProvider(DiagnosisProvider):
    """Placeholder for LLM-assisted hypothesis ranking.

    Contract: LLM proposes only. Policy validates. Executor executes.
    Verifier confirms.
    """

    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        """Raise until an LLM backend is wired.

        Args:
            evidence: Normalized evidence document.

        Raises:
            NotImplementedError: Always; provider not implemented.
        """
        raise NotImplementedError("LLMDiagnosisProvider is not implemented yet")


def get_default_provider() -> DiagnosisProvider:
    """Return the default diagnosis provider for the pipeline.

    Returns:
        A :class:`RuleDiagnosisProvider` instance.
    """
    return RuleDiagnosisProvider()
