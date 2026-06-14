"""Landing-zone maturity model + peer benchmarking.

Turns the raw Well-Architected number into an engaging *journey*: a named level,
a progress bar to the next level, and a concrete "next best action". Also defines
representative peer profiles so an architect can see where their design sits
relative to typical organizations of each shape.
"""

from __future__ import annotations

from lz_core import LZDesign, score_design

# (threshold, name, icon, blurb). Ordered ascending by threshold.
LEVELS = [
    (0,  "Foundational", "🌱", "Basic footprint — major guardrails still missing."),
    (45, "Managed",      "🔧", "Core controls in place; isolation/automation maturing."),
    (62, "Secured",      "🛡️", "Solid security & governance baseline across the org."),
    (80, "Optimized",    "⚡", "Well-architected across pillars; tuned for scale & cost."),
    (92, "Exemplary",    "🏆", "Reference-grade landing zone — exceeds best practice."),
]


def level_for(overall: int) -> dict:
    """Return the maturity level for an overall (0-100) score, with progress."""
    idx = 0
    for i, (threshold, *_rest) in enumerate(LEVELS):
        if overall >= threshold:
            idx = i
    threshold, name, icon, blurb = LEVELS[idx]
    if idx + 1 < len(LEVELS):
        next_threshold, next_name = LEVELS[idx + 1][0], LEVELS[idx + 1][1]
        span = max(1, next_threshold - threshold)
        progress = min(1.0, (overall - threshold) / span)
        to_next = max(0, next_threshold - overall)
    else:
        next_name, next_threshold, progress, to_next = None, None, 1.0, 0
    return {
        "level": idx + 1, "max_level": len(LEVELS),
        "name": name, "icon": icon, "blurb": blurb,
        "next_name": next_name, "next_threshold": next_threshold,
        "points_to_next": to_next, "progress_to_next": round(progress, 3),
    }


# ---------------------------------------------------------------------------
# Peer benchmark profiles — representative designs scored with the same model.
# ---------------------------------------------------------------------------

PEER_DESIGNS = {
    "Typical Startup": LZDesign(
        org_size="Startup", compliance=[], num_teams=2, num_workloads=3,
        environments=["dev", "prod"], regions=["us-east-1"],
        account_strategy="Account per environment",
        network_pattern="Transit Gateway hub-and-spoke",
        identity_model="IAM Identity Center (SSO)", governance="AWS Control Tower",
        centralized_logging=True, security_tooling=True, backup_dr=False),
    "Typical Mid-market": LZDesign(
        org_size="Mid-market", compliance=["SOC 2"], num_teams=6, num_workloads=10,
        environments=["dev", "test", "prod"], regions=["us-east-1", "us-west-2"],
        account_strategy="Account per workload per environment",
        network_pattern="Centralized egress + TGW",
        identity_model="IAM Identity Center (SSO)", governance="AWS Control Tower",
        centralized_logging=True, security_tooling=True, backup_dr=True),
    "Regulated Enterprise": LZDesign(
        org_size="Enterprise", compliance=["PCI-DSS", "HIPAA", "NIST 800-53"],
        num_teams=20, num_workloads=30,
        environments=["dev", "test", "staging", "prod"],
        regions=["us-east-1", "us-west-2", "eu-west-1"],
        account_strategy="Account per workload per environment",
        network_pattern="AWS Cloud WAN",
        identity_model="External IdP federation (Okta/Entra ID)",
        governance="Landing Zone Accelerator (LZA)",
        centralized_logging=True, security_tooling=True, backup_dr=True),
}


def peer_scores() -> dict:
    """{peer_name: {dimension: score}} for the radar overlay."""
    return {name: score_design(d) for name, d in PEER_DESIGNS.items()}
