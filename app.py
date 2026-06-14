"""AWS Landing Zone Studio — interactive multi-account design simulator + AI advisor.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud (see README.md)
"""

import copy
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import fixes
import iac
import llm
import maturity
import roadmap
import sharing
import store
import ui
import waf
import wizard

try:
    from streamlit_agraph import agraph
    import interactive_diagrams as idiagrams
    _HAS_AGRAPH = True
except Exception:  # streamlit-agraph not installed — fall back to Graphviz
    _HAS_AGRAPH = False
from diagrams import network_diagram, org_structure_diagram
from report import build_pdf_report
from lz_core import (
    ACCOUNT_STRATEGIES, COMPLIANCE_FRAMEWORKS, ENVIRONMENTS, GOVERNANCE_TOOLING,
    IDENTITY_MODELS, NETWORK_PATTERNS, ORG_SIZES, REGIONS,
    LZDesign, core_account_count, estimate_monthly_cost, explain_scores, recommend_design,
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

# Guided onboarding wizard (renders in place of the app while active).
if wizard.run_wizard():
    st.stop()

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "design" not in st.session_state:
    st.session_state.design = LZDesign()
if "chat" not in st.session_state:
    st.session_state.chat = []  # list of {"role", "content"}

# Load a shared design from a ?d=<token> link, once per session.
if "shared_loaded" not in st.session_state:
    _tok = st.query_params.get("d")
    if _tok:
        _shared = sharing.decode_design(_tok)
        if _shared:
            st.session_state.design = LZDesign(**_shared)
            st.session_state._flash = "Loaded a shared design from the link."
    st.session_state.shared_loaded = True

_user = st.session_state.get("lz_user", "operator")
_scen_key = f"scenarios_{_user}"
_PERSIST = store.available()  # SQLite-backed durability when the store is writable
if _scen_key not in st.session_state:
    st.session_state[_scen_key] = {}  # name -> design dict (session fallback)


def _load_scenarios() -> dict:
    """Saved scenarios for the current user — durable store if available."""
    if _PERSIST:
        return store.load_scenarios(_user)
    return st.session_state[_scen_key]


def _save_scenario(name: str, ddict: dict):
    if _PERSIST:
        store.save_scenario(_user, name, ddict)
    else:
        st.session_state[_scen_key][name] = ddict


def _delete_scenario(name: str):
    if _PERSIST:
        store.delete_scenario(_user, name)
    else:
        st.session_state[_scen_key].pop(name, None)

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

    if st.button("🧭 Guided setup (wizard)", use_container_width=True):
        wizard.launch()
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
cost = estimate_monthly_cost(design, st.session_state.get("live_prices"))
n_total = total_accounts(design)
assessment = waf.assess(design)
waf_overall = waf.overall_score(assessment)

# ---------------------------------------------------------------------------
# Hero + tabs
# ---------------------------------------------------------------------------

ui.hero(design, n_total, waf_overall, cost["total"])

# One-click-fix feedback survives the rerun via a session flash.
if st.session_state.get("_flash"):
    st.toast(st.session_state.pop("_flash"), icon="✅")

(tab_design, tab_sim, tab_waf, tab_iac, tab_road, tab_live, tab_drift,
 tab_scen, tab_advisor, tab_ref) = st.tabs(
    ["🎨 Design Studio", "🧪 Simulator", "🏛️ Well-Architected", "🚀 IaC",
     "🗺️ Roadmap", "📡 Live Estate", "📈 Drift", "🗂️ Scenarios", "🤖 AI Advisor", "📚 Reference"])

# ============================== DESIGN STUDIO ==============================

with tab_design:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total AWS accounts", n_total)
    c2.metric("Workload accounts", workload_account_count(design))
    c3.metric("Core / platform accounts", core_account_count(design))
    c4.metric("Est. platform overhead", f"${cost['total']:,.0f}/mo")
    c5.metric("Well-Architected", f"{waf_overall}/100",
              help="Alignment to the six WAF pillars — see the Well-Architected tab.")

    # --- Maturity journey banner ---
    _lvl = maturity.level_for(waf_overall)
    mb1, mb2 = st.columns([1, 2])
    with mb1:
        st.markdown(
            f"<div style='font-family:monospace;font-size:.7rem;letter-spacing:.12em;"
            f"color:#8C9CB8'>MATURITY · LEVEL {_lvl['level']}/{_lvl['max_level']}</div>"
            f"<div style='font-family:Bricolage Grotesque,serif;font-size:1.6rem;"
            f"color:#E9EEF7'>{_lvl['icon']} {_lvl['name']}</div>"
            f"<div style='color:#8C9CB8;font-size:.85rem'>{_lvl['blurb']}</div>",
            unsafe_allow_html=True)
    with mb2:
        if _lvl["next_name"]:
            st.progress(_lvl["progress_to_next"],
                        text=f"{_lvl['points_to_next']} pts to **{_lvl['next_name']}**")
        else:
            st.progress(1.0, text="Top maturity level reached 🏆")
        _next = waf.top_remediations(assessment, limit=1)
        if _next:
            st.markdown(f"**🎯 Next best action:** {_next[0]['id']} — {_next[0]['title']}  \n"
                        f"<span style='color:#8C9CB8;font-size:.85rem'>{_next[0]['remediation']}</span>",
                        unsafe_allow_html=True)
            st.caption("Apply it with one click in the 🏛️ Well-Architected tab.")
    st.divider()

    col_org, col_net = st.columns(2)
    with col_org:
        st.subheader("Organization structure")
        if _HAS_AGRAPH:
            _nodes, _edges, _cfg, _det = idiagrams.build_org_graph(design)
            _clicked = agraph(nodes=_nodes, edges=_edges, config=_cfg)
            if _clicked and _clicked in _det:
                st.info(_det[_clicked])
            else:
                st.caption("💡 Click a node to inspect its purpose and guardrails · drag to rearrange.")
        else:
            st.graphviz_chart(org_structure_diagram(design), use_container_width=True)
    with col_net:
        st.subheader("Network topology")
        st.graphviz_chart(network_diagram(design), use_container_width=True)

        st.subheader("Design scorecard")
        show_peers = st.checkbox("Overlay peer benchmarks", value=False,
                                 help="Compare against typical designs for each org shape.")
        dims_r = list(scores.keys())
        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=list(scores.values()) + [list(scores.values())[0]],
            theta=dims_r + [dims_r[0]],
            fill="toself", name="Current design",
            line_color="#FF9900",
        ))
        if show_peers:
            peer_palette = {"Typical Startup": "#2DD4BF", "Typical Mid-market": "#5B8DEF",
                            "Regulated Enterprise": "#B0084D"}
            for pname, pscores in maturity.peer_scores().items():
                fig.add_trace(go.Scatterpolar(
                    r=[pscores[d] for d in dims_r] + [pscores[dims_r[0]]],
                    theta=dims_r + [dims_r[0]], name=pname,
                    line=dict(color=peer_palette.get(pname, "#888"), dash="dot")))
        fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=show_peers, legend=dict(orientation="h", y=-0.2),
            height=420 if show_peers else 380, margin=dict(l=60, r=60, t=30, b=30),
        )
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("📐 Why these scores? (auditable rubric)"):
            breakdown = explain_scores(design)
            for dim, factors in breakdown.items():
                st.markdown(f"**{dim} — {scores[dim]}/100**")
                bd = pd.DataFrame([{"Factor": f["factor"],
                                    "Points": (f"+{f['delta']}" if f["delta"] >= 0 else str(f["delta"]))}
                                   for f in factors])
                st.dataframe(bd, use_container_width=True, hide_index=True)
            st.caption("Scores are a transparent rubric for exploring trade-offs (each factor's "
                       "weight is shown above), not an official AWS rating.")

    st.subheader("Recommended guardrails (SCPs)")
    guardrails = recommend_guardrails(design)
    st.dataframe(
        pd.DataFrame(guardrails, columns=["Guardrail", "Mechanism", "Applied to"]),
        use_container_width=True, hide_index=True,
    )

    st.subheader("Estimated monthly platform cost breakdown")
    st.caption(f"Basis: {cost['price_source']} × stated usage assumptions, scaled by a "
               f"region price index of {cost['region_index']}×. Planning estimate — verify with "
               "the AWS Pricing Calculator. See the derivation below.")
    cost_df = pd.DataFrame(
        [(k, v) for k, v in cost["items"].items()], columns=["Item", "USD / month"])
    st.dataframe(cost_df, use_container_width=True, hide_index=True)

    with st.expander("🧾 How each number is derived (price × assumption × region index)"):
        basis_df = pd.DataFrame([
            {"Item": b["item"], "USD/mo": b["monthly"], "Formula": b["formula"],
             "Price source": b["source"]} for b in cost["basis"]])
        st.dataframe(basis_df, use_container_width=True, hide_index=True)
        st.caption("Every figure traces to a published AWS list price and an explicit usage "
                   "assumption (see pricing.py). Use the Live Estate tab to overlay real, current "
                   "regional prices from the AWS Price List API.")

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

    @st.fragment
    def _growth_stress_test():
        st.subheader("Growth stress-test")
        st.caption("What happens to your current design as the number of workloads grows? "
                   "_(The slider reruns only this panel — instant.)_")
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

    _growth_stress_test()

# ============================ WELL-ARCHITECTED =============================

with tab_waf:
    st.subheader("AWS Well-Architected Framework alignment")
    st.caption("Educational pillar-by-pillar assessment of the current design — "
               "not a substitute for an official Well-Architected Tool review.")
    st.info(waf.WAF_DISCLAIMER, icon="ℹ️")
    with st.expander("🔗 How Studio checks map to official WAF best practices"):
        st.caption("‘≈ adapted’ = a landing-zone interpretation in that practice area, not a 1:1 "
                   "official best practice. Validate with the official Well-Architected Tool before "
                   "using as audit evidence.")
        st.dataframe(pd.DataFrame(waf.mapping_table()), use_container_width=True, hide_index=True)
        st.markdown(f"[Official AWS Well-Architected Framework]({waf.WAF_REFERENCE_URL})")

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

    # --- One-click remediations (close the advisor loop) ---
    fixable, _seen = [], set()
    for r in remediations:
        fa = fixes.fix_for(r["id"])
        if fa and not fixes.is_already_satisfied(design, r["id"]) and fa["label"] not in _seen:
            _seen.add(fa["label"])
            fixable.append(r)
    if fixable:
        st.subheader("⚡ One-click remediations")
        st.caption("Apply a recommended change to the working design and watch every "
                   "score update instantly — no manual editing.")
        for r in fixable:
            fa = fixes.fix_for(r["id"])
            fc1, fc2 = st.columns([3, 1])
            sev = "❌" if r["status"] == "fail" else "⚠️"
            fc1.markdown(f"{sev} **{r['id']} — {r['title']}**  \n"
                         f"<span style='color:#8C9CB8;font-size:.85rem'>→ {fa['label']}</span>",
                         unsafe_allow_html=True)
            with fc2:
                st.write("")
                if st.button("⚡ Apply fix", key=f"fix_{r['id']}", use_container_width=True):
                    new_design, action = fixes.apply_fix(design, r["id"])
                    new_overall = waf.overall_score(waf.assess(new_design))
                    delta = new_overall - waf_overall
                    st.session_state.design = new_design
                    st.session_state._flash = (
                        f"{action['label']} · WAF {waf_overall}→{new_overall} "
                        f"({'+' if delta >= 0 else ''}{delta})")
                    st.rerun()

    st.subheader("Pillar detail")
    icons = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
    for pillar, data in assessment.items():
        with st.expander(f"{pillar} — {data['score']}/100", expanded=data["score"] < 55):
            for c in data["checks"]:
                tag = "✓ exact" if c.get("match") == "exact" else "≈ adapted"
                st.markdown(f"{icons[c['status']]} **{c['id']} — {c['title']}**  \n"
                            f"{c['finding']}  \n"
                            f"<span style='color:#8C9CB8;font-size:.8rem'>maps to {c.get('official','')} "
                            f"· {tag}</span>", unsafe_allow_html=True)
                if c["status"] != "pass":
                    st.markdown(f"> 💡 {c['remediation']}")

    st.divider()
    st.subheader("Design report")
    rep1, rep2 = st.columns([1, 1])
    with rep1:
        _prov = st.session_state.get("provider", "Claude (Anthropic)")
        _key = llm.get_api_key(_prov)
        if st.button("🧠 Generate AI executive summary for the report",
                     disabled=_key is None,
                     help="Adds a CTO-ready one-page summary as the first section of the PDF."):
            ctx = (llm.design_context_markdown(design.to_dict(), scores, cost, n_total)
                   + "\n\n" + waf.assessment_markdown(assessment))
            with st.spinner(f"Asking {_prov} for an executive summary…"):
                try:
                    st.session_state.exec_summary = llm.complete(
                        _prov, [{"role": "user", "content": ctx + "\n\n" + llm.EXEC_SUMMARY_PROMPT}], _key)
                except Exception as e:
                    st.error(f"LLM call failed: {e}")
        if _key is None:
            st.caption("Add an API key in the sidebar to enable the AI executive summary.")
        if st.session_state.get("exec_summary"):
            with st.expander("Preview executive summary (included in PDF)"):
                st.markdown(st.session_state.exec_summary)
    with rep2:
        pdf_bytes = build_pdf_report(design.to_dict(), scores, cost, n_total,
                                     assessment, waf_overall, recommend_guardrails(design),
                                     exec_summary=st.session_state.get("exec_summary"))
        st.download_button("⬇️ Download full design report (PDF)", pdf_bytes,
                           file_name="landing-zone-design-report.pdf", mime="application/pdf",
                           use_container_width=True)
        if st.session_state.get("exec_summary"):
            st.caption("✅ AI executive summary will be included as the opening section.")

# ================================ IAC EXPORT ================================

with tab_iac:
    iac_mode = st.radio("Mode", ["⬇️ Export design → IaC", "📥 Import IaC → assess"],
                        horizontal=True, label_visibility="collapsed")

    if iac_mode.startswith("⬇️"):
        st.subheader("Infrastructure as Code export")
        st.caption("Deterministic, reviewable starting points generated from the current design — "
                   "always review and adapt before applying to a real organization.")

        fmt = st.radio("Format", ["Terraform", "Landing Zone Accelerator (LZA)", "Control Tower checklist"],
                       horizontal=True)
        if fmt == "Terraform":
            tf = iac.terraform_export(design)
            st.download_button("⬇️ Download main.tf", tf, file_name="main.tf", mime="text/plain")
            st.code(tf, language="hcl")
        elif fmt == "Landing Zone Accelerator (LZA)":
            yml = iac.lza_config(design)
            st.download_button("⬇️ Download lza-config.yaml", yml,
                               file_name="lza-config.yaml", mime="text/yaml")
            st.code(yml, language="yaml")
        else:
            md = iac.control_tower_checklist(design)
            st.download_button("⬇️ Download control-tower-checklist.md", md,
                               file_name="control-tower-checklist.md", mime="text/markdown")
            st.markdown(md)

    else:
        import iac_import
        st.subheader("Assess existing IaC")
        st.caption("Upload a Terraform (`.tf`), Landing Zone Accelerator config (`.yaml`), or "
                   "CloudFormation (`.yaml`/`.json`) file. We infer an approximate design from "
                   "what it declares and score it with the same Well-Architected engine — a local, "
                   "transparent take on the AWS WA IaC Analyzer.")
        up = st.file_uploader("IaC file", type=["tf", "yaml", "yml", "json"])
        pasted = st.text_area("…or paste IaC here", height=140, placeholder="resource \"aws_organizations_organization\" ...")
        raw, fname = None, "pasted.tf"
        if up is not None:
            raw, fname = up.getvalue().decode("utf-8", "replace"), up.name
        elif pasted.strip():
            raw = pasted

        if raw:
            res = iac_import.parse_iac(fname, raw)
            if not res.get("ok"):
                st.error(res.get("error", "Parse failed."))
            else:
                st.success(f"Parsed as **{res['format'].upper()}**. "
                           "Detected signals below (with confidence).", icon="✅")
                imp_design = res["mapped_design"]
                imp_assess = waf.assess(imp_design)
                imp_overall = waf.overall_score(imp_assess)

                ic1, ic2 = st.columns([2, 1])
                with ic1:
                    st.dataframe(pd.DataFrame(res["signals"]), use_container_width=True, hide_index=True)
                with ic2:
                    st.metric("Inferred Well-Architected", f"{imp_overall}/100")
                    _ilvl = maturity.level_for(imp_overall)
                    st.metric("Maturity", f"{_ilvl['icon']} {_ilvl['name']}")
                    if res.get("guardrails"):
                        st.caption("Guardrails seen: " + ", ".join(res["guardrails"][:6]))
                if res.get("undetectable"):
                    st.caption("Not detectable from this file: " + "; ".join(res["undetectable"]) + ".")

                fig_imp = go.Figure(go.Bar(
                    x=[p["score"] for p in imp_assess.values()],
                    y=list(imp_assess.keys()), orientation="h",
                    marker_color=["#3F8624" if p["score"] >= 80 else "#FF9900" if p["score"] >= 55
                                  else "#B0084D" for p in imp_assess.values()],
                    text=[f"{p['score']}" for p in imp_assess.values()], textposition="outside"))
                fig_imp.update_layout(xaxis=dict(range=[0, 110]), yaxis=dict(autorange="reversed"),
                                      height=300, margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(fig_imp, use_container_width=True)

                if st.button("⬅️ Load parsed IaC as my working design", use_container_width=True):
                    st.session_state.design = imp_design
                    st.session_state._flash = f"Loaded design from {res['format'].upper()} (WAF {imp_overall})"
                    st.rerun()

# ================================= ROADMAP ==================================

with tab_road:
    st.subheader("Day 0 / Day 1 / Day 2 implementation roadmap")
    st.caption("Phased plan derived from your design — durations are indicative and scale "
               "with account count, governance tooling, and network pattern.")
    st.plotly_chart(roadmap.timeline_figure(design), use_container_width=True)
    with st.expander("Roadmap as a table"):
        st.dataframe(roadmap.plan_dataframe(design), use_container_width=True, hide_index=True)

# ================================ LIVE ESTATE ===============================

with tab_live:
    st.subheader("Assess your real AWS estate")
    st.caption("Read-only scan of AWS Organizations — maps your actual estate onto the same "
               "Well-Architected assessment used for designs.")
    st.warning("Use **temporary, read-only** credentials (e.g. an STS session for a role with "
               "`AWSOrganizationsReadOnlyAccess`). Credentials stay in this session only and are "
               "never stored.", icon="🔒")
    with st.expander("IAM permissions used (all read-only)"):
        import live_aws as _law
        st.code("\n".join(_law.READONLY_CALLS), language="text")

    with st.form("live_creds"):
        lc1, lc2 = st.columns(2)
        ak = lc1.text_input("Access key ID")
        sk = lc2.text_input("Secret access key", type="password")
        tok = st.text_input("Session token (for temporary credentials)", type="password")
        reg = st.selectbox("Region for API calls", REGIONS, index=0)
        want_prices = st.checkbox(
            "Also overlay live AWS prices (Price List API — needs pricing:GetProducts)",
            value=False)
        scan_now = st.form_submit_button("📡 Scan organization (read-only)")

    if scan_now:
        if not ak or not sk:
            st.error("Access key ID and secret access key are required.")
        else:
            import live_aws
            import pricing
            with st.spinner("Scanning AWS Organizations…"):
                st.session_state.live_scan = live_aws.scan_organization(ak, sk, tok, reg)
            if want_prices:
                with st.spinner("Fetching live prices from the AWS Price List API…"):
                    lp = pricing.fetch_live_prices(ak, sk, tok, reg)
                if lp.get("ok"):
                    st.session_state.live_prices = lp["prices"]
                    st.success(f"Live prices overlaid for {', '.join(lp['prices'])} "
                               f"({reg}). All cost estimates now use them.", icon="💲")
                else:
                    st.warning("Live pricing unavailable: "
                               + "; ".join(lp.get("errors", ["unknown error"])))

    scan = st.session_state.get("live_scan")
    if scan:
        if not scan.get("ok"):
            st.error(scan.get("error", "Scan failed."))
        else:
            import live_aws
            st.success(f"Organization **{scan['org']['Id']}** — "
                       f"{len(scan['accounts'])} accounts, {len(scan['ous'])} OUs, "
                       f"{len(scan['policies'])} SCPs", icon="✅")

            ctd = scan.get("control_tower", {}) or {}
            if ctd.get("confirmed"):
                gr = ", ".join(ctd.get("governed_regions", []) or []) or "n/a"
                st.success(
                    f"**Control Tower confirmed via API** — status `{ctd.get('status')}`, "
                    f"version `{ctd.get('version')}`, drift `{ctd.get('drift')}`, "
                    f"governed regions: {gr}.", icon="🛡️")
            else:
                st.info(f"Control Tower not confirmed via API ({ctd.get('error', 'no landing zone')}). "
                        "Governance below is inferred from org structure.", icon="ℹ️")
            st.caption("Confidence column: **Confirmed** = authoritative API; "
                       "**Inferred** = heuristic from names/structure.")
            for w in scan.get("warnings", []):
                st.caption(f"⚠️ partial: {w}")

            lcol, rcol = st.columns(2)
            with lcol:
                st.markdown("**Detected estate**")
                st.graphviz_chart(live_aws.estate_diagram(scan), use_container_width=True)
            with rcol:
                st.markdown("**Detected signals**")
                st.dataframe(pd.DataFrame(scan["signals"]),
                             use_container_width=True, hide_index=True)

            mapped = scan["mapped_design"]
            live_assessment = waf.assess(mapped)
            live_overall = waf.overall_score(live_assessment)
            st.subheader(f"Well-Architected alignment of your estate: {live_overall}/100")
            st.caption("Approximation — signals not visible from Organizations alone: "
                       + "; ".join(scan["undetectable"]) + ".")
            fig_live = go.Figure(go.Bar(
                x=[p["score"] for p in live_assessment.values()],
                y=list(live_assessment.keys()), orientation="h",
                marker_color=["#3F8624" if p["score"] >= 80 else "#FF9900" if p["score"] >= 55
                              else "#B0084D" for p in live_assessment.values()],
                text=[f"{p['score']}" for p in live_assessment.values()],
                textposition="outside"))
            fig_live.update_layout(xaxis=dict(range=[0, 110]), yaxis=dict(autorange="reversed"),
                                   height=300, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_live, use_container_width=True)

            if st.button("⬅️ Load detected estate as my working design"):
                st.session_state.design = mapped
                st.rerun()

# ================================== DRIFT ===================================

with tab_drift:
    st.subheader("Drift & history")
    st.caption("Capture point-in-time snapshots of your design (**target**) and your real "
               "estate (**actual** — load a scan from Live Estate first), then track the "
               "Well-Architected trajectory and the target-vs-actual gap over time.")
    if not _PERSIST:
        st.info("Durable store unavailable — snapshots require SQLite persistence "
                "(set LZ_DB_PATH to a writable path).", icon="ℹ️")
    else:
        dc1, dc2, dc3 = st.columns([2, 1, 1])
        snap_label = dc1.text_input("Snapshot label", placeholder="e.g. Q3 baseline",
                                    key="snap_label")
        if dc2.button("📌 Save as TARGET", use_container_width=True, disabled=not snap_label):
            store.save_snapshot(_user, snap_label, "target", design.to_dict(), waf_overall, scores)
            st.session_state._flash = f"Saved target snapshot '{snap_label}'"
            st.rerun()
        if dc3.button("📡 Save as ACTUAL", use_container_width=True, disabled=not snap_label):
            store.save_snapshot(_user, snap_label, "actual", design.to_dict(), waf_overall, scores)
            st.session_state._flash = f"Saved actual snapshot '{snap_label}'"
            st.rerun()

        snaps = store.load_snapshots(_user)
        if not snaps:
            st.info("No snapshots yet. Save the current design as a **target**, then evolve it "
                    "(or load a live scan as your working design) and save **actuals** to see drift.")
        else:
            hist_df = pd.DataFrame([{
                "When": s["ts"][:16].replace("T", " "), "Label": s["label"],
                "Kind": s["kind"], "WAF": s["waf_overall"]} for s in snaps])
            st.dataframe(hist_df, use_container_width=True, hide_index=True)

            fig_traj = go.Figure()
            for kind, color in (("target", "#FF9900"), ("actual", "#2DD4BF")):
                pts = [s for s in snaps if s["kind"] == kind]
                if pts:
                    fig_traj.add_trace(go.Scatter(
                        x=[p["ts"][:16].replace("T", " ") for p in pts],
                        y=[p["waf_overall"] for p in pts], mode="lines+markers",
                        name=kind, line=dict(color=color, width=3)))
            fig_traj.update_layout(title="Well-Architected trajectory",
                                   yaxis=dict(range=[0, 100], title="WAF /100"),
                                   height=340, legend=dict(orientation="h", y=-0.2))
            st.plotly_chart(fig_traj, use_container_width=True)

            targets = [s for s in snaps if s["kind"] == "target"]
            actuals = [s for s in snaps if s["kind"] == "actual"]
            if targets and actuals:
                t, a = targets[-1], actuals[-1]
                st.subheader(f"Target vs actual — '{t['label']}' vs '{a['label']}'")
                dims_d = list(t["scores"].keys())
                gap_df = pd.DataFrame(
                    [{"Dimension": k, "Target": t["scores"][k], "Actual": a["scores"][k],
                      "Gap (A−T)": a["scores"][k] - t["scores"][k]} for k in dims_d]
                    + [{"Dimension": "WAF overall", "Target": t["waf_overall"],
                        "Actual": a["waf_overall"], "Gap (A−T)": a["waf_overall"] - t["waf_overall"]}])
                st.dataframe(gap_df, use_container_width=True, hide_index=True)
                worst = min(dims_d, key=lambda k: a["scores"][k] - t["scores"][k])
                if a["scores"][worst] - t["scores"][worst] < 0:
                    st.warning(f"Largest gap: **{worst}** is {t['scores'][worst] - a['scores'][worst]} "
                               "points below target.", icon="⚠️")
                else:
                    st.success("Actual meets or exceeds target on every dimension. 🎉")

            with st.expander("Manage snapshots"):
                pick = st.selectbox("Snapshot",
                                    [f"{s['ts']} · {s['label']} ({s['kind']})" for s in snaps])
                if st.button("🗑️ Delete selected snapshot"):
                    store.delete_snapshot(_user, pick.split(" · ")[0])
                    st.rerun()

# ================================= SCENARIOS ================================

with tab_scen:
    st.subheader(f"Scenario manager — {_user}")

    # --- Share & collaborate ---
    with st.expander("🔗 Share this design (link) & comments"):
        token = sharing.encode_design(design.to_dict())
        if st.button("Generate shareable link"):
            st.query_params["d"] = token
            st.toast("Share link set in the address bar — copy the URL.", icon="🔗")
        st.caption("Copy the browser URL after clicking, or share the token below "
                   "(paste it into another session's URL as `?d=<token>`).")
        st.code(token, language="text")
    if _PERSIST:
        st.caption("Save named design scenarios, compare them side-by-side, and export/import as "
                   "JSON. Scenarios are **saved durably** (SQLite) per user and persist across "
                   "sessions and restarts.")
    else:
        st.caption("Save named design scenarios, compare them side-by-side, and export/import as "
                   "JSON. ⚠️ Durable store unavailable — scenarios live in this session only; "
                   "export to keep them.")

    sc1, sc2 = st.columns([2, 1])
    with sc1:
        new_name = st.text_input("Scenario name", placeholder="e.g. target-state-2027")
    with sc2:
        st.write("")
        if st.button("💾 Save current design", use_container_width=True, disabled=not new_name):
            _save_scenario(new_name, design.to_dict())
            st.success(f"Saved '{new_name}'")
            st.rerun()

    scenarios = _load_scenarios()
    if scenarios:
        rows = []
        for name, ddict in scenarios.items():
            sd = LZDesign(**ddict)
            sa = waf.assess(sd)
            rows.append({"Scenario": name, "Strategy": sd.account_strategy,
                         "Accounts": total_accounts(sd),
                         "WAF": waf.overall_score(sa),
                         "Cost/mo": f"${estimate_monthly_cost(sd)['total']:,.0f}"})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        m1, m2, m3 = st.columns(3)
        pick = m1.selectbox("Scenario", list(scenarios.keys()))
        if m2.button("📂 Load", use_container_width=True):
            st.session_state.design = LZDesign(**scenarios[pick])
            st.rerun()
        if m3.button("🗑️ Delete", use_container_width=True):
            _delete_scenario(pick)
            st.rerun()

        if _PERSIST and pick:
            st.markdown(f"**💬 Comments on '{pick}'**")
            _comments = store.load_comments(pick)
            for cm in _comments:
                st.markdown(
                    f"<span style='color:#8C9CB8;font-size:.72rem'>"
                    f"{cm['ts'][:16].replace('T', ' ')} · {cm['author']}</span>  \n{cm['text']}",
                    unsafe_allow_html=True)
            if not _comments:
                st.caption("No comments yet — start the thread.")
            nc1, nc2 = st.columns([3, 1])
            new_comment = nc1.text_input("Add a comment", key=f"cmt_{pick}",
                                         label_visibility="collapsed", placeholder="Add a comment…")
            if nc2.button("Post", use_container_width=True, disabled=not new_comment):
                store.add_comment(pick, _user, new_comment)
                st.rerun()

        if len(scenarios) >= 2:
            st.divider()
            st.subheader("Compare two scenarios")
            cc1, cc2 = st.columns(2)
            names = list(scenarios.keys())
            a_name = cc1.selectbox("Scenario A", names, index=0)
            b_name = cc2.selectbox("Scenario B", names, index=1)
            if a_name != b_name:
                da, db = LZDesign(**scenarios[a_name]), LZDesign(**scenarios[b_name])
                sa_, sb_ = score_design(da), score_design(db)
                dims = list(sa_.keys())
                fig_cmp = go.Figure()
                for nm, sc_, col in ((a_name, sa_, "#FF9900"), (b_name, sb_, "#2DD4BF")):
                    fig_cmp.add_trace(go.Scatterpolar(
                        r=[sc_[x] for x in dims] + [sc_[dims[0]]],
                        theta=dims + [dims[0]], name=nm, line_color=col))
                fig_cmp.update_layout(polar=dict(radialaxis=dict(range=[0, 100])),
                                      height=420, legend=dict(orientation="h", y=-0.15))
                st.plotly_chart(fig_cmp, use_container_width=True)

                wa, wb = waf.overall_score(waf.assess(da)), waf.overall_score(waf.assess(db))
                ca, cb = estimate_monthly_cost(da)["total"], estimate_monthly_cost(db)["total"]
                delta_df = pd.DataFrame([
                    {"Metric": "Total accounts", a_name: total_accounts(da),
                     b_name: total_accounts(db), "Δ (B−A)": total_accounts(db) - total_accounts(da)},
                    {"Metric": "Well-Architected /100", a_name: wa, b_name: wb, "Δ (B−A)": wb - wa},
                    {"Metric": "Platform cost $/mo", a_name: round(ca), b_name: round(cb),
                     "Δ (B−A)": round(cb - ca)},
                ] + [{"Metric": k, a_name: sa_[k], b_name: sb_[k], "Δ (B−A)": sb_[k] - sa_[k]}
                     for k in dims])
                st.dataframe(delta_df, use_container_width=True, hide_index=True)

        st.divider()
        ex1, ex2 = st.columns(2)
        with ex1:
            st.download_button("⬇️ Export all scenarios (JSON)",
                               json.dumps({"user": _user, "scenarios": scenarios}, indent=2),
                               file_name="lz-scenarios.json", mime="application/json",
                               use_container_width=True)
        with ex2:
            up = st.file_uploader("Import scenarios JSON", type="json", label_visibility="collapsed")
            if up is not None:
                try:
                    data = json.load(up)
                    imported = data.get("scenarios", {})
                    valid = {k: v for k, v in imported.items() if isinstance(v, dict)}
                    # validate each by constructing
                    for k, v in list(valid.items()):
                        try:
                            LZDesign(**v)
                        except TypeError:
                            del valid[k]
                    for k, v in valid.items():
                        _save_scenario(k, v)
                    st.success(f"Imported {len(valid)} scenario(s).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Import failed: {e}")
    else:
        st.info("No saved scenarios yet — set a name and save the current design.")

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
