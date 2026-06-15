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

_STYLE = """
<style>
  .flow { stroke-width: 2; stroke-dasharray: 9 7; animation: dash 1s linear infinite; }
  @keyframes dash { to { stroke-dashoffset: -32; } }
  .flow-dx { stroke:#FF9900; }
  .flow-peer { stroke:#2DD4BF; animation-duration:1.6s; stroke-width:2.4; }
  .flow-inspect { stroke:#3F8624; animation-duration:1.4s; }
  .flow-egress { stroke:#5B8DEF; animation-duration:1.5s; }
  .flow-sdwan { stroke:#2DD4BF; opacity:.8; animation-duration:1.8s; }
  .flow-vpn { stroke:#B0084D; stroke-dasharray:4 9; animation-duration:2.6s; opacity:.85; }
  /* DX <-> VPN auto-failover: DX active, then drops while VPN takes over */
  .fail-dx { stroke:#FF9900; stroke-width:2.6; stroke-dasharray:9 7;
             animation: dash 1s linear infinite, faildx 9s ease-in-out infinite; }
  .fail-vpn { stroke:#B0084D; stroke-width:2.6; stroke-dasharray:4 9;
              animation: dash 2.6s linear infinite, failvpn 9s ease-in-out infinite; }
  @keyframes faildx  { 0%,58% {opacity:1;}   66%,90% {opacity:.1;}  100% {opacity:1;} }
  @keyframes failvpn { 0%,58% {opacity:.15;} 66%,90% {opacity:1;}   100% {opacity:.15;} }
  text { paint-order: stroke; }
</style>
"""


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
    g.append(_node(nx + 322, ny + 100, nw - 336, 46, "Shared Services", NAVY, "#E9EEF7",
                   "AD · ITSM · AWS SSO"))
    g.append(_text(nx + 12, ny + 180, "+ Route 53 Resolver · VPC endpoints (PrivateLink) · "
                   "GuardDuty · Security Hub · Config (delegated admin → Audit)", MUT, 8.5, "500"))
    g.append(_text(nx + 12, ny + 191, "All VPC↔VPC and on-prem traffic inspected by AWS Network "
                   "Firewall · VPCs attach to TGW (any-to-any) — no full-mesh VPC peering", MUT, 8.5, "500"))

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


def single_region_zoom(region="us-east-1", supernet="10.20.0.0/16", dx_speed="1 Gbps"):
    """Deep-dive of ONE region fully expanded: 2 AZs, every subnet (public/app/db),
    route tables, firewall endpoints per AZ, NAT per AZ, IGW, TGW attachments, and
    Shared Services (AD/ITSM/SSO). Animated ingress / egress / east-west flows, all
    routed through the AWS Network Firewall (appliance-mode inspection)."""
    W, H = 1500, 1130
    ec = _env_cidrs(supernet)
    prod = ec.get("Production", {"vpc": supernet, "subnets": {}})
    s = [f'<rect width="{W}" height="{H}" fill="#080D17"/>']
    flows = []

    # --- top band: on-prem, DX/VPN, TGW, Internet ---
    s.append(_rect(20, 40, 220, 150, PANEL, stroke=GREY, rx=12, dash="5 5"))
    s.append(_text(32, 60, "On-premises", MUT, 11, "700"))
    s.append(_node(30, 70, 200, 40, "Datacenter", GREY, "#E9EEF7", "10.100.0.0/16"))
    s.append(_node(30, 116, 200, 30, "SD-WAN edge", TEAL, INKT))
    s.append(_node(30, 150, 200, 30, "Branches ×3", "#3A4555", "#E9EEF7"))
    s.append(_node(270, 56, 150, 48, "Direct Connect", NAVY, "#E9EEF7", f"DX GW · {dx_speed}"))
    s.append(_node(270, 120, 150, 48, "Site-to-Site VPN", "#3A2530", "#E9EEF7", "backup"))
    s.append(_node(660, 60, 180, 50, "Transit Gateway", INK, "#E9EEF7", "appliance-mode"))
    s.append(_rect(660, 60, 180, 50, "none", stroke=AMBER, sw=2, rx=10))
    s.append(_node(660, 118, 180, 26, "TGW route table → inspection", NAVY, "#E9EEF7", rx=6))
    s.append(_node(1300, 50, 170, 50, "🌐 Internet", "#22303f", "#E9EEF7", "egress"))
    s.append(_text(900, 78, "VPCs attach to TGW (any-to-any, transitive) — no full-mesh VPC peering",
                   MUT, 9, "500"))
    tgw_cx, tgw_bottom = 750, 144

    # --- Inspection / Network VPC ---
    iy = 210
    s.append(_rect(300, iy, 1170, 196, "#0C1422", stroke=GREEN, sw=1.6, rx=12))
    s.append(_text(312, iy + 20, "Inspection VPC · Network account  (AWS Network Firewall — stateful)",
                   GREEN, 11.5, "700"))
    s.append(_node(1230, iy + 12, 160, 34, "Internet Gateway", BLUE, "#E9EEF7", rx=8))
    fw_anchor, nat_anchor = {}, {}
    for i, suffix in enumerate("ab"):
        ax = 330 + i * 460
        s.append(_rect(ax, iy + 40, 430, 140, "#0a1019", stroke=LINE, rx=10, dash="3 4"))
        s.append(_text(ax + 12, iy + 58, f"AZ {region}{suffix}", MUT, 10, "600"))
        s.append(_node(ax + 14, iy + 66, 200, 44, "Firewall endpoint", GREEN, INKT, "inspection"))
        s.append(_node(ax + 14, iy + 116, 200, 40, "NAT gateway", BLUE, "#E9EEF7", "egress"))
        s.append(_node(ax + 226, iy + 66, 190, 44, "TGW attach subnet", NAVY, "#E9EEF7", "ENI"))
        s.append(_node(ax + 226, iy + 116, 190, 40, "Firewall subnet", "#1d3b24", "#E9EEF7", rx=8))
        fw_anchor[suffix] = (ax + 114, iy + 88)
        nat_anchor[suffix] = (ax + 114, iy + 136)

    # --- Production VPC fully expanded ---
    py = 430
    s.append(_rect(300, py, 1170, 340, "#0C1422", stroke=RED, sw=1.6, rx=12))
    s.append(_text(312, py + 20, f"Production VPC · {prod['vpc']} · multi-AZ", RED, 11.5, "700"))
    tiers = [("public", GREEN, "RT: 0.0.0.0/0 → IGW"),
             ("app", NAVY, "RT: 0.0.0.0/0 → TGW (→ firewall)"),
             ("db", GREY, "RT: local only")]
    app_anchor = {}
    for i, suffix in enumerate("ab"):
        ax = 330 + i * 460
        s.append(_rect(ax, py + 40, 430, 286, "#0a1019", stroke=LINE, rx=10, dash="3 4"))
        s.append(_text(ax + 12, py + 58, f"AZ {region}{suffix}", MUT, 10, "600"))
        subs = prod.get("subnets", {})
        for j, (tier, col, rt) in enumerate(tiers):
            sy = py + 66 + j * 58
            s.append(_node(ax + 14, sy, 190, 48, f"{tier} subnet", col, "#E9EEF7" if tier != "db" else "#E9EEF7",
                           subs.get(tier, "")))
            s.append(_node(ax + 214, sy + 4, 200, 40, rt, "#16202e", MUT, rx=7))
            if tier == "app":
                app_anchor[suffix] = (ax + 109, sy + 24)
        s.append(_node(ax + 14, py + 240, 400, 34, "TGW attach subnet (×AZ)", NAVY, "#E9EEF7", rx=8))

    # --- Shared Services VPC ---
    sy = 794
    s.append(_rect(300, sy, 1170, 120, "#0C1422", stroke=TEAL, sw=1.4, rx=12))
    s.append(_text(312, sy + 20, "Shared Services VPC", TEAL, 11.5, "700"))
    for i, (lab, sub) in enumerate([
            ("Active Directory", "AWS Managed AD"), ("ITSM", "ServiceNow connector"),
            ("AWS SSO", "IAM Identity Center"), ("Route 53 Resolver", "hybrid DNS"),
            ("VPC Endpoints", "PrivateLink"), ("CI/CD · AMIs", "golden images")]):
        x = 318 + i * 192
        s.append(_node(x, sy + 32, 180, 56, lab, NAVY, "#E9EEF7", sub))

    # --- Security account (delegated admin, org-wide) ---
    qy = 930
    s.append(_rect(300, qy, 1170, 96, "#0C1422", stroke=GREEN, sw=1.4, rx=12))
    s.append(_text(312, qy + 20, "Security account · delegated admin (org-wide)", GREEN, 11.5, "700"))
    for i, (lab, sub) in enumerate([
            ("Amazon GuardDuty", "threat detection"), ("AWS Security Hub", "posture / CSPM"),
            ("AWS Config", "compliance rules"), ("CloudTrail", "org trail"),
            ("Log Archive", "S3 Object Lock"), ("Access Analyzer", "external access")]):
        x = 318 + i * 192
        col = GREEN if i < 3 else NAVY
        s.append(_node(x, qy + 30, 180, 52, lab, col, INKT if i < 3 else "#E9EEF7", sub))

    # --- animated traffic flows (all via the firewall) ---
    # 0) DX primary <-> VPN backup auto-failover
    sg, _ = _flow(420, 80, 660, 80, "flow-dx fail-dx"); s.append(sg)
    sg, _ = _flow(420, 144, 660, 102, "flow-vpn fail-vpn"); s.append(sg)
    s.append(_text(545, 64, "DX ⇄ VPN failover", AMBER, 9, "600", "middle"))
    # 1) ingress: DX -> TGW -> firewall endpoint (AZ-a) -> app subnet (AZ-a)
    d = f"M 660 80 L 745 80"  # reference DX path for an ingress packet
    flows.append((d, AMBER, "2.2s"))
    sg, d = _flow(tgw_cx, tgw_bottom, *fw_anchor["a"], "flow-inspect", mid=(tgw_cx, 200, fw_anchor["a"][0], 230)); s.append(sg); flows.append((d, GREEN, "2.0s"))
    sg, d = _flow(*fw_anchor["a"], *app_anchor["a"], "flow-inspect", mid=(fw_anchor["a"][0], 420, app_anchor["a"][0], 470)); s.append(sg); flows.append((d, GREEN, "2.2s"))
    # 2) egress: app (AZ-b) -> TGW -> firewall (AZ-b) -> NAT (AZ-b) -> IGW -> Internet
    sg, d = _flow(*app_anchor["b"], tgw_cx + 20, tgw_bottom, "flow-egress", mid=(app_anchor["b"][0], 430, tgw_cx + 20, 220)); s.append(sg); flows.append((d, BLUE, "2.8s"))
    sg, d = _flow(tgw_cx + 20, tgw_bottom, *fw_anchor["b"], "flow-inspect", mid=(tgw_cx + 20, 200, fw_anchor["b"][0], 230)); s.append(sg)
    sg, d = _flow(*fw_anchor["b"], *nat_anchor["b"], "flow-egress"); s.append(sg)
    sg, d = _flow(*nat_anchor["b"], 1310, iy + 29, "flow-egress", mid=(1250, iy + 136, 1310, iy + 60)); s.append(sg)
    sg, d = _flow(1390, iy + 29, 1385, 100, "flow-egress"); s.append(sg); flows.append((d, BLUE, "2.6s"))
    # 3) east-west AZ-a app <-> AZ-b app, inspected at TGW/firewall
    sg, d = _flow(*app_anchor["a"], *app_anchor["b"], "flow-peer", mid=(app_anchor["a"][0], py + 300, app_anchor["b"][0], py + 300)); s.append(sg); flows.append((d, TEAL, "3.0s"))

    # step labels
    s.append(_text(470, 200, "① ingress (DC → TGW → firewall → app)", GREEN, 9.5, "600"))
    s.append(_text(980, 200, "② egress (app → TGW → firewall → NAT → IGW)", BLUE, 9.5, "600"))
    s.append(_text(560, py + 296, "③ east-west VPC↔VPC inspected via TGW + firewall", TEAL, 9.5, "600"))

    # legend
    legend = []
    for i, (lab, col, cls) in enumerate([
            ("DX (primary)", AMBER, "flow-dx"), ("Inspection (firewall)", GREEN, "flow-inspect"),
            ("Egress → NAT → IGW", BLUE, "flow-egress"), ("East-west (inspected)", TEAL, "flow-peer")]):
        x = 320 + i * 270
        legend.append(f'<line x1="{x}" y1="{H-20}" x2="{x+24}" y2="{H-20}" class="flow {cls}"/>')
        legend.append(_text(x + 30, H - 16, lab, MUT, 10, "500"))

    packets = "".join(_packet(d, c, dur) for d, c, dur in flows)
    svg = (f'<svg viewBox="0 0 {W} {H}" width="100%" preserveAspectRatio="xMidYMid meet" '
           f'xmlns="http://www.w3.org/2000/svg">' + "".join(s) + "".join(legend) + packets + '</svg>')
    return f'<div style="background:#080D17;border-radius:14px;overflow:auto">{_STYLE}{svg}</div>'


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
    # Branches aggregate into the SD-WAN edge: bow up the right margin so each
    # line lands on the SD-WAN edge's right side instead of piercing its centre.
    for by, bow in ((465, 236), (511, 250), (557, 264)):
        sg, _d = _flow(208, by, 208, 422, "flow-sdwan", mid=(bow, by, bow, 432))
        s.append(sg)
    # SD-WAN edge -> Site-to-Site VPN (backup overlay). Terminates on the VPN
    # node (which would otherwise be orphaned) rather than dangling in space.
    sg, _d = _flow(208, 430, 270, 498, "flow-sdwan")
    s.append(sg)
    # DX + VPN terminate at the PRIMARY region's TGW; the second region is reached
    # via inter-region TGW peering (no second DX line).
    sg, d1 = _flow(420, 358, r1tx0, r1cy, "flow-dx fail-dx", mid=(450, 358, 460, r1cy)); s.append(sg)
    sg, dv1 = _flow(420, 498, r1tx0, r1cy + 14, "flow-vpn fail-vpn", mid=(450, 520, 460, r1cy + 14)); s.append(sg)
    s.append(_text(345, 300, "DX primary ⇄ VPN auto-failover", AMBER, 9.5, "600", "middle"))
    # inter-region TGW peering (carries traffic to region 2)
    sg, dp = _flow(r1tx1, r1cy, r2tx0, r2cy, "flow-peer", mid=(r1tx1 + 70, r1cy - 60, r2tx0 - 70, r2cy - 60))
    s.append(sg); flows.append((dp, TEAL, "2.8s"))
    s.append(_text((r1tx1 + r2tx0) / 2, r1cy - 52, "TGW inter-region peering", TEAL, 10, "600", "middle"))
    s.append(_text((r1tx1 + r2tx0) / 2, r1cy - 40, "transitive · not VPC peering (O(n²))", MUT, 8.5, "500", "middle"))

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

    svg = (f'<svg viewBox="0 0 {W} {H}" width="100%" preserveAspectRatio="xMidYMid meet" '
           f'xmlns="http://www.w3.org/2000/svg">'
           f'<rect width="{W}" height="{H}" fill="#080D17"/>'
           + "".join(s) + "".join(legend) + packets + '</svg>')
    return (f'<div style="background:#080D17;border-radius:14px;overflow:auto">{_STYLE}{svg}</div>')
