"""One-click remediation engine — close the advisor loop.

Each Well-Architected finding (and the advisor's recommendations) can be turned
into a concrete design change that the user applies with a single click. This
maps a check ID to the field mutation that resolves it, so the app can show an
"⚡ Apply fix" button next to a finding, mutate the working design, and re-score
instantly — turning passive advice into an interactive feedback loop.
"""

from __future__ import annotations

import copy

from lz_core import LZDesign

# check_id -> {label, changes:{field: value}}
# `label` is the human action shown on the button; `changes` is applied to the
# LZDesign. Several checks share a remedy (e.g. blast-radius + cost-allocation
# both improve by moving to per-workload-per-env isolation).
FIX_ACTIONS: dict[str, dict] = {
    # --- Security ---
    "SEC01-BP01": {"label": "Adopt account-per-workload-per-env isolation",
                   "changes": {"account_strategy": "Account per workload per environment"}},
    "SEC01-BP02": {"label": "Adopt AWS Control Tower governance",
                   "changes": {"governance": "AWS Control Tower"}},
    "SEC02-BP04": {"label": "Switch to IAM Identity Center (SSO)",
                   "changes": {"identity_model": "IAM Identity Center (SSO)"}},
    "SEC04-BP01": {"label": "Enable centralized logging (Log Archive)",
                   "changes": {"centralized_logging": True}},
    "SEC04-BP02": {"label": "Enable org-wide security tooling",
                   "changes": {"security_tooling": True}},
    "SEC10-BP02": {"label": "Move to multi-account isolation (containment)",
                   "changes": {"account_strategy": "Account per workload per environment"}},
    # --- Operational Excellence ---
    "OPS05-BP01": {"label": "Adopt Control Tower (automated account vending)",
                   "changes": {"governance": "AWS Control Tower"}},
    "OPS05-BP02": {"label": "Adopt LZA (manage landing zone as code)",
                   "changes": {"governance": "Landing Zone Accelerator (LZA)"}},
    # --- Reliability ---
    "REL01-BP01": {"label": "Adopt account-per-workload-per-env isolation",
                   "changes": {"account_strategy": "Account per workload per environment"}},
    "REL02-BP01": {"label": "Switch to Transit Gateway hub-and-spoke",
                   "changes": {"network_pattern": "Transit Gateway hub-and-spoke"}},
    "REL09-BP01": {"label": "Enable centralized backup & DR",
                   "changes": {"backup_dr": True}},
    # --- Performance ---
    "PERF04-BP01": {"label": "Switch to Transit Gateway hub-and-spoke",
                    "changes": {"network_pattern": "Transit Gateway hub-and-spoke"}},
    # --- Cost ---
    "COST02-BP01": {"label": "Use per-workload accounts for cost allocation",
                    "changes": {"account_strategy": "Account per workload per environment"}},
    "COST05-BP01": {"label": "Centralize egress (Centralized egress + TGW)",
                    "changes": {"network_pattern": "Centralized egress + TGW"}},
}


def fix_for(check_id: str) -> dict | None:
    """Return the fix action for a check ID, or None if there's no auto-fix."""
    return FIX_ACTIONS.get(check_id)


def is_already_satisfied(design: LZDesign, check_id: str) -> bool:
    """True if the design already has the fix's target values (nothing to do)."""
    action = FIX_ACTIONS.get(check_id)
    if not action:
        return True
    return all(getattr(design, k, None) == v for k, v in action["changes"].items())


def apply_fix(design: LZDesign, check_id: str) -> tuple[LZDesign, dict | None]:
    """Return (new_design, action). new_design is a copy with the fix applied."""
    action = FIX_ACTIONS.get(check_id)
    if not action:
        return design, None
    new = copy.deepcopy(design)
    for field, value in action["changes"].items():
        setattr(new, field, value)
    return new, action
