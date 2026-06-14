# 🏗️ AWS Landing Zone Studio

An interactive **simulator + AI advisor** for designing AWS multi-account Landing Zones.
Play with organization profiles, account strategies, network patterns, and governance
tooling — and instantly see the org structure, network topology, guardrails, scores,
and cost impact. Then get an opinionated review from **Claude** (or OpenAI).

## Features

- **🎨 Design Studio** — pick org size, compliance frameworks, workloads, environments,
  regions, account strategy, network pattern, identity model, and governance tooling.
  See a live AWS Organizations OU tree, network topology diagram, recommended SCP
  guardrails, a 5-dimension scorecard (radar), and a monthly platform cost estimate.
- **🧪 Simulator** — compare all four account strategies side-by-side for *your*
  profile, and stress-test your design against workload growth (accounts, cost,
  scalability, ops-overhead trajectories).
- **🤖 AI Advisor** — one-click design review, best-practice comparison, and
  Day-0/1/2 roadmap, plus free-form chat. Streams responses from
  **Claude Opus 4.8** (default) or **OpenAI GPT-4o**.
- **✨ Auto-suggest** — rule-based recommendation engine proposes a complete design
  from your organization profile.
- **🏛️ Well-Architected** — pillar-by-pillar alignment assessment (25 checks across
  all six WAF pillars, critical practices weighted), top-remediations table, and a
  downloadable branded **PDF design report** (parameters, scorecards, findings with
  remediations, cost breakdown, guardrails). Every check is **mapped to the closest
  official WAF best practice** with an honest `exact` / `adapted` flag, plus a
  reference mapping table — so results are never mistaken for an official audit.
- **🚀 IaC Export** — turn the design into deployable artifacts: Terraform
  (Organizations, OUs, accounts, real SCP policy documents), an LZA
  `organization/accounts/global` config, or a Control Tower checklist with
  Account Factory inputs.
- **🗺️ Roadmap** — Day 0 / Day 1 / Day 2 implementation timeline (Gantt) derived
  from the design; durations scale with account count, tooling, and network pattern.
- **📡 Live Estate** — read-only `boto3` scan of a real AWS Organization
  (accounts, OUs, SCPs, trusted service access) → detected-signals table **with a
  confidence column** (Confirmed-via-API vs inferred), estate diagram, and a
  Well-Architected score of the *actual* estate; one click loads it as the working
  design to plan the target state. **Control Tower is confirmed via the real
  `controltower` API** (status, version, drift, governed regions) — not guessed —
  and account strategy is inferred from OU/naming **structure**, not just count.
- **🗂️ Scenarios** — save named designs per user with **durable SQLite persistence**
  (survive restarts/sessions), side-by-side comparison (radar + delta table for
  accounts/WAF/cost), JSON export/import.
- **🔐 Login gate** — branded, animated access page. Single key via `APP_PASSWORD`,
  or multi-user via a `[users]` table in secrets; demo mode (access key `awslz`)
  when neither is set.
- **💵 Transparent, sourced costs** — every cost line is derived as a **published AWS
  list price × a stated usage assumption** (no magic constants), includes
  **data-transfer/processing**, and ships with a per-item "how this number is derived"
  basis table citing the price source. Estimates scale with a per-region price index
  (e.g. `sa-east-1` ≈ 1.35× `us-east-1`), and the **Live Estate** tab can overlay
  **real, current prices from the AWS Price List API**. See `pricing.py`.
- **📐 Auditable scores** — the design scorecard exposes a **per-factor breakdown**
  (which choice added/removed how many points) instead of an opaque number.
- **🧠 AI executive summary** — one click generates a CTO-ready summary that is
  embedded as the opening section of the PDF report.
- **📚 Reference** — trade-off cheat sheets: account strategies, Control Tower vs LZA
  vs custom, foundational OU layout, landing-zone non-negotiables.

## Run locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your API key(s)
streamlit run app.py
```

> Diagrams use Graphviz. Locally, install the Graphviz binary if you don't have it
> (`winget install graphviz` / `brew install graphviz` / `apt install graphviz`).
> On Streamlit Cloud this is handled automatically by `packages.txt`.

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → pick the
   repo/branch, main file `app.py`.
3. In **App settings → Secrets**, paste:

   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   # and/or
   OPENAI_API_KEY = "sk-..."
   # login access key (demo mode key is "awslz" if omitted)
   APP_PASSWORD = "choose-a-strong-passphrase"
   ```

4. Deploy. `requirements.txt` (Python deps) and `packages.txt` (Graphviz binary)
   are picked up automatically.

## Project layout

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI — tabs, charts, chat |
| `lz_core.py` | Domain logic: account math, transparent scoring (`explain_scores`), sourced cost estimates, SCP recommendations, rule-based design suggester |
| `pricing.py` | Sourced AWS list prices + stated usage assumptions + optional live AWS Price List API overlay |
| `store.py` | Durable SQLite persistence for saved scenarios (keyed by user) |
| `waf.py` | Well-Architected alignment engine — 25 weighted checks, each mapped to the closest official WAF best practice (`exact`/`adapted`) |
| `diagrams.py` | Graphviz builders for org structure + network topology |
| `llm.py` | Provider layer: Claude (Anthropic SDK, streaming) + OpenAI fallback |
| `ui.py` | Enterprise theme (CSS), branded chrome, animated login gate (single or multi-user) |
| `report.py` | Branded PDF design report (fpdf2) with optional AI executive summary |
| `iac.py` | IaC exporters: Terraform / LZA config / Control Tower checklist |
| `roadmap.py` | Day 0/1/2 phased implementation plan + Gantt |
| `live_aws.py` | Read-only AWS Organizations scanner + estate→design mapping |

## Persistence & configuration

Saved scenarios are stored in a SQLite database. By default this is
`lz_scenarios.db` next to the app; set the `LZ_DB_PATH` environment variable to
relocate it (e.g. to a mounted volume). **Note:** on Streamlit Community Cloud the
container filesystem is ephemeral and is wiped on redeploy/sleep — for durable
multi-user storage there, point `LZ_DB_PATH` at a persistent volume or swap
`store.py`'s connection for a managed database (the module's public API is stable).

## Disclaimer

Scores and costs are **planning estimates** for exploring trade-offs — not a
substitute for an AWS Well-Architected review or an official pricing quote.
The tool is built to be *transparent* about this: cost figures show their
price-source-and-assumption derivation, scores show a per-factor breakdown, WAF
checks are flagged `exact`/`adapted` against the official framework, and live-scan
signals carry a confidence rating. Validate any real landing-zone design with the
official AWS Well-Architected Tool, the AWS Pricing Calculator, and your
security/compliance teams before acting on it.
