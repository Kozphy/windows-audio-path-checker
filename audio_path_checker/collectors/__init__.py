"""Structured evidence collection for the Bluetooth audio path.

This package gathers read-only signals from Windows (PowerShell collectors,
WinRT capability probes, and optional Python snapshots) into a single
normalized evidence document consumed by the diagnostics engine.

Data flow::

    Windows state (PnP, services, endpoints)
        → Collectors/Evidence.ps1 + platform.winrt.probe_winrt_capabilities
        → collect_evidence() normalized document
        → evidence_feature_vector() for rule/ML/LLM providers

Collectors never mutate system state; remediation is handled separately.
"""
