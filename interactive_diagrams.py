"""Interactive, clickable org diagram using streamlit-agraph.

Unlike the static Graphviz render, this builds a force-directed graph the user
can drag, zoom, and click. Clicking a node returns its id so the app can show a
drill-down panel (accounts, purpose, guardrails). Falls back to Graphviz in
app.py when streamlit-agraph isn't installed.
"""

from __future__ import annotations

from streamlit_agraph import Node, Edge, Config

from lz_core import (
    LZDesign, core_account_count, total_accounts, workload_account_count,
)

# AWS-ish palette
INK = "#232F3E"
AMBER = "#FF9900"
NAVY = "#1A476F"
TEAL = "#2DD4BF"
GREEN = "#3F8624"
RED = "#B0084D"

_MAX_WORKLOAD_NODES = 8

# Default light label font (used by the Config fallback).
_LABEL_FONT = {"color": "#E9EEF7", "size": 15, "face": "Helvetica", "strokeWidth": 0}


def _font_for(fill: str) -> dict:
    """Pick a label colour with strong contrast against the node fill, so the
    text *inside* each box is always legible (dark on bright, light on dark)."""
    r, g, b = int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    color = "#0B1220" if luminance > 150 else "#F4F7FB"
    return {"color": color, "size": 14, "face": "Helvetica", "strokeWidth": 0}


def build_org_graph(d: LZDesign):
    """Return (nodes, edges, config, details) for streamlit-agraph.

    `details` maps node-id -> markdown shown when that node is clicked.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []
    details: dict[str, str] = {}

    def node(nid, label, color, size=22, shape="dot"):
        # "box" renders the label INSIDE a rectangle (sized to the text) with a
        # contrast-aware font, instead of floating it outside the shape.
        nodes.append(Node(id=nid, label=label, color=color, shape="box",
                          font=_font_for(color), margin=10, shapeProperties={"borderRadius": 6}))

    def edge(a, b):
        edges.append(Edge(source=a, target=b, color="#5A6B86"))

    single = d.account_strategy == "Single account" or d.governance.startswith("None")

    # Root
    node("root", "Organization\n(Management)", INK, size=34, shape="hexagon")
    details["root"] = (
        "### Management account\nRoot of AWS Organizations — billing and org "
        "management only. **No workloads ever run here.** Secure root: MFA, no "
        "access keys, break-glass procedure.")

    if single:
        node("single", "Single account", AMBER, size=30, shape="square")
        edge("root", "single")
        details["single"] = ("### Single account\nEverything in one account — "
                             "entire org is the blast radius. Suitable only for "
                             "prototypes. Consider account-level isolation.")
        return nodes, edges, _config(), details

    # Security OU
    node("ou_security", "Security OU", NAVY, size=26, shape="diamond")
    edge("root", "ou_security")
    details["ou_security"] = ("### Security OU\nFirst thing you build. Holds the "
                              "Log Archive and Audit (Security Tooling) accounts.")
    node("acct_log", "Log Archive", GREEN if d.centralized_logging else RED, shape="square")
    edge("ou_security", "acct_log")
    details["acct_log"] = (
        "### Log Archive account\nImmutable, centralized CloudTrail / Config / VPC "
        "flow logs (S3 Object Lock). " +
        ("✅ Centralized logging is **enabled**." if d.centralized_logging
         else "❌ Centralized logging is **off** — enable it (SEC04-BP01)."))
    node("acct_audit", "Security Tooling", GREEN if d.security_tooling else RED, shape="square")
    edge("ou_security", "acct_audit")
    details["acct_audit"] = (
        "### Security Tooling (Audit) account\nDelegated admin for GuardDuty / "
        "Security Hub / Detective / Config. " +
        ("✅ Org-wide security tooling is **enabled**." if d.security_tooling
         else "❌ Org-wide security tooling is **off** (SEC04-BP02)."))

    # Infrastructure OU
    node("ou_infra", "Infrastructure OU", NAVY, size=26, shape="diamond")
    edge("root", "ou_infra")
    details["ou_infra"] = "### Infrastructure OU\nNetwork and Shared Services accounts."
    if d.network_pattern != "Flat VPC peering":
        node("acct_net", "Network", TEAL, shape="square")
        edge("ou_infra", "acct_net")
        details["acct_net"] = (
            f"### Network account\nHosts the **{d.network_pattern}** hub "
            "(Transit Gateway / Cloud WAN / centralized egress + Route 53 Resolver). "
            "Shared to workload accounts via AWS RAM.")
    if d.num_workloads >= 5 or d.org_size in ("Mid-market", "Enterprise"):
        node("acct_shared", "Shared Services", TEAL, shape="square")
        edge("ou_infra", "acct_shared")
        details["acct_shared"] = ("### Shared Services account\nCI/CD, golden AMIs, "
                                  "container registries, directories — consumed org-wide.")

    # Workloads OU
    node("ou_workloads", "Workloads OU", NAVY, size=26, shape="diamond")
    edge("root", "ou_workloads")
    wl_count = workload_account_count(d)
    details["ou_workloads"] = (
        f"### Workloads OU\n**{wl_count}** workload account(s) under the "
        f"**{d.account_strategy}** strategy. SCPs differ by criticality "
        "(Prod vs Non-Prod).")
    per_env = d.account_strategy == "Account per workload per environment"
    if per_env:
        for child, color in (("Prod", "#8B0000"), ("NonProd", "#555")):
            cid = f"ou_wl_{child}"
            node(cid, f"{child}", "#3A4555", size=20, shape="diamond")
            edge("ou_workloads", cid)
            details[cid] = f"### Workloads / {child}\nEnvironment-scoped OU; SCPs tuned for {child}."
        parent_for_wl = "ou_wl_Prod"
    else:
        parent_for_wl = "ou_workloads"

    shown = min(wl_count, _MAX_WORKLOAD_NODES)
    for i in range(shown):
        wid = f"wl_{i}"
        node(wid, f"workload-{i + 1}", AMBER, size=16, shape="square")
        edge(parent_for_wl, wid)
        details[wid] = ("### Workload account\nOne workload (optionally per "
                        "environment). Blast radius is capped to this account.")
    if wl_count > shown:
        node("wl_more", f"+{wl_count - shown} more", "#6E7A8C", size=16, shape="square")
        edge(parent_for_wl, "wl_more")
        details["wl_more"] = f"### {wl_count - shown} more workload accounts\nCollapsed for clarity."

    # Sandbox + Suspended
    if d.org_size != "Startup":
        node("ou_sandbox", "Sandbox OU", "#3A4555", size=22, shape="diamond")
        edge("root", "ou_sandbox")
        details["ou_sandbox"] = ("### Sandbox OU\nDisconnected experimentation with "
                                 "budgets, budget actions, and auto-nuke policies.")
    node("ou_suspended", "Suspended OU", "#4A2530", size=22, shape="diamond")
    edge("root", "ou_suspended")
    details["ou_suspended"] = ("### Suspended OU\nDeny-all SCP for quarantined or "
                               "decommissioned accounts (incident containment).")

    return nodes, edges, _config(), details


def _config() -> Config:
    return Config(
        width="100%", height=460, directed=True, physics=True, hierarchical=False,
        nodeHighlightBehavior=True, highlightColor=AMBER, collapsible=False,
        node={"labelProperty": "label", "renderLabel": True, "font": _LABEL_FONT},
        link={"labelProperty": "label", "renderLabel": False},
    )
