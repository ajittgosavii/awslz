"""Enterprise UI layer — global theming, branded chrome, and the login gate.

All visual styling lives here so app.py stays focused on product logic.
Design language: "mission control" — deep navy ink, amber signal accent,
Bricolage Grotesque display type over IBM Plex Sans/Mono.
"""

import html

import streamlit as st

# ---------------------------------------------------------------------------
# Global CSS
# ---------------------------------------------------------------------------

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,300..800&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root {
  --lz-bg: #080D17;
  --lz-panel: #101A2C;
  --lz-panel-2: #0C1422;
  --lz-line: rgba(140, 156, 184, .14);
  --lz-amber: #FF9900;
  --lz-amber-soft: #FFB84D;
  --lz-teal: #2DD4BF;
  --lz-ink: #E9EEF7;
  --lz-mut: #8C9CB8;
  --lz-display: 'Bricolage Grotesque', serif;
  --lz-sans: 'IBM Plex Sans', sans-serif;
  --lz-mono: 'IBM Plex Mono', monospace;
}

html, body, .stApp, [class*="css"] { font-family: var(--lz-sans); }

/* ---------- atmospheric backdrop: aurora + blueprint grid ---------- */
.stApp { background-color: var(--lz-bg); }
.stApp::before {
  content: ""; position: fixed; inset: -20%; z-index: 0; pointer-events: none;
  background:
    radial-gradient(38% 32% at 18% 12%, rgba(255,153,0,.13), transparent 70%),
    radial-gradient(34% 38% at 85% 8%,  rgba(45,212,191,.10), transparent 70%),
    radial-gradient(50% 44% at 55% 96%, rgba(58,110,224,.12), transparent 72%);
  animation: lz-aurora 26s ease-in-out infinite alternate;
}
.stApp::after {
  content: ""; position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background-image:
    linear-gradient(rgba(140,156,184,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(140,156,184,.05) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(75% 65% at 50% 35%, black 30%, transparent 100%);
}
@keyframes lz-aurora {
  0%   { transform: translate3d(0,0,0) scale(1); }
  50%  { transform: translate3d(2%,-2%,0) scale(1.06); }
  100% { transform: translate3d(-2%,2%,0) scale(1.02); }
}
.stApp > * { position: relative; z-index: 1; }

/* ---------- typography ---------- */
h1, h2, h3 { font-family: var(--lz-display) !important; letter-spacing: -.01em; }
h1 { font-weight: 700 !important; }
[data-testid="stMarkdownContainer"] p { color: var(--lz-ink); }
[data-testid="stCaptionContainer"], .stCaption, small { color: var(--lz-mut) !important; }

/* ---------- header bar ---------- */
header[data-testid="stHeader"] { background: transparent; }

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0A1322 0%, #070D18 100%);
  border-right: 1px solid var(--lz-line);
}
[data-testid="stSidebar"] h1 { font-size: 1.25rem !important; }
[data-testid="stSidebar"] h2 {
  font-family: var(--lz-mono) !important; font-size: .72rem !important;
  text-transform: uppercase; letter-spacing: .18em; color: var(--lz-amber) !important;
  border-top: 1px solid var(--lz-line); padding-top: 1.1rem; margin-top: .4rem;
}

/* ---------- sidebar: exactly ONE visible scrollbar ---------- */
/* Only the sidebar content container scrolls. The inner user-content is forced
   to overflow:visible so it never renders a second (nested) scrollbar. */
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
  overflow: visible !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
  overflow-y: auto !important;
  max-height: 100vh;
  scrollbar-width: thin;                                   /* Firefox */
  scrollbar-color: rgba(255,153,0,.6) rgba(140,156,184,.10);
}
/* WebKit (Chrome/Edge/Safari) — style only that single scroll container. */
section[data-testid="stSidebar"] [data-testid="stSidebarContent"]::-webkit-scrollbar {
  width: 11px;
}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"]::-webkit-scrollbar-track {
  background: rgba(140,156,184,.08); border-radius: 8px;
}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"]::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, rgba(255,153,0,.65), rgba(255,153,0,.35));
  border-radius: 8px; border: 2px solid transparent; background-clip: padding-box;
}
section[data-testid="stSidebar"] [data-testid="stSidebarContent"]::-webkit-scrollbar-thumb:hover {
  background: var(--lz-amber);
}

/* ---------- metric cards ---------- */
[data-testid="stMetric"] {
  background: linear-gradient(180deg, var(--lz-panel) 0%, var(--lz-panel-2) 100%);
  border: 1px solid var(--lz-line);
  border-radius: 14px;
  padding: 16px 18px 12px;
  position: relative;
  overflow: hidden;
  transition: transform .25s ease, border-color .25s ease, box-shadow .25s ease;
}
[data-testid="stMetric"]::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, var(--lz-amber), rgba(255,153,0,0) 75%);
}
[data-testid="stMetric"]:hover {
  transform: translateY(-2px);
  border-color: rgba(255,153,0,.35);
  box-shadow: 0 14px 34px rgba(0,0,0,.35);
}
[data-testid="stMetricLabel"] p {
  font-family: var(--lz-mono) !important; font-size: .68rem !important;
  text-transform: uppercase; letter-spacing: .14em; color: var(--lz-mut) !important;
}
[data-testid="stMetricValue"] {
  font-family: var(--lz-mono) !important; font-weight: 600;
  color: var(--lz-ink) !important;
}

/* ---------- tabs ---------- */
[data-baseweb="tab-list"] { gap: .35rem; border-bottom: 1px solid var(--lz-line); }
button[data-baseweb="tab"] {
  font-family: var(--lz-mono) !important; font-size: .78rem !important;
  text-transform: uppercase; letter-spacing: .1em;
  background: transparent !important; border-radius: 10px 10px 0 0 !important;
  padding: .55rem 1rem !important;
}
button[data-baseweb="tab"]:hover { background: rgba(255,153,0,.07) !important; }
button[data-baseweb="tab"][aria-selected="true"] { color: var(--lz-amber) !important; }
[data-baseweb="tab-highlight"] { background-color: var(--lz-amber) !important; height: 2px; }
[data-baseweb="tab-border"] { background: transparent !important; }

/* ---------- buttons ---------- */
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
  font-family: var(--lz-mono) !important; font-size: .8rem !important;
  letter-spacing: .06em;
  border: 1px solid rgba(255,153,0,.45) !important;
  background: linear-gradient(180deg, rgba(255,153,0,.14), rgba(255,153,0,.05)) !important;
  color: var(--lz-ink) !important;
  border-radius: 10px !important;
  transition: all .2s ease !important;
}
.stButton > button:hover, .stDownloadButton > button:hover,
[data-testid="stFormSubmitButton"] > button:hover {
  border-color: var(--lz-amber) !important;
  background: linear-gradient(180deg, rgba(255,153,0,.30), rgba(255,153,0,.10)) !important;
  box-shadow: 0 6px 22px rgba(255,153,0,.22) !important;
  transform: translateY(-1px);
}

/* ---------- expanders + dataframes + chat ---------- */
[data-testid="stExpander"] {
  border: 1px solid var(--lz-line) !important; border-radius: 12px !important;
  background: var(--lz-panel-2);
}
[data-testid="stDataFrame"] { border: 1px solid var(--lz-line); border-radius: 12px; }
[data-testid="stChatMessage"] {
  background: var(--lz-panel-2); border: 1px solid var(--lz-line); border-radius: 14px;
}

/* ---------- inputs ---------- */
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input {
  font-family: var(--lz-mono) !important;
}

/* ---------- hero band ---------- */
.lz-hero { padding: .4rem 0 1.0rem; animation: lz-rise .6s ease both; }
.lz-eyebrow {
  font-family: var(--lz-mono); font-size: .7rem; letter-spacing: .26em;
  text-transform: uppercase; color: var(--lz-amber); margin-bottom: .35rem;
}
.lz-title {
  font-family: var(--lz-display); font-size: 2.35rem; font-weight: 750;
  line-height: 1.05; color: var(--lz-ink); margin: 0 0 .4rem;
}
.lz-title em {
  font-style: italic; font-weight: 500;
  background: linear-gradient(92deg, var(--lz-amber), var(--lz-amber-soft));
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.lz-sub { color: var(--lz-mut); font-size: .95rem; max-width: 56rem; }
.lz-chips { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .8rem; }
.lz-chip {
  font-family: var(--lz-mono); font-size: .68rem; letter-spacing: .08em;
  padding: .28rem .65rem; border-radius: 999px;
  border: 1px solid var(--lz-line); color: var(--lz-mut);
  background: rgba(16,26,44,.6);
}
.lz-chip b { color: var(--lz-amber); font-weight: 600; }

/* ---------- login ---------- */
.lz-login-wrap { text-align: center; padding-top: 8vh; }
.lz-login-mark {
  width: 92px; height: 92px; margin: 0 auto 1.4rem; position: relative;
  animation: lz-rise .7s ease both;
}
.lz-login-mark svg { width: 100%; height: 100%; }
.lz-ring { transform-origin: 50% 50%; animation: lz-spin 14s linear infinite; }
.lz-login-eyebrow {
  font-family: var(--lz-mono); font-size: .72rem; letter-spacing: .34em;
  text-transform: uppercase; color: var(--lz-amber);
  animation: lz-rise .7s .08s ease both;
}
.lz-login-title {
  font-family: var(--lz-display); font-size: 3.1rem; font-weight: 780;
  line-height: 1.02; color: var(--lz-ink); margin: .5rem 0 .8rem;
  animation: lz-rise .7s .16s ease both;
}
.lz-login-title em {
  font-style: italic; font-weight: 480;
  background: linear-gradient(92deg, var(--lz-amber), var(--lz-amber-soft) 60%, var(--lz-teal));
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.lz-login-sub {
  color: var(--lz-mut); max-width: 34rem; margin: 0 auto;
  animation: lz-rise .7s .24s ease both;
}
.lz-login-status {
  display: flex; gap: 1.6rem; justify-content: center; margin: 1.6rem 0 .4rem;
  font-family: var(--lz-mono); font-size: .68rem; letter-spacing: .12em;
  color: var(--lz-mut); text-transform: uppercase;
  animation: lz-rise .7s .32s ease both;
}
.lz-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  margin-right: .45rem; vertical-align: 1px; }
.lz-dot.amber { background: var(--lz-amber); box-shadow: 0 0 0 0 rgba(255,153,0,.6);
  animation: lz-pulse 2.2s ease-out infinite; }
.lz-dot.teal { background: var(--lz-teal); box-shadow: 0 0 0 0 rgba(45,212,191,.6);
  animation: lz-pulse 2.2s .5s ease-out infinite; }
.lz-dot.blue { background: #5B8DEF; box-shadow: 0 0 0 0 rgba(91,141,239,.6);
  animation: lz-pulse 2.2s 1s ease-out infinite; }

[data-testid="stForm"] {
  background: rgba(15, 24, 41, .72) !important;
  backdrop-filter: blur(16px);
  border: 1px solid rgba(255,153,0,.22) !important;
  border-radius: 18px !important;
  padding: 1.6rem 1.6rem 1.2rem !important;
  box-shadow: 0 34px 90px rgba(0,0,0,.55);
  animation: lz-rise .7s .40s ease both;
}

@keyframes lz-rise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
@keyframes lz-spin { to { transform: rotate(360deg); } }
@keyframes lz-pulse {
  0% { box-shadow: 0 0 0 0 currentColor; opacity: 1; }
  70% { box-shadow: 0 0 0 9px transparent; opacity: .75; }
  100% { box-shadow: 0 0 0 0 transparent; opacity: 1; }
}
</style>
"""

_LOGO_SVG = """
<svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
  <g class="lz-ring">
    <circle cx="50" cy="50" r="46" stroke="rgba(255,153,0,.35)" stroke-width="1.5"
            stroke-dasharray="6 9" />
  </g>
  <polygon points="50,14 81,32 81,68 50,86 19,68 19,32"
           stroke="#FF9900" stroke-width="2.5" fill="rgba(255,153,0,.07)" />
  <polygon points="50,30 67,40 67,60 50,70 33,60 33,40"
           stroke="#FFB84D" stroke-width="1.6" fill="rgba(255,184,77,.10)" />
  <circle cx="50" cy="50" r="4.5" fill="#FF9900" />
  <line x1="50" y1="14" x2="50" y2="30" stroke="rgba(255,153,0,.5)" stroke-width="1.2"/>
  <line x1="81" y1="68" x2="67" y2="60" stroke="rgba(255,153,0,.5)" stroke-width="1.2"/>
  <line x1="19" y1="68" x2="33" y2="60" stroke="rgba(255,153,0,.5)" stroke-width="1.2"/>
</svg>
"""


def inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Login gate
# ---------------------------------------------------------------------------

_DEMO_PASSWORD = "awslz"


def _auth_config() -> tuple[dict | None, str, bool]:
    """Return (users_table_or_None, single_password, is_demo_fallback).

    Multi-user mode: define a [users] table in secrets (username = "password").
    Single-password mode: APP_PASSWORD. Demo fallback otherwise.
    """
    try:
        if "users" in st.secrets and len(dict(st.secrets["users"])) > 0:
            return dict(st.secrets["users"]), "", False
    except Exception:
        pass
    for name in ("APP_PASSWORD", "PASSWORD", "LOGIN_PASSWORD"):
        try:
            if name in st.secrets and st.secrets[name]:
                return None, str(st.secrets[name]), False
        except Exception:
            break
    return None, _DEMO_PASSWORD, True


def login_gate() -> bool:
    """Render the login page unless authenticated. Returns True when signed in."""
    if st.session_state.get("lz_authed"):
        return True

    users, expected, is_demo = _auth_config()

    st.markdown(
        f"""
        <div class="lz-login-wrap">
          <div class="lz-login-mark">{_LOGO_SVG}</div>
          <div class="lz-login-eyebrow">Multi-account command center</div>
          <div class="lz-login-title">AWS Landing Zone<br/><em>Studio</em></div>
          <p class="lz-login-sub">Design, simulate, and stress-test enterprise landing zones —
          scored against the six pillars of the AWS Well-Architected Framework.</p>
          <div class="lz-login-status">
            <span><span class="lz-dot amber"></span>Org &amp; Guardrails</span>
            <span><span class="lz-dot teal"></span>Network Core</span>
            <span><span class="lz-dot blue"></span>AI Advisor</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _, mid, _ = st.columns([1, 1.1, 1])
    with mid:
        with st.form("lz_login", border=False):
            user = ""
            if users is not None:
                user = st.text_input("Username", placeholder="Username…",
                                     label_visibility="collapsed")
            pw = st.text_input("Access key", type="password",
                               placeholder="Enter access key…",
                               label_visibility="collapsed")
            submitted = st.form_submit_button("⏻  Enter the Studio", use_container_width=True)
            if is_demo:
                st.caption(f"Demo mode — access key is `{_DEMO_PASSWORD}`. Set `APP_PASSWORD` "
                           "(or a `[users]` table) in Streamlit secrets to change it.")
        if submitted:
            if users is not None:
                if user in users and pw == str(users[user]):
                    st.session_state.lz_authed = True
                    st.session_state.lz_user = user
                    st.rerun()
                else:
                    st.error("Invalid username or access key.")
            elif pw == expected:
                st.session_state.lz_authed = True
                st.session_state.lz_user = "operator"
                st.rerun()
            else:
                st.error("Invalid access key.")
    return False


def logout_button():
    user = st.session_state.get("lz_user", "operator")
    st.markdown(
        f"<div style='font-family:var(--lz-mono); font-size:.68rem; color:var(--lz-mut); "
        f"letter-spacing:.1em; padding-bottom:.3rem;'>SIGNED IN AS "
        f"<span style='color:var(--lz-amber)'>{html.escape(user.upper())}</span></div>",
        unsafe_allow_html=True)
    if st.button("⎋ Sign out", use_container_width=True):
        st.session_state.lz_authed = False
        st.session_state.lz_user = None
        st.rerun()


# ---------------------------------------------------------------------------
# Branded chrome
# ---------------------------------------------------------------------------

def sidebar_brand():
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:.7rem; padding:.2rem 0 .6rem;">
          <div style="width:44px; height:44px; flex:none;">{_LOGO_SVG}</div>
          <div>
            <div style="font-family:var(--lz-display); font-weight:700; font-size:1.05rem;
                        color:var(--lz-ink); line-height:1.1;">Landing Zone Studio</div>
            <div style="font-family:var(--lz-mono); font-size:.6rem; letter-spacing:.22em;
                        text-transform:uppercase; color:var(--lz-amber);">Mission Control</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def hero(design, n_total: int, waf_overall: int, cost_total: float):
    chips = [
        f"<span class='lz-chip'><b>{html.escape(design.org_size)}</b> profile</span>",
        f"<span class='lz-chip'><b>{n_total}</b> accounts</span>",
        f"<span class='lz-chip'>WAF <b>{waf_overall}/100</b></span>",
        f"<span class='lz-chip'>overhead <b>${cost_total:,.0f}</b>/mo</span>",
        f"<span class='lz-chip'><b>{len(design.regions)}</b> region(s)</span>",
    ]
    if design.compliance:
        chips.append(f"<span class='lz-chip'>{html.escape(' · '.join(design.compliance[:3]))}</span>")
    st.markdown(
        f"""
        <div class="lz-hero">
          <div class="lz-eyebrow">Multi-account command center</div>
          <div class="lz-title">Design the landing zone.<br/><em>Then stress-test it.</em></div>
          <div class="lz-sub">Interactive simulation of account strategy, network topology,
          guardrails, and cost — reviewed by AI against the Well-Architected Framework.</div>
          <div class="lz-chips">{''.join(chips)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
