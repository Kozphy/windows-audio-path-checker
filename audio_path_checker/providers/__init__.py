"""Diagnosis provider abstractions.

Providers implement ``Evidence → diagnosis`` without executing PowerShell or
remediation. The default ``RuleDiagnosisProvider`` delegates to the
diagnostics engine; ``MLDiagnosisProvider`` and ``LLMDiagnosisProvider`` are
placeholders for future backends.
"""
