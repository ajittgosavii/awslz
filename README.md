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
   ```

4. Deploy. `requirements.txt` (Python deps) and `packages.txt` (Graphviz binary)
   are picked up automatically.

## Project layout

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI — tabs, charts, chat |
| `lz_core.py` | Domain logic: account math, scoring model, cost estimates, SCP recommendations, rule-based design suggester |
| `diagrams.py` | Graphviz builders for org structure + network topology |
| `llm.py` | Provider layer: Claude (Anthropic SDK, streaming) + OpenAI fallback |

## Disclaimer

Scores and costs are **educational estimates** for exploring trade-offs — not a
substitute for an AWS Well-Architected review or real pricing analysis.
