"""LLM provider layer — Claude (default) and OpenAI, both streaming.

API keys are resolved from Streamlit secrets first, then sidebar input.
"""

import streamlit as st

CLAUDE_MODEL = "claude-opus-4-8"
OPENAI_MODEL = "gpt-4o"

SYSTEM_PROMPT = """You are a principal AWS solutions architect specializing in \
Landing Zones, AWS Organizations, Control Tower, and the Landing Zone Accelerator (LZA). \
You advise customers on multi-account strategy, OU design, SCP guardrails, network \
architecture (Transit Gateway, Cloud WAN, centralized egress), identity (IAM Identity \
Center, federation), and centralized logging/security tooling.

You will receive the customer's current landing-zone design as structured data, plus \
computed scores and cost estimates from a simulator. Ground every recommendation in the \
AWS Well-Architected Framework and AWS multi-account best practices (Security OU first, \
log archive immutability, blast-radius reduction, account vending automation).

Be direct and opinionated. Call out risks plainly (e.g. single-account designs for \
regulated workloads). Structure reviews as: verdict first, then top risks, then concrete \
changes in priority order, then a suggested migration path. Keep responses focused — \
no generic AWS marketing language."""


def get_api_key(provider: str) -> str | None:
    """Resolve API key: st.secrets first, then session (sidebar input)."""
    secret_name = "ANTHROPIC_API_KEY" if provider == "Claude (Anthropic)" else "OPENAI_API_KEY"
    try:
        if secret_name in st.secrets:
            return st.secrets[secret_name]
    except Exception:
        pass
    return st.session_state.get(f"key_{secret_name}") or None


def stream_completion(provider: str, messages: list[dict], api_key: str):
    """Yield response text chunks for the given provider.

    `messages` is a list of {"role": "user"|"assistant", "content": str}.
    """
    if provider == "Claude (Anthropic)":
        yield from _stream_claude(messages, api_key)
    else:
        yield from _stream_openai(messages, api_key)


def _stream_claude(messages: list[dict], api_key: str):
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=64000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def _stream_openai(messages: list[dict], api_key: str):
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    oai_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    stream = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=oai_messages,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


def design_context_markdown(design_dict: dict, scores: dict, cost: dict, n_accounts: int) -> str:
    """Build the design summary passed to the LLM as context."""
    lines = ["## Current Landing Zone Design\n"]
    for k, v in design_dict.items():
        lines.append(f"- **{k}**: {v}")
    lines.append(f"\n## Simulator Output\n- **Total accounts**: {n_accounts}")
    lines.append("- **Scores (0-100)**: " + ", ".join(f"{k}: {v}" for k, v in scores.items()))
    lines.append(f"- **Estimated platform overhead**: ~${cost['total']:,.0f}/month")
    for item, val in cost["items"].items():
        lines.append(f"  - {item}: ${val:,.0f}")
    return "\n".join(lines)
