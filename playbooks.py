"""Scenario Playbooks — enterprise lifecycle simulations on top of a design.

Each playbook takes the current design as a "golden blueprint" and simulates a
real-world organizational event (M&A replication, account absorption,
divestiture, geo/compliance expansion, scale-out), computing concrete numbers
(accounts, cost, vending time, score deltas) and a grounded, AWS-aligned runbook
with risks and references.

Grounded in AWS guidance: accounts detach from one organization and re-attach to
another (place them in a permissive "landing" OU first, then tighten SCPs);
migrating a management account requires emptying and deleting the source org;
GuardDuty/Security Hub delegated administration is per-organization; AFT (Account
Factory for Terraform) is the GitOps engine for scaled vending and enrolling
existing accounts.
"""

from __future__ import annotations

import copy
import math

import plotly.graph_objects as go
import streamlit as st

import waf
from lz_core import (
    COMPLIANCE_FRAMEWORKS, REGIONS,
    estimate_monthly_cost, recommend_guardrails, score_design, total_accounts,
    workload_account_count, core_account_count,
)

# ---------------------------------------------------------------------------
# Assumptions (transparent, like the cost model)
# ---------------------------------------------------------------------------
MIN_PER_ACCOUNT = 30            # Control Tower Account Factory provisioning, sequential
AFT_CONCURRENCY = 5            # AFT pipeline concurrent account builds (assumption)
BASELINE_HOURS_PER_ORG = 24    # stand up a fresh landing zone (mgmt+OUs+SCPs+core) per org
REMEDIATION_HOURS_PER_ACCT = 2  # assess + baseline a migrated/acquired account

REFS = {
    "ma_network": ("Migrating accounts between AWS Organizations (networking)",
                   "https://aws.amazon.com/blogs/networking-and-content-delivery/migrating-accounts-between-aws-organizations-from-a-network-perspective/"),
    "ma_waf": ("M&A readiness with the Well-Architected Framework",
               "https://aws.amazon.com/blogs/architecture/mergers-and-acquisitions-readiness-with-the-well-architected-framework/"),
    "migrate_accounts": ("Migrating AWS accounts between organizations (re:Post)",
                         "https://repost.aws/articles/ARf43Hri2LQmGARxerfK9B6Q/migrating-aws-accounts-between-organizations"),
    "lz_to_ct": ("Migrate AWS Landing Zone solution to AWS Control Tower",
                 "https://aws.amazon.com/blogs/mt/migrate-aws-landing-zone-solution-to-aws-control-tower/"),
    "aft": ("AWS Control Tower Account Factory for Terraform (AFT)",
            "https://docs.aws.amazon.com/controltower/latest/userguide/aft-overview.html"),
    "enroll": ("Enroll existing accounts in AWS Control Tower",
               "https://docs.aws.amazon.com/controltower/latest/userguide/enroll-account.html"),
}


# ---------------------------------------------------------------------------
# Shared UI helpers
# ---------------------------------------------------------------------------

def _vending_time(n_accounts: int) -> tuple[float, float]:
    """(sequential_hours, aft_pipelined_hours) to vend n accounts."""
    seq = n_accounts * MIN_PER_ACCOUNT / 60
    aft = math.ceil(max(1, n_accounts) / AFT_CONCURRENCY) * MIN_PER_ACCOUNT / 60
    return round(seq, 1), round(aft, 1)


def _runbook(phases: list[tuple[str, list[str]]]):
    st.markdown("#### Runbook")
    for label, steps in phases:
        st.markdown(f"**{label}**")
        for s in steps:
            st.markdown(f"- {s}")


def _risks(items: list[str]):
    st.markdown("#### Key risks & considerations")
    for it in items:
        st.markdown(f"- ⚠️ {it}")


def _refs(keys: list[str]):
    st.markdown("#### AWS references")
    for k in keys:
        label, url = REFS[k]
        st.markdown(f"- [{label}]({url})")


def _guardrail_names(design) -> list[str]:
    return [g[0] for g in recommend_guardrails(design)]


# ===========================================================================
# 1. M&A — replicate blueprint to N organizations
# ===========================================================================

def _ma_replicate(design, derived):
    st.markdown("Treat your **current design as a golden blueprint** and replicate it across "
                "several separate AWS Organizations — e.g. post-merger entities that must each keep "
                "their own org, or standardized subsidiaries. Each target org is provisioned to the "
                "same OU structure, guardrails, and baselines.")

    c1, c2 = st.columns(2)
    k = c1.slider("Target organizations to replicate to", 1, 12, 3)
    same = c2.toggle("Identical to the blueprint", value=True,
                     help="Off lets you scale workloads per target org.")
    wl_per = workload_account_count(design)
    if not same:
        factor = st.slider("Workload accounts per target (× blueprint)", 0.25, 3.0, 1.0, 0.25)
    else:
        factor = 1.0

    n_blueprint = total_accounts(design)
    core = core_account_count(design)
    per_target_accounts = core + round(workload_account_count(design) * factor) + (1 if design.org_size != "Startup" else 0)
    new_accounts = per_target_accounts * k
    blueprint_cost = derived["cost"]["total"]
    per_target_cost = estimate_monthly_cost(_scaled(design, factor))["total"]
    total_cost = per_target_cost * k
    seq_h, aft_h = _vending_time(new_accounts)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Target orgs", k)
    m2.metric("New accounts (total)", new_accounts, help=f"{per_target_accounts}/org × {k}")
    m3.metric("Added platform cost", f"${total_cost:,.0f}/mo")
    m4.metric("Vend time (AFT)", f"~{aft_h:,.0f} h", help=f"Sequential ≈ {seq_h:,.0f} h")

    # accounts & cost across orgs
    fig = go.Figure()
    fig.add_trace(go.Bar(x=[f"Org {i+1}" for i in range(k)], y=[per_target_accounts] * k,
                         name="Accounts", marker_color="#FF9900"))
    fig.add_trace(go.Scatter(x=[f"Org {i+1}" for i in range(k)], y=[per_target_cost] * k,
                             name="Cost ($/mo)", yaxis="y2", line=dict(color="#2DD4BF", width=3)))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10),
                      yaxis=dict(title="Accounts"),
                      yaxis2=dict(title="USD/mo", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, use_container_width=True)

    st.caption("Guardrails replicated to every org: " + ", ".join(_guardrail_names(design)[:8]) + " …")

    _runbook([
        ("Day 0 — Blueprint as code", [
            "Capture the blueprint as **LZA config** or **AFT** templates (OU tree, SCPs, baselines, "
            "core accounts) — export from the IaC tab as the starting point.",
            "Create a fresh **management account** per target org and enable AWS Organizations "
            "with **ALL features**; enable trusted access for CloudTrail/Config/GuardDuty/Security Hub.",
            "Reserve unique **root email addresses** for every account across every org (a common blocker)."]),
        ("Day 1 — Foundation per org", [
            "Set up the landing zone (Control Tower or LZA) and the Security OU "
            "(**Log Archive + Audit**) first.",
            "Apply the SCP guardrails and OU structure identically across orgs (deploy from the same "
            "code, parameterized per org).",
            f"Vend the **{per_target_accounts} accounts** per org via Account Factory / **AFT** "
            "(GitOps, ~5 concurrent builds)."]),
        ("Day 2 — Operate", [
            "Delegate **GuardDuty / Security Hub / Config admin per org** (delegated admin does not "
            "span organizations — each org needs its own).",
            "Establish cross-org connectivity where needed (TGW **peering** / Cloud WAN / PrivateLink — "
            "RAM sharing does not cross organization boundaries).",
            "Set budgets + budget actions, enable drift detection, and snapshot each org as a "
            "**target** in the Drift tab."]),
    ])
    _risks([
        "Root email uniqueness across *all* accounts in *all* orgs — plan an address scheme up front.",
        "Security tooling (GuardDuty/Security Hub) delegated admin is **per-organization** — you get N "
        "separate security planes, not one; aggregate findings centrally (e.g. Security Lake).",
        "Identity: each org has its own IAM Identity Center instance — federate to a central external "
        "IdP (Okta/Entra) for one sign-on experience.",
        "Cross-org networking can't use AWS RAM; use TGW peering, Cloud WAN, or PrivateLink and watch "
        "for overlapping CIDRs.",
        "Service quotas (accounts per org default soft-limit) — request increases before bulk vending.",
    ])
    _refs(["ma_waf", "aft", "migrate_accounts"])


def _scaled(design, factor):
    d = copy.deepcopy(design)
    d.num_workloads = max(1, round(design.num_workloads * factor))
    return d


# ===========================================================================
# 2. M&A — absorb acquired accounts into your org
# ===========================================================================

def _ma_absorb(design, derived):
    st.markdown("Bring an **acquired company's accounts into your existing organization**. Migrated "
                "accounts land in a permissive **quarantine OU**, are assessed and baselined, then "
                "enrolled into your governance and moved to their target OUs.")

    c1, c2, c3 = st.columns(3)
    m = c1.slider("Acquired member accounts", 1, 200, 15)
    whole_org = c2.toggle("Absorbing their whole org", value=True,
                          help="Includes migrating/decommissioning their management account.")
    regulated = c3.toggle("Acquiree handles regulated data", value=False)

    n_now = total_accounts(design)
    n_after = n_now + m
    enroll_h = m * (MIN_PER_ACCOUNT / 60)
    remediate_h = m * REMEDIATION_HOURS_PER_ACCT
    # cost: scale current model to the larger fleet (approx via workloads proxy)
    after_design = copy.deepcopy(design)
    after_design.num_workloads = design.num_workloads + m
    cost_after = estimate_monthly_cost(after_design)["total"]
    cost_delta = cost_after - derived["cost"]["total"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Accounts after merge", n_after, delta=f"+{m}")
    m2.metric("Enroll time", f"~{enroll_h:,.0f} h")
    m3.metric("Baseline remediation", f"~{remediate_h:,.0f} h")
    m4.metric("Added platform cost", f"${cost_delta:,.0f}/mo")

    _runbook([
        ("Phase 1 — Prepare & quarantine", [
            "Create an **Acquired / Quarantine OU** with a **permissive SCP** (allow-all or matching "
            "the acquiree's controls) so migrated accounts aren't broken by your stricter SCPs.",
            "Inventory the acquiree: accounts, workloads, CIDRs, identity, compliance scope, spend."]),
        ("Phase 2 — Migrate accounts", [
            "Remove each member account from the **source** organization, then **invite** it to yours "
            "and accept; initially place it in the Quarantine OU.",
            ("Migrating their **management account** requires first removing **all** its member "
             "accounts (not suspending) and **deleting** the source org configuration."
             if whole_org else
             "Member accounts can be invited directly; their management account stays in their org."),
        ]),
        ("Phase 3 — Assess & remediate", [
            "Run the **Live Estate** scan / IaC import to score each account; fix FAIL/WARN findings.",
            "Apply your baselines: centralized logging, GuardDuty/Security Hub/Config, encryption, "
            "tagging." + (" Apply heavy-compliance controls (LZA pack)." if regulated else "")]),
        ("Phase 4 — Enroll & graduate", [
            "**Enroll** each account into Control Tower; move it from Quarantine to its target OU so "
            "your SCPs apply progressively.",
            "Reconcile identity (map to your Identity Center / IdP groups) and connect networking "
            "(watch for overlapping CIDRs).",
            "Consolidate billing; decommission the acquiree's management account once empty."]),
    ])
    _risks([
        "Applying strict SCPs too early can break migrated workloads — graduate from the Quarantine OU "
        "gradually.",
        "Management-account migration is irreversible busywork: empty + delete the source org first.",
        "Overlapping VPC CIDRs between the two estates block simple peering — plan re-addressing or "
        "PrivateLink.",
        "Identity reconciliation (duplicate users, conflicting SSO) is often the long pole.",
        "Compliance scope may expand — re-run the assessment with the acquiree's frameworks selected.",
    ])
    _refs(["ma_network", "enroll", "ma_waf"])


# ===========================================================================
# 3. Divestiture / carve-out
# ===========================================================================

def _divestiture(design, derived):
    st.markdown("**Spin a business unit out into its own new organization** (the inverse of "
                "absorption). Stand up a fresh management account + landing zone, migrate the unit's "
                "accounts out of your org, and re-establish independent identity, security, and "
                "networking.")

    c1, c2 = st.columns(2)
    p = c1.slider("Accounts to carve out", 1, 100, 8)
    replicate = c2.toggle("Replicate your guardrails to the new org", value=True)

    core = core_account_count(design)
    new_org_accounts = p + core + 1  # carved accounts + new core + sandbox
    standup_h = BASELINE_HOURS_PER_ORG
    migrate_h = p * (MIN_PER_ACCOUNT / 60) + p * REMEDIATION_HOURS_PER_ACCT
    carved = copy.deepcopy(design)
    carved.num_workloads = p
    new_org_cost = estimate_monthly_cost(carved)["total"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("New-org accounts", new_org_accounts, help=f"{p} carved + {core} core + 1 sandbox")
    m2.metric("New-org platform cost", f"${new_org_cost:,.0f}/mo")
    m3.metric("Stand-up effort", f"~{standup_h} h")
    m4.metric("Migration effort", f"~{migrate_h:,.0f} h")

    _runbook([
        ("Day 0 — Stand up the new org", [
            "Create a new **management account** + organization (ALL features) for the divested unit.",
            "Build its landing zone (Control Tower/LZA): Security OU, **Log Archive + Audit**, Network, "
            "Shared Services." + (" Replicate your SCP guardrails." if replicate else "")]),
        ("Day 1 — Migrate the unit", [
            "An account can belong to **one org only** — remove each carved account from your org and "
            "invite it to the new org (place in a landing OU first).",
            "Re-establish per-account baselines (logging, security tooling delegated admin in the **new** "
            "org), identity, and budgets."]),
        ("Day 2 — Separate & validate", [
            "Split networking: detach from your TGW/Cloud WAN, stand up the unit's own egress, re-point "
            "DNS/PrivateLink.",
            "Separate billing (own payer), transfer data ownership, rotate cross-account roles, and "
            "validate guardrails + drift in the new org."]),
    ])
    _risks([
        "Identity re-pointing: the unit needs its own Identity Center / IdP integration — plan a cutover.",
        "Security tooling must be re-established in the new org (delegated admin doesn't follow accounts).",
        "Shared services the unit depended on (golden AMIs, CI/CD, registries) must be duplicated or "
        "vended via PrivateLink during transition.",
        "Data residency / compliance evidence must be re-baselined under the new org.",
        "Billing/credits and Reserved Instances/Savings Plans don't transfer between payers — model the "
        "cost impact.",
    ])
    _refs(["migrate_accounts", "ma_network", "lz_to_ct"])


# ===========================================================================
# 4. Geo / data-residency expansion
# ===========================================================================

def _geo_expansion(design, derived):
    st.markdown("Expand the landing zone into a **new region / jurisdiction** (e.g. enter the EU or "
                "APAC). Adds governed regions, region-restriction guardrails, and per-region baseline "
                "infrastructure.")

    new_regions = st.multiselect("New regions to add", [r for r in REGIONS if r not in design.regions],
                                 default=[r for r in REGIONS if r not in design.regions][:1])
    residency = st.toggle("Enforce data residency (deny activity outside approved regions)", value=True)

    after = copy.deepcopy(design)
    after.regions = list(design.regions) + new_regions
    cost_before = derived["cost"]["total"]
    cost_after = estimate_monthly_cost(after)["total"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Active regions", len(after.regions), delta=f"+{len(new_regions)}")
    m2.metric("Platform cost", f"${cost_after:,.0f}/mo", delta=f"+${cost_after - cost_before:,.0f}")
    m3.metric("New region baselines", len(new_regions),
              help="Each region duplicates NAT/endpoints/security tooling.")

    _runbook([
        ("Plan", [
            "Confirm **service availability** in the target regions and any data-sovereignty rules.",
            "Decide network model: extend TGW per region or adopt **Cloud WAN** for global segmentation."]),
        ("Build", [
            "Add the regions to **Control Tower governed regions**; deploy Config conformance packs / "
            "StackSets region-wide.",
            "Stand up per-region network (TGW, centralized egress, interface endpoints) and replicate "
            "security tooling (GuardDuty/Security Hub are per-region per-account)."]),
        ("Enforce", [
            ("Apply a **region-restriction SCP** (deny `aws:RequestedRegion` outside approved regions, "
             "with global-service exceptions) at the OU/root." if residency else
             "Optionally apply a region-restriction SCP for cost/blast-radius control."),
            "Deploy workloads close to users; validate residency with preventative controls + Config."]),
    ])
    _risks([
        "Not every AWS service is available in every region — verify before committing workloads.",
        "Each added region multiplies fixed baseline cost (NAT, endpoints, security tooling).",
        "Cross-region data transfer adds cost and latency — keep data-gravity in mind.",
        "Residency SCPs must whitelist global services (IAM, CloudFront, Route 53, STS, support).",
    ])
    _refs(["aft", "ma_waf"])


# ===========================================================================
# 5. Compliance onboarding
# ===========================================================================

def _compliance(design, derived):
    st.markdown("Onboard a **new compliance framework**. Simulates the guardrails, governance, and "
                "score impact of bringing the estate into scope.")

    add = st.multiselect("Frameworks to onboard",
                         [f for f in COMPLIANCE_FRAMEWORKS if f not in design.compliance],
                         default=[f for f in COMPLIANCE_FRAMEWORKS if f not in design.compliance][:1])
    harden = st.toggle("Apply recommended hardening (logging, security tooling, isolation, LZA)",
                       value=True)

    after = copy.deepcopy(design)
    after.compliance = list(design.compliance) + add
    if harden:
        after.centralized_logging = True
        after.security_tooling = True
        after.backup_dr = True
        if after.account_strategy in ("Single account", "Account per environment"):
            after.account_strategy = "Account per workload per environment"
        heavy = {"PCI-DSS", "HIPAA", "FedRAMP", "NIST 800-53", "APRA CPS 234", "HITRUST"}
        if heavy & set(add):
            after.governance = "Landing Zone Accelerator (LZA)"

    before_a, after_a = waf.assess(design), waf.assess(after)
    before_o, after_o = waf.overall_score(before_a), waf.overall_score(after_a)
    before_comp = score_design(design)["Compliance Readiness"]
    after_comp = score_design(after)["Compliance Readiness"]
    new_guardrails = [g for g in _guardrail_names(after) if g not in _guardrail_names(design)]

    m1, m2, m3 = st.columns(3)
    m1.metric("Well-Architected", f"{after_o}/100", delta=after_o - before_o)
    m2.metric("Compliance readiness", f"{after_comp}/100", delta=after_comp - before_comp)
    m3.metric("New guardrails", len(new_guardrails))

    if new_guardrails:
        st.caption("Added SCP guardrails: " + ", ".join(new_guardrails))

    _runbook([
        ("Assess", [
            "Run a gap assessment (Well-Architected tab + Live Estate) against the new framework.",
            "Map in-scope workloads and define the **PHI/CDE/regulated OU** boundary."]),
        ("Implement", [
            "Adopt **LZA compliance packs** (FedRAMP/PCI/CCCS) or Control Tower proactive/elective "
            "controls for the framework.",
            "Enforce: account-level isolation per workload, **immutable centralized logging** (S3 Object "
            "Lock), org-wide encryption SCPs, GuardDuty/Security Hub everywhere."]),
        ("Evidence", [
            "Turn on Audit Manager / Security Hub standards for continuous evidence.",
            "Snapshot a **target** in Drift and track convergence; schedule the collector for actuals."]),
    ])
    _risks([
        "Heavy frameworks penalize weak isolation hard — single-account or shared accounts won't pass.",
        "Encryption-in-transit/at-rest SCPs can break legacy workloads — stage rollout.",
        "Evidence collection is ongoing, not one-off — automate it.",
        "Region/residency constraints may apply (e.g. FedRAMP → GovCloud).",
    ])
    _refs(["lz_to_ct", "ma_waf"])


# ===========================================================================
# 6. Scale-out (N → M workloads)
# ===========================================================================

def _scale_out(design, derived):
    st.markdown("Project growth from today's footprint to a larger one and see how accounts, cost, and "
                "scores move — and when to industrialize account vending.")

    target = st.slider("Grow to N workloads", design.num_workloads, 200,
                       min(120, max(40, design.num_workloads * 3)))
    rows = []
    step = max(1, (target - design.num_workloads) // 24 or 1)
    for wl in range(design.num_workloads, target + 1, step):
        alt = copy.deepcopy(design)
        alt.num_workloads = wl
        s = score_design(alt)
        rows.append((wl, total_accounts(alt), estimate_monthly_cost(alt)["total"],
                     s["Scalability"], s["Operational Simplicity"]))
    end = rows[-1]
    seq_h, aft_h = _vending_time(end[1] - total_accounts(design))

    m1, m2, m3 = st.columns(3)
    m1.metric("Accounts at target", end[1], delta=end[1] - total_accounts(design))
    m2.metric("Platform cost at target", f"${end[2]:,.0f}/mo",
              delta=f"+${end[2]-derived['cost']['total']:,.0f}")
    m3.metric("Net-new vend time (AFT)", f"~{aft_h:,.0f} h")

    fig = go.Figure()
    xs = [r[0] for r in rows]
    fig.add_trace(go.Scatter(x=xs, y=[r[1] for r in rows], name="Accounts", line=dict(color="#FF9900", width=3)))
    fig.add_trace(go.Scatter(x=xs, y=[r[2] for r in rows], name="Cost ($/mo)", yaxis="y2",
                             line=dict(color="#2DD4BF", width=3)))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10),
                      xaxis=dict(title="Workloads"), yaxis=dict(title="Accounts"),
                      yaxis2=dict(title="USD/mo", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig, use_container_width=True)

    _runbook([
        ("Industrialize vending", [
            "Adopt **AFT** (Account Factory for Terraform) — GitOps account requests + customizations so "
            "every new account is born compliant.",
            "Request **service-quota** increases (accounts per org default soft limit) ahead of demand."]),
        ("Restructure for scale", [
            "Split the Workloads OU into Prod / Non-Prod (and per-BU) children so SCPs differ by "
            "criticality.",
            "Move to centralized egress / Cloud WAN as VPC count grows; consolidate interface endpoints."]),
        ("Operate at scale", [
            "Centralize observability and findings (delegated admin, Security Lake).",
            "Enforce tagging policies + budgets per account; track drift continuously."]),
    ])
    _risks([
        "Accounts-per-org soft limit — raise quotas before bulk creation.",
        "SCP changes propagate org-wide — test in a non-prod OU first.",
        "Peering meshes grow O(n²) — adopt TGW/Cloud WAN before it bites.",
        "Per-account fixed costs (Config/Security Hub/NAT) dominate at scale — consolidate egress.",
    ])
    _refs(["aft", "enroll"])


# ---------------------------------------------------------------------------
# Registry + entry point
# ---------------------------------------------------------------------------
_SCENARIOS = [
    ("🏢 M&A — replicate blueprint to N orgs", _ma_replicate),
    ("🤝 M&A — absorb acquired accounts", _ma_absorb),
    ("✂️ Divestiture — carve out a business unit", _divestiture),
    ("🌍 Expansion — new region / data residency", _geo_expansion),
    ("🛡️ Compliance — onboard a new framework", _compliance),
    ("📈 Scale-out — grow N→M workloads", _scale_out),
]


def render(design, derived):
    st.subheader("Scenario playbooks")
    st.caption("Pick an enterprise event and simulate it against your **current design** (the golden "
               "blueprint). Each playbook computes live numbers and a grounded, AWS-aligned runbook. "
               "Estimates are directional — validate before executing.")
    titles = [t for t, _ in _SCENARIOS]
    choice = st.selectbox("Scenario", titles, key="playbook_choice")
    st.divider()
    fn = dict(_SCENARIOS)[choice]
    fn(design, derived)
