# 🏗️ AWS Landing Zone Studio

An interactive **simulator + AI advisor** for designing AWS multi-account Landing Zones.
Play with organization profiles, account strategies, network patterns, and governance
tooling — and instantly see the org structure, network topology, guardrails, scores,
and cost impact. Then get an opinionated review from **Claude** (or OpenAI).

## Features

- **🧭 Guided setup wizard** — a friendly 3-step on-ramp (profile → priorities →
  review) that produces a complete, scored starting design for first-time users
  instead of dropping every control at once.
- **⚡ One-click remediations (closed advisor loop)** — every Well-Architected
  finding has an *Apply fix* button that mutates the working design and re-scores
  instantly, with a live toast showing the WAF delta. Advice becomes action.
- **🕹️ Interactive org diagram** — clickable, draggable force-directed graph
  (streamlit-agraph); click any OU/account node to drill into its purpose and
  guardrails (falls back to the static Graphviz render if the package is absent).
- **🏆 Maturity journey + peer benchmark** — the WAF score becomes a named level
  (Foundational → Exemplary) with a progress bar, a "next best action", and an
  optional radar overlay comparing your design to typical Startup / Mid-market /
  Regulated-Enterprise profiles.
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
- **🚀 IaC — export *and* import** — turn the design into deployable artifacts:
  Terraform (Organizations, OUs, accounts, real SCP policy documents), an LZA
  `organization/accounts/global` config, or a Control Tower checklist with
  Account Factory inputs. **Reverse mode:** upload an existing Terraform / LZA /
  CloudFormation file and the tool infers an approximate design and scores it with
  the same Well-Architected engine (a local, transparent take on the AWS WA IaC
  Analyzer), with a confidence-rated signal table — then load it as your design.
- **📈 Drift & history** — capture point-in-time **target** and **actual** snapshots,
  chart the Well-Architected trajectory over time, and see the per-pillar
  target-vs-actual gap. Durable via SQLite. Actuals can be collected
  **automatically on a schedule** (see *Scheduled drift collection* below).
- **🔗 Share & collaborate** — generate a shareable link that encodes the whole
  design in the URL (`?d=<token>`, no server state), and leave **comments** on
  saved scenarios.
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
| `fixes.py` | One-click remediation engine — maps WAF findings to design mutations |
| `maturity.py` | Maturity levels, next-best-action, and peer benchmark profiles |
| `interactive_diagrams.py` | Clickable streamlit-agraph org graph with node drill-down |
| `wizard.py` | Guided 3-step onboarding setup flow |
| `iac_import.py` | Reverse mode — parse Terraform (python-hcl2 + regex fallback) / LZA / CloudFormation → scored design |
| `sharing.py` | Encode/decode a design to a URL-safe shareable token |
| `drift_collector.py` | Headless CLI — scheduled read-only scan → 'actual' drift snapshot |
| `waf.py` | Well-Architected alignment engine — 25 weighted checks, each mapped to the closest official WAF best practice (`exact`/`adapted`) |
| `diagrams.py` | Graphviz builders for org structure + network topology |
| `llm.py` | Provider layer: Claude (Anthropic SDK, streaming) + OpenAI fallback |
| `ui.py` | Enterprise theme (CSS), branded chrome, animated login gate (single or multi-user) |
| `report.py` | Branded PDF design report (fpdf2) with optional AI executive summary |
| `iac.py` | IaC exporters: Terraform / LZA config / Control Tower checklist |
| `roadmap.py` | Day 0/1/2 phased implementation plan + Gantt |
| `live_aws.py` | Read-only AWS Organizations scanner + estate→design mapping |

## Scheduled drift collection

`drift_collector.py` is a headless CLI that runs the read-only AWS Organizations
scan and writes an **actual** snapshot to the shared store — so the Drift tab
fills in automatically. Point it at the **same** database as the app via
`LZ_DB_PATH`, and give it read-only credentials (explicit keys, `AWS_*` env vars,
or — recommended — an attached EC2/ECS instance role via the default boto3 chain).

```bash
# one-off
LZ_DB_PATH=/data/lz_scenarios.db python drift_collector.py --user operator --region us-east-1 --label nightly

# cron (02:00 daily)
0 2 * * *  cd /opt/landing-zone-studio && LZ_DB_PATH=/data/lz_scenarios.db \
  python drift_collector.py --user operator --region us-east-1
```

On Windows use Task Scheduler. **A ready-made GitHub Action is included** at
[`.github/workflows/drift-collector.yml`](.github/workflows/drift-collector.yml) —
it runs nightly (and on demand), assumes a role via **GitHub OIDC** (no static
keys), and commits the snapshot to `data/lz_scenarios.db`. To enable it:

1. Create an IAM role trusting `token.actions.githubusercontent.com` scoped to
   this repo, with a **read-only** policy (`AWSOrganizationsReadOnlyAccess` +
   `controltower:ListLandingZones`/`GetLandingZone`/`ListEnabledControls`).
2. Add the role ARN as repo secret **`AWS_OIDC_ROLE_ARN`** (optional repo
   variables: `AWS_REGION`, `LZ_USER`).

Until the secret is set the workflow no-ops cleanly. Point the deployed app at the
committed snapshots with `LZ_DB_PATH=data/lz_scenarios.db`. For a server/RDS-backed
deployment, swap the checkout+commit steps for an S3 sync (or write to RDS).

The read-only IAM set is the same one listed in the Live Estate tab
(Organizations `List*`/`Describe*` + `controltower:ListLandingZones`/`GetLandingZone`).

> Reverse-mode IaC parsing prefers **python-hcl2** for robust real-world Terraform
> and automatically falls back to a dependency-free regex parser if it's missing or
> a file fails to parse (the signal table shows which parser ran).

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
