"""Day 0 / Day 1 / Day 2 migration roadmap derived from an LZDesign."""

from datetime import date, timedelta

import pandas as pd
import plotly.express as px

from lz_core import LZDesign, total_accounts

PHASE_COLORS = {
    "Day 0 — Foundation": "#FF9900",
    "Day 1 — Build-out": "#5B8DEF",
    "Day 2 — Operate & Optimize": "#2DD4BF",
}


def build_plan(d: LZDesign) -> list[dict]:
    """Return list of {phase, task, start, duration_weeks, owner}."""
    automated = d.governance in ("AWS Control Tower", "Landing Zone Accelerator (LZA)")
    n_acct = total_accounts(d)
    plan = []

    def add(phase, task, start_w, dur_w, owner):
        plan.append({"phase": phase, "task": task, "start_w": start_w,
                     "dur_w": dur_w, "owner": owner})

    p0 = "Day 0 — Foundation"
    add(p0, "Secure management account (root MFA, break-glass)", 0, 1, "Security")
    add(p0, f"Deploy {d.governance if automated else 'AWS Organizations + baseline SCPs'}", 0, 2 if automated else 4, "Platform")
    add(p0, "Security OU: Log Archive + Security Tooling accounts", 1, 1, "Platform")
    add(p0, "Org-wide CloudTrail → immutable Log Archive", 1, 1, "Security")
    if d.security_tooling:
        add(p0, "GuardDuty / Security Hub / Config delegated admin", 2, 1, "Security")
    add(p0, f"Identity: {d.identity_model}", 1, 2, "IAM")
    add(p0, "Core SCP guardrails (region, tamper-protection, root deny)", 2, 1, "Security")

    p1 = "Day 1 — Build-out"
    net_dur = {"Flat VPC peering": 1, "Transit Gateway hub-and-spoke": 2,
               "Centralized egress + TGW": 3, "AWS Cloud WAN": 3}[d.network_pattern]
    add(p1, f"Network account + {d.network_pattern}", 3, net_dur, "Network")
    if "egress" in d.network_pattern.lower():
        add(p1, "Centralized egress + inspection VPC", 4, 2, "Network")
    add(p1, "Shared Services (CI/CD, AMIs, registries)", 4, 2, "Platform")
    add(p1, "Account vending pipeline (Account Factory / AFT)", 4, 2 if automated else 4, "Platform")
    vend_w = max(1, round(n_acct / 8))
    add(p1, f"Vend {n_acct} accounts + baselines", 6, vend_w, "Platform")
    add(p1, "Compliance controls: " + (", ".join(d.compliance) if d.compliance else "baseline"),
        6, 2, "Security")
    add(p1, "First workload migration wave", 7, 3, "App teams")

    p2 = "Day 2 — Operate & Optimize"
    start2 = 9 + vend_w
    add(p2, "FinOps: budgets, cost allocation, showback", start2, 2, "FinOps")
    if d.backup_dr:
        add(p2, "AWS Backup org policies + DR game-days", start2, 3, "Platform")
    add(p2, "Drift detection + conformance packs", start2 + 1, 2, "Security")
    add(p2, "Sandbox program (spend caps, auto-nuke)", start2 + 1, 1, "Platform")
    add(p2, "Quarterly Well-Architected reviews", start2 + 2, 1, "Architecture")

    return plan


def plan_dataframe(d: LZDesign) -> pd.DataFrame:
    base = date.today()
    rows = []
    for item in build_plan(d):
        start = base + timedelta(weeks=item["start_w"])
        end = start + timedelta(weeks=item["dur_w"])
        rows.append({
            "Task": item["task"], "Phase": item["phase"], "Owner": item["owner"],
            "Start": start, "End": end, "Weeks": item["dur_w"],
        })
    return pd.DataFrame(rows)


def timeline_figure(d: LZDesign):
    df = plan_dataframe(d)
    fig = px.timeline(
        df, x_start="Start", x_end="End", y="Task", color="Phase",
        color_discrete_map=PHASE_COLORS, hover_data=["Owner", "Weeks"],
    )
    fig.update_yaxes(autorange="reversed", title="")
    fig.update_layout(
        height=30 * len(df) + 160,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, title=""),
        margin=dict(l=10, r=10, t=10, b=10),
    )
    return fig
