"""AWS Well-Architected Framework alignment engine.

Evaluates an LZDesign against landing-zone-relevant best practices across the
six WAF pillars. Each check returns PASS / WARN / FAIL with a remediation.
Check IDs follow the WAF best-practice naming style (e.g. SEC01-BP01) and map
to the closest official practice areas; this is an educational approximation,
not an official AWS Well-Architected Tool review.
"""

from colors import WAF_PILLARS
from lz_core import LZDesign, estimate_monthly_cost, total_accounts, workload_account_count

PASS, WARN, FAIL = "pass", "warn", "fail"

# ---------------------------------------------------------------------------
# Honest mapping to the official AWS Well-Architected Framework.
#
# These checks are landing-zone-focused heuristics. Each one is mapped below to
# the closest *official* WAF best practice, with a `match` flag:
#   * "exact"   — the check corresponds directly to that published best practice.
#   * "adapted" — the check is an LZ-specific interpretation in that practice's
#                 area; the official best practice is broader or worded
#                 differently. Do NOT cite an "adapted" result as audit evidence.
#
# Always validate findings against the official AWS Well-Architected Tool and the
# current framework before using them for compliance or audit purposes.
# ---------------------------------------------------------------------------

WAF_DISCLAIMER = (
    "Check IDs are indicative and mapped to the closest official AWS Well-Architected "
    "best practice (see the 'WAF mapping' table). 'Adapted' checks are landing-zone "
    "interpretations, not 1:1 official practices — use the official Well-Architected "
    "Tool for audit evidence."
)

WAF_REFERENCE_URL = "https://docs.aws.amazon.com/wellarchitected/latest/framework/"

# our check_id -> (official best practice it maps to, match flag)
OFFICIAL_BP = {
    "SEC01-BP01": ("SEC01-BP01 Separate workloads using accounts", "exact"),
    "SEC01-BP02": ("SEC01-BP01 Separate workloads using accounts / account governance", "adapted"),
    "SEC02-BP04": ("SEC02-BP04 Rely on a centralized identity provider", "exact"),
    "SEC04-BP01": ("SEC04-BP01 Configure service and application logging", "exact"),
    "SEC04-BP02": ("SEC04-BP02 Capture logs, findings, and metrics in standardized locations", "adapted"),
    "SEC08-BP01": ("SEC08 Protecting data at rest / data residency controls", "adapted"),
    "SEC10-BP02": ("SEC10-BP02 Develop incident management plans (containment)", "adapted"),
    "OPS05-BP01": ("OPS Prepare — implement infrastructure & account automation", "adapted"),
    "OPS05-BP02": ("OPS Prepare — manage infrastructure as code", "adapted"),
    "OPS07-BP01": ("OPS Operate — operational readiness & team topology", "adapted"),
    "OPS08-BP01": ("OPS Operate — standardize operations across the estate", "adapted"),
    "REL01-BP01": ("REL01 Manage service quotas and constraints / blast radius", "adapted"),
    "REL02-BP01": ("REL02-BP01 Use highly available network connectivity", "exact"),
    "REL09-BP01": ("REL09 Back up data (centralized backup & recovery)", "adapted"),
    "REL10-BP01": ("REL10-BP01 Deploy the workload to multiple locations", "exact"),
    "REL01-BP03": ("REL01-BP03 Accommodate fixed service quotas through architecture", "adapted"),
    "PERF04-BP01": ("PERF04 Networking & content delivery — select the right pattern", "adapted"),
    "PERF04-BP06": ("PERF04 Networking — place resources close to consumers", "adapted"),
    "PERF02-BP01": ("PERF02 Compute & hardware — consolidate shared services", "adapted"),
    "COST02-BP01": ("COST03 Monitor usage and cost — cost allocation by account", "adapted"),
    "COST05-BP01": ("COST05 Plan for data transfer — consolidate egress", "adapted"),
    "COST01-BP05": ("COST02 Govern usage — proportionate platform overhead", "adapted"),
    "COST02-BP05": ("COST02-BP05 Implement cost controls (budgets/guardrails)", "adapted"),
    "SUS01-BP01": ("SUS01 Region selection — choose regions to reduce footprint", "adapted"),
    "SUS02-BP01": ("SUS02 Alignment to demand — reduce duplicated infrastructure", "adapted"),
    "SUS05-BP01": ("SUS05 Hardware & services — maximize utilization", "adapted"),
}

# Ordered pillar names, derived from the shared WAF_PILLARS (single source).
PILLARS = [label for label, _color in WAF_PILLARS.values()]

HEAVY_COMPLIANCE = {"PCI-DSS", "HIPAA", "FedRAMP", "NIST 800-53", "APRA CPS 234"}
RESIDENCY_COMPLIANCE = {"GDPR", "APRA CPS 234", "FedRAMP"}


def _check(check_id, title, status, finding, remediation, critical=False):
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "finding": finding,
        "remediation": remediation,
        "critical": critical,  # critical checks weigh double in the pillar score
    }


# ---------------------------------------------------------------------------
# Pillar check functions — each returns a list of check dicts
# ---------------------------------------------------------------------------

def _security(d: LZDesign) -> list:
    checks = []

    multi = d.account_strategy != "Single account"
    checks.append(_check(
        "SEC01-BP01", "Separate workloads using accounts",
        PASS if d.account_strategy == "Account per workload per environment"
        else WARN if multi else FAIL,
        f"Account strategy: {d.account_strategy}.",
        "Use account-level isolation as the primary security boundary. Group workloads "
        "and environments into separate accounts under environment-based OUs.",
        critical=True))

    checks.append(_check(
        "SEC01-BP02", "Secure root user and properties of all accounts",
        PASS if d.governance in ("AWS Control Tower", "Landing Zone Accelerator (LZA)")
        else FAIL if d.governance == "None (single account, no org)" else WARN,
        f"Governance tooling: {d.governance}.",
        "Control Tower / LZA enforce root controls and account baselines automatically. "
        "With custom Organizations, implement root MFA, no root access keys, and break-glass "
        "procedures yourself."))

    checks.append(_check(
        "SEC02-BP04", "Rely on a centralized identity provider",
        FAIL if d.identity_model == "IAM users per account"
        else PASS,
        f"Identity model: {d.identity_model}.",
        "Federate human access through IAM Identity Center or an external IdP. "
        "Eliminate long-lived IAM user credentials.", critical=True))

    checks.append(_check(
        "SEC04-BP01", "Configure service and application logging centrally",
        PASS if d.centralized_logging else FAIL,
        "Centralized Log Archive " + ("enabled." if d.centralized_logging else "NOT enabled."),
        "Enable an org-wide CloudTrail and aggregate CloudTrail / Config / VPC flow logs "
        "into an immutable Log Archive account (S3 Object Lock, restricted SCP).",
        critical=True))

    checks.append(_check(
        "SEC04-BP02", "Capture and analyze events org-wide (threat detection)",
        PASS if d.security_tooling else FAIL,
        "Org-wide GuardDuty / Security Hub / Config " +
        ("enabled." if d.security_tooling else "NOT enabled."),
        "Enable GuardDuty, Security Hub, and Config with delegated administration to the "
        "Security Tooling account across every member account and region.", critical=True))

    if RESIDENCY_COMPLIANCE & set(d.compliance):
        many_regions = len(d.regions) > 2
        checks.append(_check(
            "SEC08-BP01", "Enforce data residency with preventative controls",
            WARN if many_regions else PASS,
            f"Residency-sensitive frameworks {sorted(RESIDENCY_COMPLIANCE & set(d.compliance))} "
            f"with {len(d.regions)} active region(s).",
            "Apply region-restriction SCPs at the OU level so data cannot be created outside "
            "approved jurisdictions."))

    checks.append(_check(
        "SEC10-BP02", "Develop incident management with account-level containment",
        PASS if multi and d.governance != "None (single account, no org)" else FAIL,
        "Suspended OU + per-account containment " +
        ("available." if multi else "not possible in a single account."),
        "Multi-account designs allow quarantining a compromised account with a deny-all SCP. "
        "Maintain a Suspended OU and tested isolation runbooks."))

    return checks


def _operational_excellence(d: LZDesign) -> list:
    checks = []
    automated = d.governance in ("AWS Control Tower", "Landing Zone Accelerator (LZA)")

    checks.append(_check(
        "OPS05-BP01", "Use automation to provision accounts (account vending)",
        PASS if automated else WARN if d.governance == "Custom (Organizations + SCPs)" else FAIL,
        f"Governance tooling: {d.governance}.",
        "Use Control Tower Account Factory (or LZA pipelines) so every new account is born "
        "with baselines, networking, and guardrails — never hand-built accounts.",
        critical=True))

    checks.append(_check(
        "OPS05-BP02", "Manage the landing zone as code",
        PASS if d.governance == "Landing Zone Accelerator (LZA)"
        else WARN if automated or d.governance == "Custom (Organizations + SCPs)" else FAIL,
        f"Governance tooling: {d.governance}.",
        "Express OU structure, SCPs, and baselines as code (LZA config, AFT/Terraform, or CfCT) "
        "with peer-reviewed pipelines — avoid console drift."))

    n_acct = total_accounts(d)
    ratio_ok = d.num_teams == 0 or n_acct / max(1, d.num_teams) <= 12
    checks.append(_check(
        "OPS07-BP01", "Keep account-to-team ratio operable",
        PASS if ratio_ok else WARN,
        f"{n_acct} accounts across {d.num_teams} team(s).",
        "High account-per-team ratios need strong platform automation (vending, centralized "
        "observability, golden pipelines) to stay operable."))

    multi_region_ops = len(d.regions) <= 2 or automated
    checks.append(_check(
        "OPS08-BP01", "Standardize operations across regions",
        PASS if multi_region_ops else WARN,
        f"{len(d.regions)} region(s) with governance = {d.governance}.",
        "Multi-region estates need automated, region-agnostic baselines (Config conformance "
        "packs, StackSets) — manual per-region operations do not scale."))

    return checks


def _reliability(d: LZDesign) -> list:
    checks = []
    multi = d.account_strategy != "Single account"

    checks.append(_check(
        "REL01-BP01", "Constrain blast radius with account boundaries",
        PASS if d.account_strategy == "Account per workload per environment"
        else WARN if multi else FAIL,
        f"Account strategy: {d.account_strategy}.",
        "Account boundaries cap the impact of credential compromise, service-limit exhaustion, "
        "and runaway automation to a single workload/environment.", critical=True))

    checks.append(_check(
        "REL02-BP01", "Use highly available, scalable network topology",
        FAIL if d.network_pattern == "Flat VPC peering" and workload_account_count(d) > 5
        else WARN if d.network_pattern == "Flat VPC peering" else PASS,
        f"Network pattern: {d.network_pattern} with ~{workload_account_count(d)} workload VPC(s).",
        "Transit Gateway or Cloud WAN provide transitive, scalable any-to-any connectivity. "
        "Peering meshes grow O(n²) and have no transitive routing."))

    regulated = bool(HEAVY_COMPLIANCE & set(d.compliance))
    needs_dr = regulated or d.org_size == "Enterprise"
    checks.append(_check(
        "REL09-BP01", "Centralized backup and disaster recovery",
        PASS if d.backup_dr else (FAIL if needs_dr else WARN),
        "Centralized backup/DR " + ("enabled." if d.backup_dr else "not enabled.") +
        (" Regulated/enterprise profile expects it." if needs_dr else ""),
        "Use AWS Backup with org-level backup policies and cross-account, cross-region vault "
        "copies; test restores regularly."))

    checks.append(_check(
        "REL10-BP01", "Use multiple locations for critical workloads",
        PASS if len(d.regions) >= 2 else (WARN if needs_dr else PASS),
        f"{len(d.regions)} region(s) selected.",
        "For stringent availability/DR objectives, deploy critical workloads across multiple "
        "Availability Zones (always) and evaluate multi-region recovery."))

    checks.append(_check(
        "REL01-BP03", "Monitor and manage service quotas per account",
        PASS if multi else WARN,
        f"{total_accounts(d)} account(s) — quotas are per-account.",
        "Multi-account designs isolate service quotas per workload. Track quota utilization "
        "(Service Quotas + alarms) centrally."))

    return checks


def _performance(d: LZDesign) -> list:
    checks = []
    n_wl_vpcs = workload_account_count(d)

    fit = PASS
    note = f"{d.network_pattern} fits ~{n_wl_vpcs} workload VPC(s)."
    if d.network_pattern == "Flat VPC peering" and n_wl_vpcs > 5:
        fit, note = FAIL, f"Peering mesh across ~{n_wl_vpcs} VPCs adds routing complexity and latency variance."
    elif d.network_pattern == "AWS Cloud WAN" and len(d.regions) == 1:
        fit, note = WARN, "Cloud WAN is optimized for multi-region; single-region estates are usually better served by TGW."
    checks.append(_check(
        "PERF04-BP01", "Network pattern matched to scale and topology",
        fit, note,
        "Choose TGW hub-and-spoke for single/dual-region scale; Cloud WAN for global, "
        "segment-driven networks; peering only for a handful of VPCs."))

    checks.append(_check(
        "PERF04-BP06", "Place workloads close to users (region selection)",
        PASS if len(d.regions) >= 2 or d.org_size in ("Startup", "SMB") else WARN,
        f"{len(d.regions)} region(s) for an {d.org_size} organization.",
        "Select regions based on user proximity, data residency, and service availability; "
        "expand only when latency or residency demands it."))

    checks.append(_check(
        "PERF02-BP01", "Shared services consolidated for consistent performance",
        PASS if total_accounts(d) <= 2 or d.governance != "None (single account, no org)" else WARN,
        "Shared Services account pattern " +
        ("in place." if d.governance != "None (single account, no org)" else "absent."),
        "Centralize CI/CD, golden AMIs, container registries, and VPC endpoints (centralized "
        "interface endpoints) to avoid duplicated, inconsistent infrastructure."))

    return checks


def _cost(d: LZDesign) -> list:
    checks = []
    cost = estimate_monthly_cost(d)

    checks.append(_check(
        "COST02-BP01", "Account structure enables cost allocation",
        PASS if d.account_strategy in ("Account per workload", "Account per workload per environment")
        else WARN if d.account_strategy == "Account per environment" else FAIL,
        f"Account strategy: {d.account_strategy}.",
        "Per-workload accounts give clean, un-gameable cost attribution by linked account — "
        "the strongest showback/chargeback mechanism. Supplement with tagging policies.",
        critical=True))

    central_egress = d.network_pattern in ("Centralized egress + TGW", "AWS Cloud WAN")
    n_vpcs = workload_account_count(d)
    checks.append(_check(
        "COST05-BP01", "Shared network egress instead of per-VPC NAT sprawl",
        PASS if central_egress or n_vpcs <= 3 else WARN,
        f"{d.network_pattern} across ~{n_vpcs} workload VPC(s).",
        "Centralized egress consolidates NAT gateways (and inspection) in the Network account. "
        "Per-VPC NAT gateways multiply fixed hourly costs."))

    overhead = cost["total"]
    import pricing
    proxy = max(1, d.num_workloads) * pricing.WORKLOAD_SPEND_PROXY_PER_MONTH
    ratio = overhead / (overhead + proxy)
    checks.append(_check(
        "COST01-BP05", "Platform overhead proportionate to workload spend",
        PASS if ratio < 0.35 else WARN if ratio < 0.55 else FAIL,
        f"Estimated platform overhead ~${overhead:,.0f}/mo (~{ratio:.0%} of modeled total spend).",
        "Right-size the platform: review per-account Config/Security Hub footprint, consolidate "
        "egress, and prune unused attachments. Some overhead is the price of governance."))

    checks.append(_check(
        "COST02-BP05", "Sandbox spend is isolated and capped",
        FAIL if d.governance == "None (single account, no org)"
        else PASS if d.org_size != "Startup" else WARN,
        "Sandbox OU " + ("impossible without an organization."
                         if d.governance == "None (single account, no org)"
                         else "modeled." if d.org_size != "Startup"
                         else "not modeled (Startup profile)."),
        "Keep experimentation in disconnected sandbox accounts with budgets, budget actions, "
        "and auto-nuke policies."))

    return checks


def _sustainability(d: LZDesign) -> list:
    checks = []

    checks.append(_check(
        "SUS01-BP01", "Region selection considers footprint and proximity",
        PASS if len(d.regions) <= 3 else WARN,
        f"{len(d.regions)} active region(s).",
        "Each additional region duplicates baseline infrastructure (NAT, endpoints, security "
        "tooling). Expand regions deliberately; prefer regions with published low-carbon energy."))

    checks.append(_check(
        "SUS02-BP01", "Shared platform services reduce duplicated infrastructure",
        PASS if d.governance != "None (single account, no org)" else WARN,
        "Shared Services / centralized platform " +
        ("modeled." if d.governance != "None (single account, no org)" else "absent."),
        "Consolidating CI/CD, registries, endpoints, and egress avoids N copies of idle "
        "infrastructure across accounts."))

    checks.append(_check(
        "SUS05-BP01", "Governance enables utilization visibility and right-sizing",
        PASS if d.security_tooling else WARN,
        "Org-wide Config " + ("enabled — supports utilization/right-sizing rules." if d.security_tooling
                              else "disabled — limited estate-wide visibility."),
        "Use Config + Compute Optimizer org-wide to find idle and over-provisioned resources "
        "across every account."))

    return checks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_PILLAR_FUNCS = {
    "Operational Excellence": _operational_excellence,
    "Security": _security,
    "Reliability": _reliability,
    "Performance Efficiency": _performance,
    "Cost Optimization": _cost,
    "Sustainability": _sustainability,
}

_STATUS_POINTS = {PASS: 1.0, WARN: 0.5, FAIL: 0.0}


def assess(d: LZDesign) -> dict:
    """Run all pillar checks. Returns {pillar: {"score": int, "checks": [...]}}.

    Each check is enriched with `official` (closest official WAF best practice)
    and `match` ("exact" | "adapted") so results are never mistaken for a 1:1
    official assessment.
    """
    out = {}
    for pillar in PILLARS:
        checks = _PILLAR_FUNCS[pillar](d)
        for c in checks:
            official, match = OFFICIAL_BP.get(c["id"], ("(unmapped)", "adapted"))
            c["official"] = official
            c["match"] = match
        pts = sum(_STATUS_POINTS[c["status"]] * (2 if c["critical"] else 1) for c in checks)
        weight = sum(2 if c["critical"] else 1 for c in checks)
        out[pillar] = {
            "score": round(100 * pts / max(1, weight)),
            "checks": checks,
        }
    return out


def mapping_table() -> list:
    """Rows for the WAF-mapping reference table: (our check, official BP, match)."""
    rows = []
    for pillar in PILLARS:
        # build a fresh design-agnostic list just for IDs/titles
        for c in _PILLAR_FUNCS[pillar](LZDesign()):
            official, match = OFFICIAL_BP.get(c["id"], ("(unmapped)", "adapted"))
            rows.append({"Pillar": pillar, "Studio check": f"{c['id']} — {c['title']}",
                         "Closest official WAF best practice": official,
                         "Match": "✓ exact" if match == "exact" else "≈ adapted"})
    return rows


def overall_score(assessment: dict) -> int:
    return round(sum(p["score"] for p in assessment.values()) / len(assessment))


def top_remediations(assessment: dict, limit: int = 8) -> list:
    """FAILs first, then WARNs, across pillars."""
    items = []
    for pillar, data in assessment.items():
        for c in data["checks"]:
            if c["status"] in (FAIL, WARN):
                items.append({**c, "pillar": pillar})
    items.sort(key=lambda c: (c["status"] != FAIL, c["pillar"]))
    return items[:limit]


def assessment_markdown(assessment: dict) -> str:
    """Markdown summary of the WAF assessment (for LLM context and reports)."""
    lines = [f"## Well-Architected Alignment: {overall_score(assessment)}/100",
             f"_{WAF_DISCLAIMER}_\n"]
    for pillar, data in assessment.items():
        lines.append(f"### {pillar} — {data['score']}/100")
        for c in data["checks"]:
            icon = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[c["status"]]
            tag = c.get("match", "adapted")
            lines.append(f"- [{icon}] {c['id']} {c['title']} ({tag} → {c.get('official', '')}): {c['finding']}")
            if c["status"] != "pass":
                lines.append(f"  - Remediation: {c['remediation']}")
    return "\n".join(lines)
