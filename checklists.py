"""Trackable project-plan checklists for landing-zone delivery.

Two detailed checklists — hybrid networking (Data Center <-> AWS) and the
Control Tower / OU / Accounts / Environments / VPC build — rendered as an
interactive project plan: phased tasks with owner, effort, and dependencies that
you can tick off (progress saved durably per user), plus CSV (project-plan) and
Markdown exports.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

import store

_PHASE_COLORS = ["#FF9900", "#2DD4BF", "#5B8DEF", "#3F8624", "#B0084D", "#C47400",
                 "#1A476F", "#8B5CF6", "#E879A6"]


def _t(tid, task, owner, effort, deps=""):
    return {"id": tid, "task": task, "owner": owner, "effort": effort, "deps": deps}


# ---------------------------------------------------------------------------
# 1. Networking — Data Center <-> AWS Accounts
# ---------------------------------------------------------------------------
NETWORKING = {
    "summary": "Build hybrid connectivity between the on-prem datacenter and AWS accounts "
               "(Direct Connect + SD-WAN into a Transit Gateway / Cloud WAN hub, with a VPN "
               "backup), then connect and validate the workload VPCs.",
    "phases": [
        {"title": "0 · Planning & prerequisites", "tasks": [
            _t("NET-0.1", "Define connectivity requirements (bandwidth, latency, resiliency SLAs, growth)", "Network architect", "2d"),
            _t("NET-0.2", "Produce the IP address plan / CIDR allocation — no overlaps with DC or acquired ranges (use the CIDR tab)", "Network architect", "2d", "NET-0.1"),
            _t("NET-0.3", "Choose the AWS hub (Transit Gateway vs Cloud WAN) and the SD-WAN vendor", "Network architect", "1d", "NET-0.1"),
            _t("NET-0.4", "Provision the Network account (Infrastructure OU) with baselines", "Platform engineer", "1d"),
            _t("NET-0.5", "Order Direct Connect (dedicated/hosted; redundant or LAG); raise service quotas", "Network engineer", "10d", "NET-0.1"),
            _t("NET-0.6", "Schedule the cross-connect at the DX location with the colo / partner", "Network engineer", "5d", "NET-0.5"),
        ]},
        {"title": "1 · AWS hub build", "tasks": [
            _t("NET-1.1", "Create the Transit Gateway / Cloud WAN core network (ASN, default associations)", "Network engineer", "1d", "NET-0.4"),
            _t("NET-1.2", "Define segments / route tables for environment isolation (Prod / Stage / Dev)", "Network engineer", "1d", "NET-1.1"),
        ]},
        {"title": "2 · Direct Connect", "tasks": [
            _t("NET-2.1", "Create the Direct Connect Gateway (amazon_side_asn)", "Network engineer", "0.5d", "NET-1.1,NET-0.5"),
            _t("NET-2.2", "Associate the DX Gateway with the hub; allow on-prem prefixes", "Network engineer", "0.5d", "NET-2.1"),
            _t("NET-2.3", "Create the transit VIF(s) on the DX connection (VLAN, customer ASN)", "Network engineer", "0.5d", "NET-2.1,NET-0.6"),
            _t("NET-2.4", "Establish BGP; advertise/receive prefixes; verify the session is up", "Network engineer", "0.5d", "NET-2.3"),
        ]},
        {"title": "3 · SD-WAN overlay", "tasks": [
            _t("NET-3.1", "Deploy the SD-WAN HA appliance pair (EC2, source/dest-check off, license)", "Network engineer", "1d", "NET-1.1"),
            _t("NET-3.2", "Create the Transit Gateway Connect attachment + connect peer (GRE / BGP)", "Network engineer", "1d", "NET-3.1,NET-2.4"),
            _t("NET-3.3", "Onboard branch / remote sites to the SD-WAN overlay", "Network engineer", "3d", "NET-3.2"),
        ]},
        {"title": "4 · VPN backup path", "tasks": [
            _t("NET-4.1", "Create the Customer Gateway (on-prem / SD-WAN public IP, ASN)", "Network engineer", "0.5d"),
            _t("NET-4.2", "Create the Site-to-Site VPN to the hub", "Network engineer", "0.5d", "NET-4.1,NET-1.1"),
            _t("NET-4.3", "Tune BGP preference (DX primary, VPN backup); test failover", "Network engineer", "1d", "NET-4.2,NET-2.4"),
        ]},
        {"title": "5 · VPC connectivity", "tasks": [
            _t("NET-5.1", "Attach the workload VPC spokes to the hub (per environment)", "Network engineer", "1d", "NET-1.2"),
            _t("NET-5.2", "Configure route tables / segment associations", "Network engineer", "1d", "NET-5.1"),
            _t("NET-5.3", "Centralized egress (NAT) in the Network account", "Network engineer", "1d", "NET-1.1"),
            _t("NET-5.4", "Inspection VPC / firewall for east-west and DC traffic (optional)", "Security engineer", "2d", "NET-5.3"),
            _t("NET-5.5", "Centralized interface endpoints (PrivateLink) + Route 53 Resolver", "Network engineer", "1d", "NET-5.1"),
        ]},
        {"title": "6 · Security & DNS", "tasks": [
            _t("NET-6.1", "Security groups / NACLs per tier and environment", "Security engineer", "1d", "NET-5.1"),
            _t("NET-6.2", "DNS: Route 53 Resolver inbound/outbound endpoints, hybrid resolution", "Network engineer", "1d", "NET-5.5"),
            _t("NET-6.3", "Enforce encryption in transit; firewall policy review", "Security engineer", "1d"),
        ]},
        {"title": "7 · Validation & handover", "tasks": [
            _t("NET-7.1", "Reachability Analyzer: DC<->VPC and cross-environment checks", "Network engineer", "0.5d", "NET-5.2"),
            _t("NET-7.2", "Verify BGP sessions + route propagation; execute the DX->VPN failover test", "Network engineer", "0.5d", "NET-4.3"),
            _t("NET-7.3", "Enable VPC Flow Logs, Network Manager monitoring, CloudWatch alarms", "Platform engineer", "1d"),
            _t("NET-7.4", "Runbook, as-built diagram, and operations handover", "Network architect", "1d", "NET-7.2"),
        ]},
    ],
}

# ---------------------------------------------------------------------------
# 2. Control Tower / OU / Accounts / Environments / VPC
# ---------------------------------------------------------------------------
CONTROL_TOWER = {
    "summary": "Stand up the landing zone with AWS Control Tower: secure the management account, "
               "build the OU structure (incl. Prod/Stage/Dev environment OUs), vend core and "
               "workload accounts, apply guardrails and identity, then build per-environment VPCs.",
    "phases": [
        {"title": "0 · Prerequisites", "tasks": [
            _t("CT-0.1", "Secure the management account (root MFA, no access keys, billing alerts)", "Cloud admin", "1d"),
            _t("CT-0.2", "Reserve unique email addresses for the Log Archive + Audit accounts", "Cloud admin", "0.5d"),
            _t("CT-0.3", "Decide home region and governed regions; confirm compliance scope", "Cloud architect", "1d"),
        ]},
        {"title": "1 · Launch Control Tower", "tasks": [
            _t("CT-1.1", "Set up the landing zone (Console -> Control Tower)", "Cloud admin", "1d", "CT-0.2,CT-0.3"),
            _t("CT-1.2", "Verify the Security OU + Log Archive + Audit accounts were created", "Cloud admin", "0.5d", "CT-1.1"),
            _t("CT-1.3", "Enable org-wide CloudTrail; KMS encryption for Control Tower resources", "Security engineer", "0.5d", "CT-1.1"),
        ]},
        {"title": "2 · OU structure", "tasks": [
            _t("CT-2.1", "Create Infrastructure, Workloads, Sandbox, Suspended OUs", "Cloud admin", "0.5d", "CT-1.2"),
            _t("CT-2.2", "Create Prod / Stage / Dev OUs under Workloads", "Cloud admin", "0.5d", "CT-2.1"),
            _t("CT-2.3", "(M&A) Create the Acquired / Quarantine OU with a permissive SCP", "Cloud admin", "0.5d", "CT-2.1"),
            _t("CT-2.4", "Register all OUs with Control Tower", "Cloud admin", "0.5d", "CT-2.1"),
        ]},
        {"title": "3 · Core platform accounts", "tasks": [
            _t("CT-3.1", "Vend the Network account (Infrastructure OU)", "Platform engineer", "0.5d", "CT-2.1"),
            _t("CT-3.2", "Vend the Shared Services account (CI/CD, golden AMIs, registries)", "Platform engineer", "0.5d", "CT-2.1"),
            _t("CT-3.3", "Delegate GuardDuty / Security Hub / Config admin to the Audit account", "Security engineer", "1d", "CT-1.2"),
        ]},
        {"title": "4 · Guardrails (SCPs / controls)", "tasks": [
            _t("CT-4.1", "Apply mandatory + strongly-recommended Control Tower controls", "Security engineer", "1d", "CT-2.4"),
            _t("CT-4.2", "Region restriction, deny-leave-org, protect security services, IMDSv2 SCPs", "Security engineer", "1d", "CT-2.4"),
            _t("CT-4.3", "Compliance-specific controls (PCI/HIPAA/FedRAMP) where in scope", "Security engineer", "2d"),
            _t("CT-4.4", "Suspended OU deny-all; Sandbox budgets + auto-nuke", "Security engineer", "0.5d", "CT-2.1"),
        ]},
        {"title": "5 · Identity", "tasks": [
            _t("CT-5.1", "Enable IAM Identity Center; connect the external IdP (Okta / Entra)", "Identity engineer", "2d", "CT-1.1"),
            _t("CT-5.2", "Define permission sets; map groups to accounts / OUs", "Identity engineer", "2d", "CT-5.1"),
            _t("CT-5.3", "Break-glass access procedure + MFA policy", "Security engineer", "0.5d", "CT-5.1"),
        ]},
        {"title": "6 · Account vending (per environment)", "tasks": [
            _t("CT-6.1", "Set up Account Factory / AFT (GitOps pipeline)", "Platform engineer", "3d", "CT-2.4"),
            _t("CT-6.2", "Define account-request templates + customizations (baselines)", "Platform engineer", "2d", "CT-6.1"),
            _t("CT-6.3", "Vend workload accounts into the Prod / Stage / Dev OUs", "Platform engineer", "2d", "CT-6.2,CT-2.2"),
            _t("CT-6.4", "Apply tagging policies + budgets per account", "Platform engineer", "1d", "CT-6.3"),
        ]},
        {"title": "7 · VPC setup (per account / environment)", "tasks": [
            _t("CT-7.1", "Assign VPC CIDRs from the IP plan (no overlaps)", "Network engineer", "1d", "CT-6.3"),
            _t("CT-7.2", "Create subnets: public / app / db across Availability Zones", "Network engineer", "1d", "CT-7.1"),
            _t("CT-7.3", "Route tables, IGW, NAT (or centralized egress), gateway endpoints", "Network engineer", "1d", "CT-7.2"),
            _t("CT-7.4", "Attach the VPC to Transit Gateway / Cloud WAN; RAM share", "Network engineer", "0.5d", "CT-7.3"),
            _t("CT-7.5", "Security groups, NACLs, interface endpoints", "Security engineer", "1d", "CT-7.2"),
        ]},
        {"title": "8 · Baselines, ops & validation", "tasks": [
            _t("CT-8.1", "Verify centralized logging to the (immutable) Log Archive", "Security engineer", "0.5d", "CT-1.3"),
            _t("CT-8.2", "AWS Backup org policy + cross-region copies", "Platform engineer", "1d", "CT-3.1"),
            _t("CT-8.3", "Drift detection (Control Tower) + remediation runbook", "Platform engineer", "1d", "CT-4.1"),
            _t("CT-8.4", "Well-Architected review; sign-off + handover", "Cloud architect", "2d", "CT-7.4"),
        ]},
    ],
}

# ---------------------------------------------------------------------------
# 3. VM migration between AWS accounts (AWS MGN) — e.g. 68 VMs, cross-account
# ---------------------------------------------------------------------------
MIGRATION = {
    "summary": "Migrate servers from one AWS account to another (e.g. an acquired account) with "
               "AWS Application Migration Service (MGN): inventory and wave the fleet, replicate "
               "block-level into a staging area, test, cut over in dependency-safe waves, then "
               "finalize and decommission the source.",
    "phases": [
        {"title": "0 · Discovery & planning", "tasks": [
            _t("MIG-0.1", "Inventory the source VMs (OS, size, storage, dependencies, owners)", "Migration lead", "3d"),
            _t("MIG-0.2", "Group VMs into applications + cutover waves (db -> app -> web)", "Migration lead", "2d", "MIG-0.1"),
            _t("MIG-0.3", "Map source -> target: account, VPC, subnet, security groups, instance types", "Cloud engineer", "2d", "MIG-0.1"),
            _t("MIG-0.4", "Confirm network connectivity source <-> staging (DX / SD-WAN / VPN) + ports", "Network engineer", "1d"),
            _t("MIG-0.5", "Licensing / compliance review (Windows/BYOL, KMS-encrypted volumes, residency)", "Migration lead", "1d", "MIG-0.1"),
        ]},
        {"title": "1 · Target environment prep (your account)", "tasks": [
            _t("MIG-1.1", "Initialize AWS MGN in the target account / region", "Cloud engineer", "0.5d"),
            _t("MIG-1.2", "Configure the replication settings template (staging subnet, gp3, security group)", "Cloud engineer", "1d", "MIG-1.1"),
            _t("MIG-1.3", "Configure launch templates per tier (subnet / SG / instance type mapping)", "Cloud engineer", "1d", "MIG-0.3"),
            _t("MIG-1.4", "Set up cross-account IAM roles + the MGN service-linked role", "Security engineer", "0.5d", "MIG-1.1"),
        ]},
        {"title": "2 · Replication", "tasks": [
            _t("MIG-2.1", "Install the MGN replication agent on all source servers", "Migration engineer", "2d", "MIG-1.2,MIG-0.4"),
            _t("MIG-2.2", "Confirm initial sync reaches 100% for every server; healthy lag", "Migration engineer", "3d", "MIG-2.1"),
        ]},
        {"title": "3 · Test", "tasks": [
            _t("MIG-3.1", "Launch test instances into an isolated subnet (no impact to source)", "Migration engineer", "2d", "MIG-2.2"),
            _t("MIG-3.2", "Validate boot / drivers / application / connectivity; refine launch template", "Application owner", "3d", "MIG-3.1"),
        ]},
        {"title": "4 · Cutover (per wave)", "tasks": [
            _t("MIG-4.1", "Pre-cutover: change freeze, source backup, stakeholder comms (per wave)", "Migration lead", "1d", "MIG-3.2"),
            _t("MIG-4.2", "Quiesce app; final sync; launch cutover instances in order (db -> app -> web)", "Migration engineer", "per wave", "MIG-4.1"),
            _t("MIG-4.3", "Repoint DNS / load balancers; smoke test; app-owner sign-off", "Application owner", "per wave", "MIG-4.2"),
            _t("MIG-4.4", "Rollback path ready (revert DNS/LB, restart source) until finalize", "Migration engineer", "per wave", "MIG-4.2"),
        ]},
        {"title": "5 · Finalize & decommission", "tasks": [
            _t("MIG-5.1", "Mark migration complete in MGN; terminate replication servers + staging volumes", "Cloud engineer", "1d", "MIG-4.3"),
            _t("MIG-5.2", "Enroll migrated workloads under guardrails (logging, GuardDuty/Security Hub, backup)", "Security engineer", "1d", "MIG-4.3"),
            _t("MIG-5.3", "Decommission the source account / resources once empty", "Cloud engineer", "2d", "MIG-5.1"),
            _t("MIG-5.4", "Post-migration review + operations handover", "Migration lead", "1d", "MIG-5.3"),
        ]},
    ],
}

CHECKLISTS = [
    ("Networking — Data Center ↔ AWS", NETWORKING),
    ("Control Tower / OU / Accounts / Environments / VPC", CONTROL_TOWER),
    ("VM migration between AWS accounts (AWS MGN)", MIGRATION),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _effort_days(effort: str) -> float:
    try:
        return float(str(effort).lower().replace("d", "").strip())
    except ValueError:
        return 0.0


def _all_tasks(cl):
    for phase in cl["phases"]:
        for task in phase["tasks"]:
            yield phase["title"], task


def schedule(cl):
    """Critical-path forward pass: earliest start/finish (in working days) per task
    from effort + dependencies. Returns (start, finish, total_days). Durations with
    no number (e.g. 'per wave') default to 1 day. This is the minimum realistic
    timeline assuming dependencies are honoured and enough people to parallelize."""
    tasks = {t["id"]: t for _, t in _all_tasks(cl)}
    order = [t["id"] for _, t in _all_tasks(cl)]
    start, finish = {}, {}
    for _ in range(len(order)):  # repeat to converge across forward references
        for tid in order:
            t = tasks[tid]
            deps = [d.strip() for d in t["deps"].split(",") if d.strip()]
            es = max((finish.get(d, 0.0) for d in deps), default=0.0)
            dur = _effort_days(t["effort"]) or 1.0
            start[tid], finish[tid] = es, es + dur
    total = max(finish.values()) if finish else 0.0
    return start, finish, total


def gantt_figure(cl):
    """Task-level Gantt (horizontal bars) coloured by phase, ordered top-to-bottom."""
    start, finish, total = schedule(cl)
    ids, bases, durs, colors, hover = [], [], [], [], []
    for pi, phase in enumerate(cl["phases"]):
        col = _PHASE_COLORS[pi % len(_PHASE_COLORS)]
        for t in phase["tasks"]:
            ids.append(t["id"])
            bases.append(start[t["id"]])
            durs.append(max(0.5, finish[t["id"]] - start[t["id"]]))
            colors.append(col)
            hover.append(f"{t['id']} · {t['task']}<br>Day {start[t['id']]:.0f}–{finish[t['id']]:.0f}"
                         f" · {t['owner']} · {t['effort']}")
    fig = go.Figure(go.Bar(
        x=durs, base=bases, y=ids, orientation="h",
        marker_color=colors, hovertext=hover, hoverinfo="text",
        text=[f"{s:.0f}–{f:.0f}" for s, f in zip(bases, [b + d for b, d in zip(bases, durs)])],
        textposition="outside", textfont=dict(size=8, color="#8C9CB8")))
    fig.update_layout(
        height=max(360, 17 * len(ids)),
        xaxis=dict(title="Working day", range=[0, total * 1.12]),
        yaxis=dict(autorange="reversed", title=""),
        margin=dict(l=10, r=10, t=10, b=10), bargap=0.25, showlegend=False)
    return fig, total


def to_csv(name, cl, state) -> str:
    start, finish, _ = schedule(cl)
    rows = ["Phase,Task ID,Task,Owner,Effort (days),Depends On,Start (day),End (day),Status"]
    for phase_title, t in _all_tasks(cl):
        status = "Done" if state.get(t["id"]) else "Open"
        task = t["task"].replace('"', "'")
        rows.append(f'"{phase_title}",{t["id"]},"{task}",{t["owner"]},'
                    f'{_effort_days(t["effort"])},{t["deps"] or ""},'
                    f'{start[t["id"]]:.0f},{finish[t["id"]]:.0f},{status}')
    return "\n".join(rows) + "\n"


def to_markdown(name, cl, state) -> str:
    done = sum(1 for _, t in _all_tasks(cl) if state.get(t["id"]))
    total = sum(1 for _ in _all_tasks(cl))
    lines = [f"# {name} — project checklist",
             f"_{cl['summary']}_", "",
             f"**Progress: {done}/{total} tasks ({round(100*done/max(1,total))}%)**", ""]
    for phase in cl["phases"]:
        lines.append(f"## {phase['title']}")
        for t in phase["tasks"]:
            box = "x" if state.get(t["id"]) else " "
            lines.append(f"- [{box}] **{t['id']}** {t['task']}  "
                         f"_(owner: {t['owner']} · effort: {t['effort']}"
                         + (f" · depends: {t['deps']}" if t["deps"] else "") + ")_")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Master plan — all three tracks combined into one programme schedule
# ---------------------------------------------------------------------------
_TRACK_TAG = {CHECKLISTS[0][0]: "NET", CHECKLISTS[1][0]: "CT", CHECKLISTS[2][0]: "MGN"}


def master_schedule():
    """Combine the three tracks into one programme. Control Tower and Networking
    run in parallel from day 0; migration starts once accounts + connectivity are
    ready (after the longer of those two)."""
    scheds = {name: schedule(cl) for name, cl in CHECKLISTS}
    names = [n for n, _ in CHECKLISTS]  # [Networking, Control Tower, Migration]
    offsets = {names[0]: 0, names[1]: 0,
               names[2]: max(scheds[names[0]][2], scheds[names[1]][2])}
    rows = []
    for name, cl in CHECKLISTS:
        s, f, _ = scheds[name]
        o = offsets[name]
        for phase_title, t in _all_tasks(cl):
            rows.append({"Track": name, "Phase": phase_title, "ID": t["id"], "Task": t["task"],
                         "Owner": t["owner"], "Effort": _effort_days(t["effort"]), "Deps": t["deps"],
                         "Start": s[t["id"]] + o, "End": f[t["id"]] + o})
    total = max((r["End"] for r in rows), default=0.0)
    return rows, total, offsets


def master_gantt():
    """Phase-level Gantt across all three tracks (readable programme overview)."""
    _rows, total, offsets = master_schedule()
    fig = go.Figure()
    ylabels, bases, durs, colors, hover = [], [], [], [], []
    for ti, (name, cl) in enumerate(CHECKLISTS):
        col = _PHASE_COLORS[ti % len(_PHASE_COLORS)]
        s, f, _ = schedule(cl)
        o = offsets[name]
        for phase in cl["phases"]:
            ps = min(s[t["id"]] for t in phase["tasks"]) + o
            pe = max(f[t["id"]] for t in phase["tasks"]) + o
            ylabels.append(f"[{_TRACK_TAG[name]}] {phase['title']}")
            bases.append(ps)
            durs.append(max(0.5, pe - ps))
            colors.append(col)
            hover.append(f"{name}<br>{phase['title']}<br>Day {ps:.0f}–{pe:.0f}")
    fig.add_trace(go.Bar(x=durs, base=bases, y=ylabels, orientation="h",
                         marker_color=colors, hovertext=hover, hoverinfo="text"))
    fig.update_layout(height=max(360, 22 * len(ylabels)),
                      xaxis=dict(title="Working day", range=[0, total * 1.1]),
                      yaxis=dict(autorange="reversed"), bargap=0.3,
                      margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
    return fig, total


def combined_csv(states=None) -> str:
    states = states or {}
    rows, _total, _off = master_schedule()
    out = ["Track,Phase,Task ID,Task,Owner,Effort (days),Depends On,Start (day),End (day),Status"]
    for r in rows:
        status = "Done" if states.get(r["Track"], {}).get(r["ID"]) else "Open"
        task = r["Task"].replace('"', "'")
        out.append(f'"{r["Track"]}","{r["Phase"]}",{r["ID"]},"{task}",{r["Owner"]},'
                   f'{r["Effort"]},{r["Deps"] or ""},{r["Start"]:.0f},{r["End"]:.0f},{status}')
    return "\n".join(out) + "\n"


def _sheet_name(name: str) -> str:
    import re
    return re.sub(r"[\\/*?:\[\]]", " ", name)[:31]


def combined_xlsx(states=None):
    """Multi-sheet Excel: a Master schedule + one sheet per track. None if openpyxl
    isn't available (caller offers CSV instead)."""
    try:
        import io
        import pandas as pd
    except Exception:
        return None
    try:
        states = states or {}
        rows, _total, _off = master_schedule()
        master = [{"Track": r["Track"], "Phase": r["Phase"], "Task ID": r["ID"], "Task": r["Task"],
                   "Owner": r["Owner"], "Effort (days)": r["Effort"], "Depends On": r["Deps"],
                   "Start (day)": round(r["Start"]), "End (day)": round(r["End"]),
                   "Status": "Done" if states.get(r["Track"], {}).get(r["ID"]) else "Open"}
                  for r in rows]
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            pd.DataFrame(master).to_excel(w, sheet_name="Master schedule", index=False)
            for name, cl in CHECKLISTS:
                s, f, _ = schedule(cl)
                df = [{"Phase": pt, "Task ID": t["id"], "Task": t["task"], "Owner": t["owner"],
                       "Effort (days)": _effort_days(t["effort"]), "Depends On": t["deps"],
                       "Start (day)": round(s[t["id"]]), "End (day)": round(f[t["id"]]),
                       "Status": "Done" if states.get(name, {}).get(t["id"]) else "Open"}
                      for pt, t in _all_tasks(cl)]
                pd.DataFrame(df).to_excel(w, sheet_name=_sheet_name(name), index=False)
        return buf.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render(user="operator", persist=False):
    st.subheader("Project checklists")
    st.caption("Track landing-zone delivery like a project plan: phased tasks with owner, effort, "
               "and dependencies. Tick items off (progress is saved per user) and export the plan "
               "as CSV (for Excel / MS Project) or Markdown.")

    names = [n for n, _ in CHECKLISTS]
    pick = st.selectbox("Checklist", names, key="checklist_pick")
    cl = dict(CHECKLISTS)[pick]
    st.caption(cl["summary"])

    # Defensive: a stale/old store module (e.g. before a full redeploy) may lack
    # the checklist functions — degrade to session-only instead of crashing.
    can_persist = persist and hasattr(store, "load_checklist") and hasattr(store, "save_checklist")
    saved = {}
    if can_persist:
        try:
            saved = store.load_checklist(user, pick)
        except Exception:
            can_persist = False
    state = {}

    # progress + reset controls
    total = sum(1 for _ in _all_tasks(cl))
    pc1, pc2 = st.columns([3, 1])

    # render phases
    for phase in cl["phases"]:
        p_total = len(phase["tasks"])
        p_done = sum(1 for t in phase["tasks"]
                     if st.session_state.get(f"clk_{pick}_{t['id']}", saved.get(t["id"], False)))
        with st.expander(f"{phase['title']}  ·  {p_done}/{p_total}", expanded=(p_done < p_total)):
            for t in phase["tasks"]:
                tkey = f"clk_{pick}_{t['id']}"
                if tkey not in st.session_state:
                    st.session_state[tkey] = bool(saved.get(t["id"], False))
                val = st.checkbox(f"**{t['id']}** · {t['task']}", key=tkey)
                st.caption(f"👤 {t['owner']}  ·  ⏱ {t['effort']}"
                           + (f"  ·  ↪ depends on {t['deps']}" if t["deps"] else ""))
                state[t["id"]] = val

    done = sum(1 for v in state.values() if v)
    eff_total = sum(_effort_days(t["effort"]) for _, t in _all_tasks(cl))
    eff_done = sum(_effort_days(t["effort"]) for _, t in _all_tasks(cl) if state.get(t["id"]))
    with pc1:
        st.progress(done / max(1, total),
                    text=f"**{done}/{total} tasks** ({round(100 * done / max(1, total))}%) · "
                         f"{eff_done:.1f}/{eff_total:.1f} person-days done")
    with pc2:
        if st.button("Reset", use_container_width=True):
            for _, t in _all_tasks(cl):
                st.session_state[f"clk_{pick}_{t['id']}"] = False
            if can_persist:
                try:
                    store.save_checklist(user, pick, {})
                except Exception:
                    pass
            st.rerun()

    if can_persist:
        try:
            store.save_checklist(user, pick, state)
            st.caption("✅ Progress saved.")
        except Exception:
            st.caption("⚠️ Could not save progress (store unavailable) — export to keep it.")
    else:
        st.caption("⚠️ Progress lives in this session only — export the CSV/Markdown to keep it.")

    # --- realistic timeline (critical-path schedule) ---
    with st.expander("📅 Timeline & schedule (critical path)", expanded=True):
        fig, total = gantt_figure(cl)
        import math as _math
        weeks = _math.ceil(total / 5)
        t1, t2, t3 = st.columns(3)
        t1.metric("Total effort", f"{eff_total:.1f} person-days")
        t2.metric("Critical-path duration", f"~{total:.0f} working days")
        t3.metric("Calendar estimate", f"~{weeks} week(s)")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Realistic, achievable schedule from task effort + dependencies (earliest "
                   "start/finish). Long-lead items like **Direct Connect provisioning** drive the "
                   "critical path. Assumes dependencies are honoured and enough people to "
                   "parallelize independent tasks; add calendar buffer for approvals and change windows.")

    d1, d2 = st.columns(2)
    d1.download_button("⬇️ This checklist (CSV)", to_csv(pick, cl, state),
                       file_name="landing-zone-checklist.csv", mime="text/csv",
                       use_container_width=True)
    d2.download_button("⬇️ This checklist (Markdown)", to_markdown(pick, cl, state),
                       file_name="landing-zone-checklist.md", mime="text/markdown",
                       use_container_width=True)

    # --- combined master programme across all three tracks ---
    st.divider()
    st.markdown("#### 📦 Master project plan — all three tracks")
    all_states = {nm: {t["id"]: bool(st.session_state.get(f"clk_{nm}_{t['id']}", False))
                       for _, t in _all_tasks(c)} for nm, c in CHECKLISTS}
    import math as _math
    mfig, mtotal = master_gantt()
    g1, g2 = st.columns([1, 1])
    g1.metric("Programme duration", f"~{mtotal:.0f} working days")
    g2.metric("Calendar estimate", f"~{_math.ceil(mtotal / 5)} week(s)")
    st.caption("Control Tower and Networking run in parallel from day 0; the MGN migration starts "
               "once accounts **and** connectivity are ready. (Long-lead Direct Connect drives the "
               "front of the schedule.)")
    st.plotly_chart(mfig, use_container_width=True)
    x1, x2 = st.columns(2)
    x1.download_button("⬇️ Master plan — all tracks (CSV)", combined_csv(all_states),
                       file_name="landing-zone-master-plan.csv", mime="text/csv",
                       use_container_width=True)
    xlsx = combined_xlsx(all_states)
    if xlsx:
        x2.download_button("⬇️ Master plan — all tracks (Excel .xlsx)", xlsx,
                           file_name="landing-zone-master-plan.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
    else:
        x2.caption("Excel export needs `openpyxl` (CSV available).")
