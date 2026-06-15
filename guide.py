"""In-app user guide — a friendly 'how to use this tool' walkthrough.

Rendered as the 📖 Guide tab. Kept separate from app.py so the product logic
stays clean. Self-contained Streamlit rendering; the only side effect is the
optional "launch the wizard" button.
"""

import streamlit as st

import wizard


def _hero():
    st.markdown(
        """
        <div style="padding:.2rem 0 .6rem">
          <div style="font-family:var(--lz-mono);font-size:.7rem;letter-spacing:.26em;
                      text-transform:uppercase;color:var(--lz-amber)">User guide</div>
          <div style="font-family:var(--lz-display);font-size:1.9rem;font-weight:750;
                      color:var(--lz-ink);line-height:1.1;margin:.2rem 0">
            How to use Landing Zone Studio</div>
          <div style="color:var(--lz-mut);max-width:60rem">
            Design an AWS multi-account landing zone, score it against the
            Well-Architected Framework, compare trade-offs, generate Infrastructure
            as Code, and review it with AI — all from the controls on the left and
            the tabs above.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render():
    _hero()

    # ---- Three ways to start ----
    st.subheader("Start in one of three ways")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**🧭 Guided wizard**  \nAnswer 3 short questions and get a "
                    "complete, scored starting design. Best for first-timers.")
        if st.button("Launch the guided wizard", use_container_width=True):
            wizard.launch()
            st.rerun()
    with c2:
        st.markdown("**✨ Auto-suggest**  \nSet your organization profile in the "
                    "sidebar, then click *“Auto-suggest a design for my profile”* "
                    "for a rules-based recommendation you can refine.")
    with c3:
        st.markdown("**🎛️ Manual**  \nPick each choice yourself in the sidebar — "
                    "account strategy, network pattern, identity, governance, and "
                    "guardrail toggles. Everything updates live.")

    st.divider()

    # ---- 60-second workflow ----
    st.subheader("The 60-second workflow")
    st.markdown(
        "1. **Describe your org** in the sidebar — size, compliance, teams, workloads, regions.\n"
        "2. **Choose an architecture** — account strategy, network pattern, identity, governance, "
        "and the logging / security / backup toggles.\n"
        "3. **Read the scorecard** on *Design Studio* — accounts, cost, Well-Architected score, and "
        "your maturity level. Click **“Why these scores?”** to see the rubric.\n"
        "4. **Fix gaps** on *Well-Architected* — each finding has a ⚡ **Apply fix** button that "
        "updates the design and re-scores instantly.\n"
        "5. **Compare & export** — stress-test strategies on *Simulator*, then export Terraform / "
        "LZA / Control Tower on *IaC*, or get an AI review on *AI Advisor*.\n"
        "6. **Save it** on *Scenarios* (durable), and optionally share a link or download the PDF report."
    )

    st.divider()

    # ---- What each tab does ----
    st.subheader("What each tab does")
    tabs = [
        ("🎨 Design Studio",
         "Your cockpit. Shows total accounts, cost, Well-Architected score, and a maturity "
         "level for the **current** design, plus the org structure (click a node to inspect it), "
         "network topology, recommended SCP guardrails, the design scorecard (with a per-factor "
         "breakdown), and a cost breakdown you can expand to see how every number is derived."),
        ("🧪 Simulator",
         "Answers *“what if?”*. Compares all four account strategies side-by-side for your profile, "
         "and runs a growth stress-test showing how accounts, cost, scalability, and ops-simplicity "
         "move as your workload count grows."),
        ("🎬 Playbooks",
         "Scenario-based **enterprise lifecycle simulations** run against your current design as a "
         "golden blueprint: an end-to-end **M&A integration (plug-and-play)** that connects a new "
         "account to a datacenter via **Direct Connect + SD-WAN** and migrates VMs with **AWS MGN**; "
         "M&A replicate-to-N-orgs (with a one-click **replication IaC** download) and absorb-acquired-"
         "accounts; a draggable **hybrid network** topology; **MGN** migration; divestiture; geo / "
         "data-residency expansion; compliance onboarding; and scale-out. Each computes live numbers "
         "(accounts, bandwidth, cost, vending/sync time, score deltas) and a grounded, AWS-aligned "
         "runbook with risks and references."),
        ("🏛️ Well-Architected",
         "A pillar-by-pillar self-assessment (25 checks across the six WAF pillars). Each finding "
         "shows a remediation and the official best practice it maps to (exact/adapted). Use the "
         "⚡ **one-click remediations** to apply a fix and watch the scores update, then download a "
         "branded **PDF report** (optionally with an AI executive summary)."),
        ("🚀 IaC",
         "Two modes. **Export** turns the design into Terraform (Organizations, OUs, accounts, real "
         "SCP documents), an LZA config, or a Control Tower checklist. **Import & score** parses an "
         "existing Terraform / LZA / CloudFormation file and assesses it with the same WAF engine."),
        ("🗺️ Roadmap",
         "A Day-0 / Day-1 / Day-2 implementation timeline derived from your design; durations scale "
         "with account count, governance tooling, and network pattern."),
        ("📡 Live Estate",
         "Connect **read-only** AWS credentials to scan a real AWS Organization. It detects accounts, "
         "OUs, SCPs, and trusted services (with a confidence rating), confirms Control Tower via the "
         "API, scores the real estate, and can load it as your working design to plan a target state."),
        ("📈 Drift",
         "Track change over time. Save **target** and **actual** snapshots, see the Well-Architected "
         "trajectory, and the per-pillar target-vs-actual gap. Actuals can be collected automatically "
         "on a schedule (see the *Automate* expander there)."),
        ("🗂️ Scenarios",
         "Save named designs durably, compare any two side-by-side (radar + delta table), export/import "
         "as JSON, generate a **shareable link**, and leave **comments** for collaborators."),
        ("🤖 AI Advisor",
         "Chat with a principal-architect AI (Claude or OpenAI) that already has your design, scores, "
         "and cost as context. One-click prompts: review the design, compare to best practice, build a "
         "Day-1/2 roadmap, or turn WAF findings into a prioritized remediation plan."),
        ("✅ Checklists",
         "Trackable **project plans** for landing-zone delivery: **Networking (Data Center ↔ AWS)**, "
         "**Control Tower / OU / Accounts / Environments / VPC**, and **VM migration between AWS "
         "accounts (AWS MGN)**. Each is phased with owner, effort, and dependencies; tick tasks off "
         "(progress saved per user), see a **critical-path Gantt with realistic timelines**, and "
         "export as CSV (Excel / MS Project) or Markdown."),
        ("🧮 CIDR",
         "An **IP address planner**. *Allocate* carves a supernet (your IPAM pool) into per-VPC and "
         "per-subnet blocks and shows a treemap of exactly **how the address space is utilized** "
         "(plus usable-host counts, AWS reserving 5/subnet) with a CSV export; *Overlap check* finds "
         "colliding CIDRs across accounts/datacenters (a common M&A pitfall); plus *Inspect* and "
         "*Subdivide* helpers."),
        ("📚 Reference",
         "Quick cheat sheets: account-strategy trade-offs, Control Tower vs LZA vs custom, the "
         "foundational OU layout, and the landing-zone non-negotiables."),
    ]
    for title, body in tabs:
        with st.expander(title):
            st.write(body)

    st.divider()

    # ---- Common workflows ----
    st.subheader("Common workflows")
    with st.expander("🟢 Greenfield — design a new landing zone"):
        st.markdown(
            "1. Run the **guided wizard** (or set your profile and **Auto-suggest**).\n"
            "2. On *Well-Architected*, clear the FAIL/WARN findings with **Apply fix**.\n"
            "3. On *Simulator*, confirm your account strategy holds up as you grow.\n"
            "4. Export **Terraform / LZA / Control Tower** on *IaC* and review it.\n"
            "5. Save the design on *Scenarios* and download the **PDF report** for sign-off.")
    with st.expander("🔵 Brownfield — assess and improve a real estate"):
        st.markdown(
            "1. On *Live Estate*, scan your org with **read-only** credentials.\n"
            "2. Review the detected signals and the estate's Well-Architected score.\n"
            "3. Click **“Load detected estate as my working design”**.\n"
            "4. Improve it (one-click fixes, strategy/network changes) into a **target** design.\n"
            "5. On *Drift*, save the current as a **target** snapshot and schedule **actuals** to "
            "track convergence over time.")
    with st.expander("📥 Review existing IaC"):
        st.markdown(
            "1. On *IaC → Import & score*, upload or paste a Terraform / LZA / CloudFormation file.\n"
            "2. Read the confidence-rated signals and the inferred Well-Architected score.\n"
            "3. Load it as your working design and use the *Well-Architected* tab to find and fix gaps.")

    st.divider()

    # ---- Tips & FAQ ----
    st.subheader("Tips & FAQ")
    with st.expander("Where do the scores and costs come from? Are they official?"):
        st.write(
            "They are transparent **planning estimates**, not official ratings. Scores use a "
            "documented rubric (open the *“Why these scores?”* panel). Costs are derived as "
            "*published AWS list price × a stated usage assumption* — expand *“How each number is "
            "derived”* to trace every figure. WAF checks are mapped to official best practices with "
            "an honest exact/adapted flag. Always validate with the official AWS Well-Architected "
            "Tool and the AWS Pricing Calculator before acting.")
    with st.expander("Do I need an API key or AWS account to use it?"):
        st.write(
            "No. The whole design/simulate/score/export flow works without anything. An **AI key** "
            "(Anthropic or OpenAI, set in the sidebar or secrets) unlocks the *AI Advisor* and the AI "
            "executive summary. **Read-only AWS credentials** are only needed for the *Live Estate* "
            "scan and live pricing overlay.")
    with st.expander("Are my saved scenarios kept?"):
        st.write(
            "Yes — scenarios, snapshots, and comments are stored durably when a database is "
            "configured. On Streamlit Community Cloud, set a `DATABASE_URL` (managed Postgres) for "
            "durability; otherwise the local store is ephemeral there. You can always **export to "
            "JSON** or a **share link** to keep a design.")
    with st.expander("Is anything I enter sent anywhere?"):
        st.write(
            "Your design stays in your session. The *AI Advisor* sends the design context to your "
            "chosen LLM provider when you ask it something. *Live Estate* credentials are used only "
            "for read-only API calls during the scan and are never stored.")

    st.info("Tip: the controls are all in the **sidebar on the left** (scroll it for more). "
            "Hover any metric or button for an inline hint.", icon="💡")
