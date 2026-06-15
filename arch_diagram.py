"""Animated two-region end-state architecture diagram (hand-laid SVG).

Renders the full hybrid landing-zone end state for the M&A scenario across two
AWS regions, showing every component the customer asked for — on-prem datacenter
+ branches, Direct Connect (+ DX Gateway), Site-to-Site VPN backup, per-region
Transit Gateway with inter-region peering, AWS Network Firewall (inspection),
centralized NAT egress, SD-WAN appliances, the Security and Shared-Services
accounts, and the Prod/Stage/Dev workload VPCs with public/app/db subnets — and
**animates the traffic flow** (flowing dashes + moving packets) along each path.

Returned as an HTML string for `streamlit.components.v1.html`.
"""

from __future__ import annotations

import cidr

INK = "#0B1422"
PANEL = "#101A2C"
LINE = "#2A3852"
AMBER = "#FF9900"
TEAL = "#2DD4BF"
NAVY = "#1A476F"
GREEN = "#3F8624"
RED = "#B0084D"
BLUE = "#5B8DEF"
GREY = "#6E7A8C"
MUT = "#8C9CB8"
INKT = "#0B1220"

_ENVS = [("Production", RED), ("Stage", AMBER), ("Dev", BLUE)]


def _env_cidrs(supernet):
    rows, _ = cidr.allocate_plan(supernet, 18, 3, 22, 1, ["public", "app", "db"],
                                 ["Production", "Stage", "Dev"])
    out = {}
    for r in rows:
        out.setdefault(r["VPC"], {"vpc": r["VPC CIDR"], "subnets": {}})
        out[r["VPC"]]["subnets"][r["Tier"]] = r["Subnet CIDR"]
    return out


def _rect(x, y, w, h, fill, stroke=LINE, rx=8, sw=1.2, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"{d}/>')


def _text(x, y, s, fill="#E9EEF7", size=12, weight="500", anchor="start", mono=False):
    fam = "IBM Plex Mono, monospace" if mono else "IBM Plex Sans, Helvetica, sans-serif"
    return (f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}" font-family="{fam}">{s}</text>')


def _node(x, y, w, h, label, fill, txt="#E9EEF7", sub="", rx=10):
    g = [_rect(x, y, w, h, fill, rx=rx)]
    cy = y + (h / 2 + 4 if not sub else h / 2 - 2)
    g.append(_text(x + w / 2, cy, label, txt, 11.5, "600", "middle"))
    if sub:
        g.append(_text(x + w / 2, y + h / 2 + 13, sub, txt, 9, "500", "middle", mono=True))
    return "".join(g)


def _flow(x1, y1, x2, y2, cls, mid=None):
    if mid:
        d = f"M {x1} {y1} C {mid[0]} {mid[1]}, {mid[2]} {mid[3]}, {x2} {y2}"
    else:
        d = f"M {x1} {y1} L {x2} {y2}"
    return f'<path d="{d}" class="flow {cls}" fill="none"/>', d


def _packet(path_d, color, dur, r=3.2):
    return (f'<circle r="{r}" fill="{color}" opacity="0.95">'
            f'<animateMotion dur="{dur}" repeatCount="indefinite" path="{path_d}"/></circle>')


def _region(x0, w, name, supernet, sdwan=True):
    """Draw one region panel. Returns (svg, tgw_anchor_left, tgw_anchor_right, tgw_cy)."""
    ec = _env_cidrs(supernet)
    g = [_rect(x0, 16, w, 880, PANEL, stroke=AMBER, sw=1.6, rx=16)]
    g.append(_text(x0 + 16, 42, f"AWS Region · {name}", AMBER, 13, "700"))
    g.append(_text(x0 + 16, 58, f"supernet {supernet}", MUT, 9.5, "500", mono=True))

    # Network account
    nx, ny, nw, nh = x0 + 18, 72, w - 36, 196
    g.append(_rect(nx, ny, nw, nh, "#0C1422", stroke=NAVY, rx=12))
    g.append(_text(nx + 12, ny + 18, "Network account", MUT, 10.5, "600"))
    tgw_x, tgw_y, tgw_w, tgw_h = nx + 14, ny + 34, 130, 52
    g.append(_node(tgw_x, tgw_y, tgw_w, tgw_h, "Transit Gateway", INK, AMBER if False else "#E9EEF7",
                   "route tables / segments"))
    g.append(_rect(tgw_x, tgw_y, tgw_w, tgw_h, "none", stroke=AMBER, sw=2, rx=10))
    g.append(_node(nx + 160, ny + 34, 150, 52, "AWS Network Firewall", GREEN, INKT, "inspection VPC"))
    g.append(_node(nx + 14, ny + 100, 130, 46, "NAT / egress", BLUE, "#E9EEF7", "centralized"))
    if sdwan:
        g.append(_node(nx + 160, ny + 100, 150, 46, "SD-WAN (HA)", TEAL, INKT, "TGW Connect / GRE"))
    g.append(_node(nx + 322, ny + 34, nw - 336, 52, "Security acct", NAVY, "#E9EEF7", "LogArchive·Audit"))
    g.append(_node(nx + 322, ny + 100, nw - 336, 46, "Shared Svcs", NAVY, "#E9EEF7", "CI/CD·AMIs"))

    tgw_cx = tgw_x + tgw_w / 2
    tgw_cy = tgw_y + tgw_h / 2
    fw_cx, fw_cy = nx + 160 + 75, ny + 34 + 26

    # Environment VPCs
    ey = 290
    flows = []
    for env, col in _ENVS:
        vx, vy, vw, vh = x0 + 18, ey, w - 36, 178
        g.append(_rect(vx, vy, vw, vh, "#0C1422", stroke=col, sw=1.6, rx=12))
        c = ec.get(env, {})
        g.append(_text(vx + 12, vy + 20, f"{env} OU · VPC", col, 11.5, "700"))
        g.append(_text(vx + 12, vy + 35, c.get("vpc", ""), MUT, 9.5, "500", mono=True))
        g.append(_node(vx + 12, vy + 48, 150, 50, f"VPC · {env}", AMBER, INKT, "×2 AZ"))
        subs = c.get("subnets", {})
        tier_col = {"public": GREEN, "app": NAVY, "db": GREY}
        for i, tier in enumerate(("public", "app", "db")):
            sx = vx + 185 + i * ((vw - 200) / 3)
            g.append(_node(sx, vy + 48, (vw - 210) / 3, 50, f"{tier}", tier_col[tier], "#E9EEF7",
                           subs.get(tier, "")))
        # inspection path: firewall -> VPC, and VPC -> NAT (egress)
        s1, d1 = _flow(fw_cx, fw_cy, vx + 60, vy + 48, "flow-inspect",
                       mid=(fw_cx, vy, vx, vy))
        flows.append((s1, d1, GREEN, "3.4s"))
        ey += 196

    # TGW -> Firewall (inspection), TGW -> SD-WAN
    s, d = _flow(tgw_cx + 65, tgw_cy, fw_cx - 75, fw_cy, "flow-inspect")
    flows.append((s, d, GREEN, "2.2s"))

    svg = "".join(g) + "".join(f for f, *_ in flows)
    packets = "".join(_packet(d, c, dur) for _, d, c, dur in flows[:2])
    return svg + packets, tgw_x, tgw_x + tgw_w, tgw_cx, tgw_cy


def animated_endstate(regions=("us-east-1", "us-west-2"), dx_speed="1 Gbps", sdwan=True):
    W, H = 1480, 920
    s = []
    # On-premises
    s.append(_rect(20, 300, 210, 300, PANEL, stroke=GREY, rx=14, dash="5 5"))
    s.append(_text(34, 322, "On-premises", MUT, 11.5, "700"))
    s.append(_node(38, 338, 170, 46, "Datacenter", GREY, "#E9EEF7", "primary site"))
    s.append(_node(38, 398, 170, 40, "SD-WAN edge", TEAL, INKT))
    s.append(_node(38, 446, 170, 38, "Branch 1", "#3A4555", "#E9EEF7"))
    s.append(_node(38, 492, 170, 38, "Branch 2", "#3A4555", "#E9EEF7"))
    s.append(_node(38, 538, 170, 38, "Branch 3", "#3A4555", "#E9EEF7"))

    # Connectivity (DX + VPN)
    s.append(_node(270, 330, 150, 56, "Direct Connect", NAVY, "#E9EEF7", f"DX Gateway · {dx_speed}"))
    s.append(_node(270, 470, 150, 56, "Site-to-Site VPN", "#3A2530", "#E9EEF7", "encrypted backup"))

    # Regions
    r1, r1tx0, r1tx1, r1cx, r1cy = _region(470, 470, regions[0], "10.20.0.0/16", sdwan)
    r2, r2tx0, r2tx1, r2cx, r2cy = _region(970, 470, regions[1], "10.30.0.0/16", sdwan)
    s.append(r1)
    s.append(r2)

    flows = []
    # DC -> DX, branches -> on-prem SD-WAN edge
    sg, d = _flow(208, 361, 270, 358, "flow-dx")
    flows.append((d, AMBER, "2.4s"))
    s.append(sg)
    for by in (465, 511, 557):
        sg, _d = _flow(208, by, 122, 418, "flow-sdwan")
        s.append(sg)
    sg, _d = _flow(208, 418, 270, 418, "flow-sdwan")  # SD-WAN edge -> DX area
    s.append(sg)
    # DX -> TGW(region1) and TGW(region2)
    sg, d1 = _flow(420, 358, r1tx0, r1cy, "flow-dx", mid=(450, 358, 460, r1cy)); s.append(sg); flows.append((d1, AMBER, "2.6s"))
    sg, d2 = _flow(420, 350, r2tx0, 110, "flow-dx", mid=(720, 60, 940, 60)); s.append(sg); flows.append((d2, AMBER, "3.6s"))
    # VPN -> TGW(region1) and TGW(region2)  (backup, slower dashed)
    sg, dv1 = _flow(420, 498, r1tx0, r1cy + 14, "flow-vpn", mid=(450, 520, 460, r1cy + 14)); s.append(sg)
    sg, dv2 = _flow(420, 506, r2tx0, 150, "flow-vpn", mid=(720, 900, 940, 900)); s.append(sg)
    # inter-region TGW peering
    sg, dp = _flow(r1tx1, r1cy, r2tx0, r2cy, "flow-peer", mid=(r1tx1 + 70, r1cy - 60, r2tx0 - 70, r2cy - 60))
    s.append(sg); flows.append((dp, TEAL, "2.8s"))
    s.append(_text((r1tx1 + r2tx0) / 2, r1cy - 52, "TGW inter-region peering", TEAL, 10, "600", "middle"))

    packets = "".join(_packet(d, c, dur) for d, c, dur in flows)

    legend = []
    lx, ly = 470, 905
    for i, (lab, col, cls) in enumerate([
            ("DX (primary)", AMBER, "flow-dx"), ("TGW peering", TEAL, "flow-peer"),
            ("Inspection (firewall)", GREEN, "flow-inspect"), ("Egress/NAT", BLUE, "flow-egress"),
            ("VPN (backup)", RED, "flow-vpn"), ("SD-WAN overlay", TEAL, "flow-sdwan")]):
        x = lx + i * 165
        legend.append(f'<line x1="{x}" y1="{ly}" x2="{x+24}" y2="{ly}" class="flow {cls}"/>')
        legend.append(_text(x + 30, ly + 4, lab, MUT, 10, "500"))

    style = """
    <style>
      .flow { stroke-width: 2; stroke-dasharray: 9 7; animation: dash 1s linear infinite; }
      @keyframes dash { to { stroke-dashoffset: -32; } }
      .flow-dx { stroke:#FF9900; }
      .flow-peer { stroke:#2DD4BF; animation-duration:1.6s; stroke-width:2.4; }
      .flow-inspect { stroke:#3F8624; animation-duration:1.4s; }
      .flow-egress { stroke:#5B8DEF; }
      .flow-sdwan { stroke:#2DD4BF; opacity:.8; animation-duration:1.8s; }
      .flow-vpn { stroke:#B0084D; stroke-dasharray:4 9; animation-duration:2.6s; opacity:.85; }
      text { paint-order: stroke; }
    </style>
    """
    svg = (f'<svg viewBox="0 0 {W} {H}" width="100%" preserveAspectRatio="xMidYMid meet" '
           f'xmlns="http://www.w3.org/2000/svg">'
           f'<rect width="{W}" height="{H}" fill="#080D17"/>'
           + "".join(s) + "".join(legend) + packets + '</svg>')
    return (f'<div style="background:#080D17;border-radius:14px;overflow:auto">{style}{svg}</div>')
