"""AWS Landing Zone Studio — interactive multi-account design simulator + AI advisor.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud (see README.md)
"""

import copy

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import llm
import ui
import waf
from diagrams import network_diagram, org_structure_diagram
from lz_core import (
    ACCOUNT_STRATEGIES, COMPLIANCE_FRAMEWORKS, ENVIRONMENTS, GOVERNANCE_TOOLING,
    IDENTITY_MODELS, NETWORK_PATTERNS, ORG_SIZES, REGIONS,
    LZDesign, core_account_count, estimate_monthly_cost, recommend_design,
    recommend_guardrails, score_design, total_accounts, workload_account_count,
)

st.set_page_config(
    page_title="AWS Landing Zone Studio",
    page_icon="🏗️",
    layout="wide",
)

ui.inject_css()

if not ui.login_gate():
    st.stop()

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "design" not in st.session_state:
    st.session_state.design = LZDesign()
if "chat" not in st.session_state:
    st.session_state.chat = []  # list of {"role", "content"}

design: LZDesign = st.session_state.design

# ---------------------------------------------------------------------------
# Sidebar — design inputs + LLM settings
# ---------------------------------------------------------------------------

with st.sidebar:
    ui.sidebar_brand()
    st.caption("Design, simulate, and stress-test AWS multi-account landing zones.")

    st.header("1. Organization profile")
    design.org_size = st.selectbox("Organization size", ORG_SIZES, index=ORG_SIZES.index(design.org_size))
    design.compliance = st.multiselect("Compliance frameworks", COMPLIANCE_FRAMEWORKS, default=design.compliance)
    design.num_teams = st.slider("Application teams", 1, 50, design.num_teams)
    design.num_workloads = st.slider("Workloads / applications", 1, 60, design.num_workloads)
    design.environments = st.multiselect("Environments", ENVIRONMENTS, default=design.environments) or ["prod"]
    design.regions = st.multiselect("AWS regions", REGIONS, default=design.regions) or ["us-east-1"]

    st.header("2. Architecture choices")
    design.account_strategy = st.selectbox(
        "Account strategy", ACCOUNT_STRATEGIES, index=ACCOUNT_STRATEGIES.index(design.account_strategy))
    design.network_pattern = st.selectbox(
        "Network pattern", NETWORK_PATTERNS, index=NETWORK_PATTERNS.index(design.network_pattern))
    design.identity_model = st.selectbox(
        "Identity model", IDENTITY_MODELS, index=IDENTITY_MODELS.index(design.identity_model))
    design.governance = st.selectbox(
        "Governance tooling", GOVERNANCE_TOOLING, index=GOVERNANCE_TOOLING.index(design.governance))
    design.centralized_logging = st.toggle("Centralized logging (Log Archive)", value=design.centralized_logging)
    design.security_tooling = st.toggle("Org-wide security tooling (GuardDuty / Security Hub / Config)",
                                        value=design.security_tooling)
    design.backup_dr = st.toggle("Centralized backup & DR", value=design.backup_dr)

    if st.button("✨ Auto-suggest a design for my profile", use_container_width=True):
        st.session_state.design = recommend_design(
            design.org_size, design.compliance, design.num_workloads,
            design.num_teams, design.regions,
        )
        st.rerun()

    st.divider()
    st.header("AI Advisor settings")
    provider = st.radio("LLM provider", ["Claude (Anthropic)", "OpenAI"], index=0)
    st.session_state.provider = provider
    secret_name = "ANTHROPIC_API_KEY" if provider == "Claude (Anthropic)" else "OPENAI_API_KEY"
    if llm.get_api_key(provider) is None:
        st.text_input(f"{secret_name}", type="password", key=f"key_{secret_name}",
                      help="Set this in Streamlit secrets for deployed apps.")
    else:
        st.success(f"{provider} key configured", icon="🔑")

    st.divider()
    ui.logout_button()

# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------

scores = score_design(design)
cost = estimate_monthly_cost(design)
n_total = total_accounts(design)
assessment = waf.assess(design)
waf_overall = waf.overall_score(assessment)

# ---------------------------------------------------------------------------
# Hero + tabs
# ---------------------------------------------------------------------------

ui.hero(design, n_total, waf_overall, cost["total"])

tab_design, tab_sim, tab_waf, tab_advisor, tab_ref = st.tabs(
    ["🎨 Design Studio", "🧪 Simulator", "🏛️ Well-Architected", "🤖 AI Advisor", "📚 Reference"])

# ============================== DESIGN STUDIO ==============================

with tab_design:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total AWS accounts", n_total)
    c2.metric("Workload accounts", workload_account_count(design))
    c3.metric("Core / platform accounts", core_account_count(design))
    c4.metric("Est. platform overhead", f"${cost['total']:,.0f}/mo")
    c5.metric("Well-Architected", f"{waf_overall}/100",
              help="Alignment to the six WAF pillars — see the Well-Architected tab.")

    col_org, col_net = st.columns(2)
    with col_org:
        st.subheader("Organization structure")
        st.graphviz_chart(org_structure_diagram(design), use_container_width=True)
    with col_net:
        st.subheader("Network topology")
        st.graphviz_chart(network_diagram(design), use_container_width=True)

        st.subheader("Design scorecard")
        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=list(scores.values()) + [list(scores.values())[0]],
            theta=list(scores.keys()) + [list(scores.keys())[0]],
            fill="toself", name="Current design",
            line_color="#FF9900",
        ))
        fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=False, height=380, margin=dict(l=60, r=60, t=30, b=30),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Recommended guardrails (SCPs)")
    guardrails = recommend_guardrails(design)
    st.dataframe(
        pd.DataFrame(guardrails, columns=["Guardrail", "Mechanism", "Applied to"]),
        use_container_width=True, hide_index=True,
    )

    st.subheader("Estimated monthly platform cost breakdown")
    st.caption("Rough demo estimates only — actuals depend on usage, data volumes, and region.")
    cost_df = pd.DataFrame(
        [(k, v) for k, v in cost["items"].items()], columns=["Item", "USD / month"])
    st.dataframe(cost_df, use_container_width=True, hide_index=True)

# ================================ SIMULATOR ================================

with tab_sim:
    st.subheader("Compare account strategies side-by-side")
    st.caption("Holding your organization profile constant, how does each account strategy score?")

    compare_scores = {}
    compare_meta = []
    for strat in ACCOUNT_STRATEGIES:
        alt = copy.deepcopy(design)
        alt.account_strategy = strat
        s = score_design(alt)
        compare_scores[strat] = s
        compare_meta.append({
            "Strategy": strat,
            "Accounts": total_accounts(alt),
            "Est. cost / mo": f"${estimate_monthly_cost(alt)['total']:,.0f}",
            "Overall (avg)": round(sum(s.values()) / len(s)),
            **s,
        })

    fig = go.Figure()
    palette = ["#FF9900", "#1A476F", "#3F8624", "#B0084D"]
    dims = list(scores.keys())
    for i, (strat, s) in enumerate(compare_scores.items()):
        fig.add_trace(go.Scatterpolar(
            r=[s[d] for d in dims] + [s[dims[0]]],
            theta=dims + [dims[0]],
            name=strat, line_color=palette[i % len(palette)],
        ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        height=480, legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        margin=dict(l=60, r=60, t=30, b=30),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(pd.DataFrame(compare_meta), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Growth stress-test")
    st.caption("What happens to your current design as the number of workloads grows?")
    max_wl = st.slider("Project growth to N workloads", design.num_workloads, 100,
                       min(60, max(20, design.num_workloads * 3)))

    rows = []
    for wl in range(1, max_wl + 1, max(1, max_wl // 25)):
        alt = copy.deepcopy(design)
        alt.num_workloads = wl
        s = score_design(alt)
        rows.append({
            "Workloads": wl,
            "Accounts": total_accounts(alt),
            "Cost / mo": estimate_monthly_cost(alt)["total"],
            "Scalability": s["Scalability"],
            "Operational Simplicity": s["Operational Simplicity"],
        })
    growth_df = pd.DataFrame(rows)

    g1, g2 = st.columns(2)
    with g1:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=growth_df["Workloads"], y=growth_df["Accounts"],
                                  name="Accounts", line=dict(color="#FF9900", width=3)))
        fig2.add_trace(go.Scatter(x=growth_df["Workloads"], y=growth_df["Cost / mo"],
                                  name="Cost ($/mo)", yaxis="y2", line=dict(color="#1A476F", width=3)))
        fig2.update_layout(
            title="Accounts & platform cost vs. growth",
            yaxis=dict(title="Accounts"),
            yaxis2=dict(title="USD / month", overlaying="y", side="right"),
            height=380, legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig2, use_container_width=True)
    with g2:
        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(x=growth_df["Workloads"], y=growth_df["Scalability"],
                                  name="Scalability", line=dict(color="#3F8624", width=3)))
        fig3.add_trace(go.Scatter(x=growth_df["Workloads"], y=growth_df["Operational Simplicity"],
                                  name="Operational Simplicity", line=dict(color="#B0084D", width=3)))
        fig3.update_layout(
            title="Score trajectory vs. growth",
            yaxis=dict(range=[0, 100], title="Score"),
            height=380, legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig3, use_container_width=True)

# ============================ WELL-ARCHITECTED =============================

with tab_waf:
    st.subheader("AWS Well-Architected Framework alignment")
    st.caption("Educational pillar-by-pillar assessment of the current design — "
               "not a substitute for an official Well-Architected Tool review.")

    w1, w2 = st.columns([1, 2])
    with w1:
        st.metric("Overall alignment", f"{waf_overall}/100")
        worst = min(assessment.items(), key=lambda kv: kv[1]["score"])
        st.metric("Weakest pillar", worst[0], delta=f"{worst[1]['score']}/100",
                  delta_color="off")
        n_fail = sum(1 for p in assessment.values() for c in p["checks"] if c["status"] == "fail")
        n_warn = sum(1 for p in assessment.values() for c in p["checks"] if c["status"] == "warn")
        st.metric("Findings", f"{n_fail} fail / {n_warn} warn")
    with w2:
        fig_waf = go.Figure(go.Bar(
            x=[p["score"] for p in assessment.values()],
            y=list(assessment.keys()),
            orientation="h",
            marker_color=["#3F8624" if p["score"] >= 80 else "#FF9900" if p["score"] >= 55
                          else "#B0084D" for p in assessment.values()],
            text=[f"{p['score']}" for p in assessment.values()],
            textposition="outside",
        ))
        fig_waf.update_layout(
            xaxis=dict(range=[0, 110], title="Pillar score"),
            yaxis=dict(autorange="reversed"),
            height=320, margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig_waf, use_container_width=True)

    st.subheader("Top remediations")
    remediations = waf.top_remediations(assessment)
    if remediations:
        st.dataframe(
            pd.DataFrame([{
                "Severity": "❌ Fail" if r["status"] == "fail" else "⚠️ Warn",
                "Pillar": r["pillar"],
                "Practice": f"{r['id']} — {r['title']}",
                "Remediation": r["remediation"],
            } for r in remediations]),
            use_container_width=True, hide_index=True,
        )
    else:
        st.success("No warnings or failures — this design passes every modeled check. 🎉")

    st.subheader("Pillar detail")
    icons = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    for pillar, data in assessment.items():
        with st.expander(f"{pillar} — {data['score']}/100", expanded=data["score"] < 55):
            for c in data["checks"]:
                st.markdown(f"{icons[c['status']]} **{c['id']} — {c['title']}**  \n"
                            f"{c['finding']}")
                if c["status"] != "pass":
                    st.markdown(f"> 💡 {c['remediation']}")

    st.divider()
    report_md = "\n\n".join([
        "# AWS Landing Zone Studio — Design Report",
        llm.design_context_markdown(design.to_dict(), scores, cost, n_total),
        waf.assessment_markdown(assessment),
        "_Generated by AWS Landing Zone Studio. Educational estimates only._",
    ])
    st.download_button("⬇️ Download full design report (Markdown)", report_md,
                       file_name="landing-zone-design-report.md", mime="text/markdown")

# ================================ AI ADVISOR ================================

with tab_advisor:
    provider = st.session_state.get("provider", "Claude (Anthropic)")
    api_key = llm.get_api_key(provider)

    st.subheader(f"AI Advisor — powered by {provider}")
    if api_key is None:
        st.warning("Enter your API key in the sidebar (or configure it in Streamlit secrets) to use the AI Advisor.")
    else:
        context_md = (llm.design_context_markdown(design.to_dict(), scores, cost, n_total)
                      + "\n\n" + waf.assessment_markdown(assessment))

        with st.expander("Design context sent to the model"):
            st.markdown(context_md)

        b1, b2, b3, b4 = st.columns(4)
        prompt_clicked = None
        if b1.button("🔍 Review my design", use_container_width=True):
            prompt_clicked = ("Review this landing zone design. Give a verdict, the top risks, "
                              "concrete prioritized changes, and a migration path.")
        if b2.button("⚖️ Compare with best practice", use_container_width=True):
            prompt_clicked = ("Compare this design against AWS multi-account best practices and the "
                              "Well-Architected Framework. Where does it deviate and does the deviation matter?")
        if b3.button("🛣️ Day-1 / Day-2 roadmap", use_container_width=True):
            prompt_clicked = ("Produce a Day-0/Day-1/Day-2 implementation roadmap for this landing zone: "
                              "what to build first, what can wait, and what to automate.")
        if b4.button("🏛️ Fix my WAF findings", use_container_width=True):
            prompt_clicked = ("Take the Well-Architected assessment findings above (the FAIL and WARN items) "
                              "and produce a prioritized remediation plan: for each finding, the concrete AWS "
                              "services/controls to implement, effort estimate (S/M/L), and what to do first and why.")

        # render history
        for msg in st.session_state.chat:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        user_q = st.chat_input("Ask the advisor anything about your landing zone…")
        question = prompt_clicked or user_q

        if question:
            st.session_state.chat.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)

            # Build LLM messages: design context + chat history
            llm_messages = [{"role": "user",
                             "content": f"{context_md}\n\n---\n\nKeep this design in mind for the whole conversation."},
                            {"role": "assistant",
                             "content": "Understood — I have the design, scores, and cost estimates. What would you like to know?"}]
            llm_messages += st.session_state.chat

            with st.chat_message("assistant"):
                try:
                    answer = st.write_stream(llm.stream_completion(provider, llm_messages, api_key))
                except Exception as e:
                    answer = f"⚠️ LLM call failed: {e}"
                    st.error(answer)
            st.session_state.chat.append({"role": "assistant", "content": answer})

        if st.session_state.chat and st.button("🗑️ Clear conversation"):
            st.session_state.chat = []
            st.rerun()

# ================================ REFERENCE ================================

with tab_ref:
    st.subheader("Landing zone quick reference")
    r1, r2 = st.columns(2)
    with r1:
        st.markdown("""
#### Account strategy trade-offs
| Strategy | Blast radius | Ops overhead | Best for |
|---|---|---|---|
| Single account | Entire org | Lowest | Prototypes only |
| Per environment | One env | Low | Small teams, few workloads |
| Per workload | One workload (all envs) | Medium | SaaS products |
| Per workload per env | One workload+env | Highest | Regulated / enterprise |

#### Governance tooling
- **AWS Control Tower** — managed landing zone: Account Factory, mandatory + elective controls, dashboard. Best default for most orgs.
- **Landing Zone Accelerator (LZA)** — CDK-based, config-driven, deep compliance packs (FedRAMP, PCI, CCCS). Best for regulated industries.
- **Custom Organizations + SCPs** — maximum control, maximum maintenance. Only with a strong platform team.
""")
    with r2:
        st.markdown("""
#### Foundational OU layout (AWS guidance)
- **Security OU** — Log Archive + Security Tooling accounts; first thing you build.
- **Infrastructure OU** — Network (TGW/Cloud WAN), Shared Services.
- **Workloads OU** — Prod / Non-Prod children; SCPs differ by criticality.
- **Sandbox OU** — disconnected experimentation, spend caps.
- **Suspended OU** — deny-all SCP for decommissioned accounts.

#### Non-negotiables for any landing zone
1. Org-wide CloudTrail → immutable Log Archive bucket.
2. Break-glass + MFA on root for every account; no root API keys.
3. SCPs: deny org-leave, deny CloudTrail/Config tampering, region restriction.
4. GuardDuty + Security Hub delegated-admin to Security Tooling account.
5. Federated human access (Identity Center / IdP) — no IAM users.
6. Automated account vending — never hand-built accounts.
""")
    st.info("This tool produces educational simulations and estimates. Validate any real "
            "landing zone design with AWS Well-Architected reviews and your security/compliance teams.")
