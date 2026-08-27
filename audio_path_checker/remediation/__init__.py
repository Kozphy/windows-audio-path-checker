"""Risk-aware remediation planning and post-action verification.

Remediation maps diagnosed state and root-cause hypotheses to ordered actions
gated by risk level R0–R5:

    R0 — Observation only (open Settings, collect more evidence)
    R1 — Safe refresh / re-query (endpoint inventory, WinRT pair attempt)
    R2 — Service restart (audio or Bluetooth audio services)
    R3 — Scoped re-enumeration / enable adapter (non-destructive)
    R4 — Adapter radio bounce (disable/enable Bluetooth radio)
    R5 — Scoped remove / re-pair (BTHPORT cache clear for target address)

Execution mode caps the maximum allowable risk (``diagnose``/``dry-run`` → R0,
``repair`` → R3, ``aggressive-repair`` → R5). The planner always recommends
the lowest-risk useful action; mode only gates what may execute.

Verification re-classifies evidence after repair to distinguish command success
from actual path recovery.
"""
