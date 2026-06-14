"""Guided setup wizard — a friendly on-ramp for first-time users.

Instead of dropping every control on the user at once, this walks them through
three steps (profile → priorities → review) and produces a complete, scored
starting design they can then refine. Mirrors the login-gate pattern: when the
wizard is active the app renders only the wizard and stops.
"""

from __future__ import annotations

import copy

import streamlit as st

from lz_core import (
    COMPLIANCE_FRAMEWORKS, ORG_SIZES, REGIONS,
    recommend_design, score_design, total_accounts, estimate_monthly_cost,
)

_PRIORITIES = {
    "Balanced": "A sensible default across security, cost, and simplicity.",
    "Strongest security & compliance": "Maximize isolation, governance, and guardrails.",
    "Lowest cost & simplest ops": "Fewest moving parts; consolidate where safe.",
    "Built to scale fast": "Optimize for growth — automation and scalable networking.",
}


def launch():
    st.session_state.wizard_active = True
    st.session_state.wizard_step = 1


def _apply_priority(design, priority: str):
    d = copy.deepcopy(design)
    if priority == "Strongest security & compliance":
        d.account_strategy = "Account per workload per environment"
        d.identity_model = "External IdP federation (Okta/Entra ID)"
        d.centralized_logging = d.security_tooling = d.backup_dr = True
        d.network_pattern = "Centralized egress + TGW"
    elif priority == "Lowest cost & simplest ops":
        if d.account_strategy == "Account per workload per environment":
            d.account_strategy = "Account per workload"
        d.network_pattern = "Centralized egress + TGW"
        d.governance = "AWS Control Tower"
    elif priority == "Built to scale fast":
        d.account_strategy = "Account per workload per environment"
        d.network_pattern = "AWS Cloud WAN" if len(d.regions) >= 2 else "Transit Gateway hub-and-spoke"
        d.governance = "AWS Control Tower"
    return d


def _progress(step: int):
    labels = ["Profile", "Priorities", "Review"]
    chips = []
    for i, lab in enumerate(labels, 1):
        state = "done" if i < step else ("active" if i == step else "todo")
        color = {"done": "#3F8624", "active": "#FF9900", "todo": "#6E7A8C"}[state]
        chips.append(
            f"<span style='font-family:monospace;font-size:.7rem;letter-spacing:.1em;"
            f"color:{color};border:1px solid {color};border-radius:999px;"
            f"padding:.2rem .7rem;margin-right:.5rem;'>"
            f"{'✓' if state == 'done' else i} {lab}</span>")
    st.markdown("<div style='margin:.4rem 0 1rem'>" + "".join(chips) + "</div>",
                unsafe_allow_html=True)


def run_wizard() -> bool:
    """Render the wizard if active. Returns True when active (caller should stop)."""
    if not st.session_state.get("wizard_active"):
        return False

    step = st.session_state.get("wizard_step", 1)
    w = st.session_state.setdefault("wizard_inputs", {})

    st.markdown("## 🧭 Guided landing-zone setup")
    st.caption("Three quick steps to a complete, scored starting design. You can "
               "refine everything afterwards.")
    _progress(step)

    # -- Step 1: Profile --
    if step == 1:
        st.subheader("1 · Tell us about your organization")
        w["org_size"] = st.selectbox("Organization size", ORG_SIZES,
                                     index=ORG_SIZES.index(w.get("org_size", "Mid-market")))
        w["compliance"] = st.multiselect("Compliance frameworks (optional)",
                                         COMPLIANCE_FRAMEWORKS, default=w.get("compliance", []))
        c1, c2 = st.columns(2)
        w["num_teams"] = c1.slider("Application teams", 1, 50, w.get("num_teams", 5))
        w["num_workloads"] = c2.slider("Workloads / applications", 1, 60, w.get("num_workloads", 8))
        w["regions"] = st.multiselect("Primary AWS regions", REGIONS,
                                      default=w.get("regions", ["us-east-1"])) or ["us-east-1"]
        _nav(back=False, next_step=2)

    # -- Step 2: Priorities --
    elif step == 2:
        st.subheader("2 · What matters most?")
        st.caption("We'll bias the recommended design toward this priority.")
        choice = st.radio("Priority", list(_PRIORITIES.keys()),
                          index=list(_PRIORITIES).index(w.get("priority", "Balanced")),
                          captions=list(_PRIORITIES.values()))
        w["priority"] = choice
        _nav(back=True, next_step=3)

    # -- Step 3: Review --
    else:
        st.subheader("3 · Review your starting design")
        base = recommend_design(w["org_size"], w["compliance"], w["num_workloads"],
                                w["num_teams"], w["regions"])
        design = _apply_priority(base, w.get("priority", "Balanced"))
        st.session_state.wizard_preview = design

        scores = score_design(design)
        cost = estimate_monthly_cost(design)
        m1, m2, m3 = st.columns(3)
        m1.metric("Total accounts", total_accounts(design))
        m2.metric("Avg design score", round(sum(scores.values()) / len(scores)))
        m3.metric("Est. platform / mo", f"${cost['total']:,.0f}")

        st.markdown(
            f"- **Account strategy:** {design.account_strategy}\n"
            f"- **Network:** {design.network_pattern}\n"
            f"- **Identity:** {design.identity_model}\n"
            f"- **Governance:** {design.governance}\n"
            f"- **Guardrails:** centralized logging {'✅' if design.centralized_logging else '❌'}, "
            f"security tooling {'✅' if design.security_tooling else '❌'}, "
            f"backup/DR {'✅' if design.backup_dr else '❌'}")

        b1, b2, b3 = st.columns([1, 1, 1])
        if b1.button("← Back", use_container_width=True):
            st.session_state.wizard_step = 2
            st.rerun()
        if b2.button("✨ Use this design", type="primary", use_container_width=True):
            st.session_state.design = design
            _finish()
        if b3.button("Skip wizard", use_container_width=True):
            _finish()

    return True


def _nav(back: bool, next_step: int):
    cols = st.columns([1, 1, 1])
    if back and cols[0].button("← Back", use_container_width=True):
        st.session_state.wizard_step = next_step - 2
        st.rerun()
    if cols[2].button("Next →", type="primary", use_container_width=True):
        st.session_state.wizard_step = next_step
        st.rerun()
    if cols[1].button("Skip", use_container_width=True):
        _finish()


def _finish():
    st.session_state.wizard_active = False
    st.session_state.wizard_step = 1
    st.rerun()
