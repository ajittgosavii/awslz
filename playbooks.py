"""Scenario Playbooks — enterprise lifecycle simulations on top of a design.

Each playbook takes the current design as a "golden blueprint" and simulates a
real-world organizational event (M&A replication, account absorption,
divestiture, geo/compliance expansion, scale-out), computing concrete numbers
(accounts, cost, vending time, score deltas) and a grounded, AWS-aligned runbook
with risks and references.

Grounded in AWS guidance: accounts detach from one organization and re-attach to
another (place them in a permissive "landing" OU first, then tighten SCPs);
migrating a management account requires emptying and deleting the source org;
GuardDuty/Security Hub delegated administration is per-organization; AFT (Account
Factory for Terraform) is the GitOps engine for scaled vending and enrolling
existing accounts.
"""

from __future__ import annotations

import copy
import math

import plotly.graph_objects as go
import streamlit as st

import arch_diagram
import cidr
import iac
import pricing
import waf
from lz_core import (
    COMPLIANCE_FRAMEWORKS, REGIONS,
    estimate_monthly_cost, recommend_guardrails, score_design, total_accounts,
    workload_account_count, core_account_count,
)

try:
    from streamlit_agraph import agraph, Node, Edge, Config
    _HAS_AGRAPH = True
except Exception:  # fall back to a static Graphviz diagram
    _HAS_AGRAPH = False

# ---------------------------------------------------------------------------
# Assumptions (transparent, like the cost model)
# ---------------------------------------------------------------------------
MIN_PER_ACCOUNT = 30            # Control Tower Account Factory provisioning, sequential
AFT_CONCURRENCY = 5            # AFT pipeline concurrent account builds (assumption)
BASELINE_HOURS_PER_ORG = 24    # stand up a fresh landing zone (mgmt+OUs+SCPs+core) per org
REMEDIATION_HOURS_PER_ACCT = 2  # assess + baseline a migrated/acquired account

REFS = {
    "ma_network": ("Migrating accounts between AWS Organizations (networking)",
                   "https://aws.amazon.com/blogs/networking-and-content-delivery/migrating-accounts-between-aws-organizations-from-a-network-perspective/"),
    "ma_waf": ("M&A readiness with the Well-Architected Framework",
               "https://aws.amazon.com/blogs/architecture/mergers-and-acquisitions-readiness-with-the-well-architected-framework/"),
    "migrate_accounts": ("Migrating AWS accounts between organizations (re:Post)",
                         "https://repost.aws/articles/ARf43Hri2LQmGARxerfK9B6Q/migrating-aws-accounts-between-organizations"),
    "lz_to_ct": ("Migrate AWS Landing Zone solution to AWS Control Tower",
                 "https://aws.amazon.com/blogs/mt/migrate-aws-landing-zone-solution-to-aws-control-tower/"),
    "aft": ("AWS Control Tower Account Factory for Terraform (AFT)",
            "https://docs.aws.amazon.com/controltower/latest/userguide/aft-overview.html"),
    "enroll": ("Enroll existing accounts in AWS Control Tower",
               "https://docs.aws.amazon.com/controltower/latest/userguide/enroll-account.html"),
    "mgn": ("AWS Application Migration Service (MGN) user guide",
            "https://docs.aws.amazon.com/mgn/latest/ug/what-is-application-migration-service.html"),
    "mgn_account": ("MGN — migrate between AWS accounts / regions",
                    "https://docs.aws.amazon.com/mgn/latest/ug/migrating-to-different-account.html"),
    "dx": ("AWS Direct Connect resiliency & hybrid connectivity",
           "https://docs.aws.amazon.com/directconnect/latest/UserGuide/Welcome.html"),
    "cloudwan": ("AWS Cloud WAN — global network with SD-WAN integration",
                 "https://docs.aws.amazon.com/network-manager/latest/cloudwan/what-is-cloudwan.html"),
}

# Contrast-aware label font for the draggable topology nodes (dark canvas).
def _net_font(fill: str) -> dict:
    r, g, b = int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return {"color": "#0B1220" if lum > 150 else "#F4F7FB", "size": 13, "face": "Helvetica"}


# ---------------------------------------------------------------------------
# Shared UI helpers
# ---------------------------------------------------------------------------

def _vending_time(n_accounts: int) -> tuple[float, float]:
    """(sequential_hours, aft_pipelined_hours) to vend n accounts."""
    seq = n_accounts * MIN_PER_ACCOUNT / 60
    aft = math.ceil(max(1, n_accounts) / AFT_CONCURRENCY) * MIN_PER_ACCOUNT / 60
    return round(seq, 1), round(aft, 1)


def _runbook(phases: list[tuple[str, list[str]]]):
    st.markdown("#### Runbook")
    for label, steps in phases:
        st.markdown(f"**{label}**")
        for s in steps:
            st.markdown(f"- {s}")


def _risks(items: list[str]):
    st.markdown("#### Key risks & considerations")
    for it in items:
        st.markdown(f"- ⚠️ {it}")


def _refs(keys: list[str]):
    st.markdown("#### AWS references")
    for k in keys:
        label, url = REFS[k]
        st.markdown(f"- [{label}]({url})")


def _guardrail_names(design) -> list[str]:
    return [g[0] for g in recommend_guardrails(design)]


# ---------------------------------------------------------------------------
# MGN per-wave cutover runbook (Markdown checklist + CSV wave plan)
# ---------------------------------------------------------------------------
_TIERS = ["db", "app", "web"]          # cutover order: data tier first, web last
_TIER_ORDER = {t: i for i, t in enumerate(_TIERS)}
_TIER_SUBNET = {"db": "data-subnet", "app": "app-subnet", "web": "web-subnet"}


def _plan_waves(vms: int, wave_size: int, n_apps: int):
    """Distribute `vms` servers across `n_apps` applications (each with db/app/web
    tiers), then pack whole applications into waves of ~`wave_size` so an app's
    dependent tiers always cut over together. Returns a list of waves; each wave
    is a list of (app_label, [(server_id, tier), ...])."""
    n_apps = max(1, min(n_apps, vms))
    members = [0] * n_apps
    for i in range(vms):
        members[i % n_apps] += 1

    apps, sid = [], 1
    for ai in range(n_apps):
        recs = []
        for j in range(members[ai]):
            tier = "db" if j == 0 else ("app" if j % 2 == 1 else "web")
            recs.append((f"srv-{sid:03d}", tier))
            sid += 1
        recs.sort(key=lambda r: _TIER_ORDER[r[1]])  # db -> app -> web
        apps.append((f"App-{chr(65 + ai % 26)}{'' if ai < 26 else ai // 26}", recs))

    waves, cur, cur_n = [], [], 0
    for label, recs in apps:
        if cur and cur_n + len(recs) > wave_size:
            waves.append(cur); cur, cur_n = [], 0
        cur.append((label, recs)); cur_n += len(recs)
    if cur:
        waves.append(cur)
    return waves


def _mgn_runbook_md(vms: int, wave_size: int, n_apps: int,
                    region: str = "us-east-1", target: str = "company-a") -> str:
    waves = _plan_waves(vms, wave_size, n_apps)
    L = [f"# AWS MGN Cutover Runbook — {vms} servers in {len(waves)} wave(s)",
         f"_Target: {target} · region {region} · servers grouped into {n_apps} application(s) "
         "(dependent tiers cut over together: **db → app → web**)._",
         "",
         "## Global prerequisites (once, before any wave)",
         "- [ ] AWS MGN initialized in the target account/region; replication settings template configured "
         "(staging subnet, gp3, security group)",
         "- [ ] Network connectivity source → staging validated (Direct Connect / SD-WAN / VPN)",
         "- [ ] Cross-account IAM roles + MGN service-linked role in place",
         "- [ ] Launch templates per tier (subnet / security group / instance type mapping)",
         "- [ ] MGN agent installed on all source servers; **initial sync = 100%** and lag healthy",
         "- [ ] Maintenance windows agreed; stakeholder comms + rollback owners assigned",
         ""]
    for wi, wave in enumerate(waves, 1):
        count = sum(len(recs) for _, recs in wave)
        apps_in = ", ".join(f"{label} ({len(recs)})" for label, recs in wave)
        L += [f"## Wave {wi} — {apps_in}  ·  {count} server(s)",
              "**Cutover window:** ________  **Lead:** ________  **Rollback owner:** ________",
              "",
              "**T-1 day — readiness**",
              "- [ ] Replication lag < threshold for every server in the wave",
              "- [ ] Source backup/snapshot taken; change freeze in effect",
              "- [ ] App owners notified; validation scripts ready",
              "",
              "**T-0 — cutover (order: db → app → web)**",
              "- [ ] Quiesce application; stop writes on the **db** tier first",
              "- [ ] Trigger **final sync**; mark wave servers *ready for cutover* in MGN",
              "- [ ] Launch cutover instances tier-by-tier (db, then app, then web)",
              "- [ ] Repoint DNS / load-balancer targets to the new instances",
              "- [ ] Smoke test: health checks, key user journeys, integrations",
              "",
              "**Validate**",
              "- [ ] App owner sign-off  - [ ] Monitoring/alarms green  - [ ] Performance acceptable",
              "",
              "**Rollback (only before *Finalize*)**",
              "- [ ] Revert DNS/LB; restart source; keep replication running; investigate",
              "",
              "| Server | Tier | Target subnet | Cutover order | Status |",
              "|--------|------|---------------|---------------|--------|"]
        order = 1
        for label, recs in wave:
            for sid, tier in recs:
                L.append(f"| {sid} | {tier} | {_TIER_SUBNET[tier]} ({label}) | {order} | ☐ |")
                order += 1
        L.append("")
    L += ["## Finalize (after all waves validated)",
          "- [ ] Mark every server **migration complete** in MGN",
          "- [ ] Terminate replication servers + staging volumes (stops replication cost)",
          "- [ ] Decommission the source account/environment once empty",
          "- [ ] Enroll migrated workloads under guardrails (centralized logging, GuardDuty / "
          "Security Hub, AWS Backup) and federate identity",
          ""]
    return "\n".join(L)


def _mgn_runbook_csv(vms: int, wave_size: int, n_apps: int) -> str:
    waves = _plan_waves(vms, wave_size, n_apps)
    rows = ["wave,application,server,tier,target_subnet,cutover_order"]
    for wi, wave in enumerate(waves, 1):
        order = 1
        for label, recs in wave:
            for sid, tier in recs:
                rows.append(f"{wi},{label},{sid},{tier},{_TIER_SUBNET[tier]},{order}")
                order += 1
    return "\n".join(rows) + "\n"


def _runbook_downloads(vms: int, wave_size: int, key: str,
                       region: str = "us-east-1", target: str = "company-a"):
    """Render the app-grouping control + Markdown/CSV download buttons."""
    vms = int(vms)
    default_apps = max(1, math.ceil(vms / 6))
    n_apps = st.slider("Group into applications (keeps each app's tiers in one wave)",
                       1, max(1, vms), min(default_apps, vms), key=f"napps_{key}")
    waves = _plan_waves(vms, wave_size, n_apps)
    st.caption(f"Plan: **{len(waves)} wave(s)**, {n_apps} application(s), {vms} servers "
               "(db → app → web within each wave).")
    md = _mgn_runbook_md(vms, wave_size, n_apps, region, target)
    csv = _mgn_runbook_csv(vms, wave_size, n_apps)
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Per-wave cutover runbook (Markdown)", md,
                       file_name="mgn-cutover-runbook.md", mime="text/markdown",
                       use_container_width=True, key=f"md_{key}")
    d2.download_button("⬇️ Wave plan (CSV)", csv, file_name="mgn-wave-plan.csv",
                       mime="text/csv", use_container_width=True, key=f"csv_{key}")


def _merge_zip(z, sub_bytes: bytes, prefix: str):
    """Copy every entry of a sub-bundle zip into `z` under `prefix`."""
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(sub_bytes)) as sub:
        for name in sub.namelist():
            z.writestr(prefix + name, sub.read(name))


def _plan_csv(rows: list) -> str:
    cols = ["VPC", "VPC CIDR", "Subnet", "Subnet CIDR", "Tier", "AZ", "Addresses", "Usable (AWS)"]
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")) for c in cols))
    return "\n".join(lines) + "\n"


def _networking_setup_guide(hub, dx_speed, dx_redundant, env_cidrs=None, b_vms=68,
                            dc_cidr="10.100.0.0/16", b_cidr="10.20.5.0/24"):
    """Detailed, step-by-step networking setup guide (Markdown) for the M&A scenario:
    connect a new AWS account to the datacenter via Direct Connect over SD-WAN, and
    provide the replication path for the AWS MGN migration."""
    ec = env_cidrs or {}
    is_cw = hub == "AWS Cloud WAN"
    seg_word = "Cloud WAN segments" if is_cw else "Transit Gateway route tables"
    env_lines = []
    for ekey, elabel, _c, _n in _env_split(3):
        c = ec.get(ekey, {})
        vcidr = c.get("vpc_cidr", "(allocate)")
        subs = c.get("subnets", {})
        sub_str = ", ".join(f"{t} {subs.get(t, '—')}" for t in ("public", "app", "db"))
        env_lines.append(f"- **{elabel}** — VPC `{vcidr}` · {sub_str}")
    envs_md = "\n".join(env_lines)
    redundancy = ("two connections at separate DX locations (or a LAG)" if dx_redundant
                  else "a single connection (add a second for production resilience)")

    return f"""# Networking setup guide — M&A integration

Connect Company A's **new AWS account** to its **datacenter** over **Direct Connect
({dx_speed})** with an **SD-WAN** overlay into a **{hub}** hub, and provide the
replication path to migrate **{int(b_vms)} VMs from Company B with AWS MGN**.

> Reviewable runbook with concrete AWS steps. Replace ASNs, VLANs, IP/CIDRs, and
> SD-WAN AMIs with your values. Order matters — follow the phases top to bottom.

## 0. Prerequisites
- A dedicated **Network account** under Company A's organization (Infrastructure OU).
- Approved **address plan** (no overlaps) — datacenter `{dc_cidr}`, Company B `{b_cidr}`,
  and the new environment VPCs (below). Validate with the CIDR tab's overlap check.
- An on-prem/SD-WAN edge that can run **BGP**, and a public IP for the VPN backup.
- IAM access to the Network account; quotas for DX VIFs, TGW attachments, VPN.

## 1. Order Direct Connect
1. In the Network account, request **Direct Connect** ({dx_speed}) at your DX location:
   provision {redundancy}.
2. Complete the **cross-connect** with the colo/partner; wait for the link to come **up**.
3. Note the **connection ID** (`dxcon-…`) — it feeds the Terraform `dx_connection_id`.

## 2. Build the AWS hub — {hub}
{"1. Create a **Cloud WAN global network** and **core network**; author a core-network **policy** with one **segment per environment** (prod / staging / dev+test) and an attachment policy." if is_cw else "1. Create a **Transit Gateway** (`amazon_side_asn`, default route table association/propagation enabled)."}
2. This hub provides **any-to-any** connectivity for the VPC spokes, the datacenter
   (via DX), and the SD-WAN overlay.

## 3. Direct Connect Gateway + transit VIF
1. Create a **Direct Connect Gateway** (`aws_dx_gateway`) with your `amazon_side_asn`.
2. **Associate** it with the {hub} (`aws_dx_gateway_association`), allowing the on-prem
   prefixes (`{dc_cidr}`).
3. Create a **transit virtual interface** on the DX connection (VLAN + your customer ASN).
   {"Add a second VIF on the redundant connection." if dx_redundant else "Add a second VIF/connection later for HA."}

## 4. SD-WAN overlay
1. Deploy an **HA pair of SD-WAN appliances** (EC2) in a transit VPC subnet; set
   `source_dest_check = false`; install the vendor license/AMI.
2. Integrate with the hub using **Transit Gateway Connect** (`aws_ec2_transit_gateway_connect`
   + `connect_peer`, GRE + BGP) so branch sites reach the hub through the overlay, with
   **Direct Connect as the underlay**.

## 5. Site-to-Site VPN (backup path)
1. Create a **Customer Gateway** (on-prem/SD-WAN public IP, customer ASN) and a
   **VPN connection** to the {hub}.
2. Tune BGP so the VPN is the **lower-preference** path; it activates only if DX drops.

## 6. Environment segmentation (Prod / Staging / Dev+Test)
Keep environments isolated using **{seg_word}** so prod can't talk to dev by default:
{envs_md}
- Give each environment its own {("segment" if is_cw else "route table")}; share only what's
  required (e.g. Shared Services, egress). Production gets the strictest SCPs/NACLs.

## 7. Routing & BGP
- Advertise on-prem `{dc_cidr}` over the transit VIF; advertise the AWS env CIDRs back.
- Prefer **DX over VPN** via BGP local-preference / AS-path; verify failover.
- Avoid overlapping routes — the env CIDRs above are carved from one supernet.

## 8. Centralized egress & inspection
- Route environment egress through a **centralized egress VPC** (NAT) in the Network
  account; optionally insert a **firewall / inspection VPC** for east-west and DC traffic.

## 9. Connectivity for the AWS MGN migration
1. Company B's source servers must reach the **MGN staging area** in the target account.
   Use the DX/SD-WAN path (or a temporary VPN) — open the MGN replication ports.
2. Place the MGN **staging subnet** in the appropriate environment VPC (usually a
   non-prod/landing subnet) and map source → target subnets/SGs in the launch template.
3. After cutover, the migrated workloads sit behind the same hub and segmentation.

## 10. Validation & monitoring
- **Reachability Analyzer** from a spoke ENI to the datacenter and between environments.
- Confirm **BGP sessions** (DX + VPN), route propagation, and DX **failover** to VPN.
- Enable **VPC Flow Logs**, **Network Manager** monitoring, and CloudWatch alarms on the
  DX connection and TGW/Cloud WAN attachments.

## Appendix
- Generate the matching Terraform from the playbook: **connectivity.tf** (this hub + DX +
  SD-WAN + VPN) and **vpcs.tf / spoke_attachments.tf** (the env VPCs above).
- Re-run the **CIDR overlap check** whenever you add a datacenter, branch, or acquired range.
"""


def _acquisition_package(design, hub: str, dx_speed: str, dx_redundant: bool, b_vms: int,
                         plan_rows=None, env_cidrs=None) -> bytes:
    """Bundle every integration artifact into one downloadable .zip:
    CIDR plan + VPC IaC + connectivity.tf + networking guide + org IaC + MGN runbook."""
    import io
    import zipfile

    region = design.regions[0] if getattr(design, "regions", None) else "us-east-1"
    if not plan_rows:
        env_cidrs, plan_rows = _env_cidr_plan()
    n_apps = max(1, math.ceil(int(b_vms) / 6))
    rb_md = _mgn_runbook_md(int(b_vms), 10, n_apps, region, "company-a")
    rb_csv = _mgn_runbook_csv(int(b_vms), 10, n_apps)

    master = f"""# Acquisition integration package

Generated by AWS Landing Zone Studio — a complete, reviewable starting package to
connect a new AWS account to a datacenter (Direct Connect over SD-WAN), vend it
under the existing landing zone, and migrate {int(b_vms)} VMs with AWS MGN.

## Contents
```
network/      # NETWORKING-SETUP.md guide + IP plan + VPC/subnet IaC + connectivity (DX + SD-WAN + {hub})
org/          # vend the integration account + replicate the landing-zone blueprint
migration/    # per-wave AWS MGN cutover runbook for the {int(b_vms)} VMs
```

## Recommended order
1. **network/** — review `ip-plan.csv` (overlap-free CIDRs). Deploy `connectivity.tf`
   (creates the {hub} hub + Direct Connect{' (redundant)' if dx_redundant else ''} + SD-WAN + VPN backup),
   then `vpcs.tf`; pass `local.spoke_vpc_attachments` (in `spoke_attachments.tf`)
   into the connectivity module's `var.spoke_vpc_attachments`.
2. **org/** — from each target org's management account, `terraform apply` the root
   under `orgs/` (vends the new account, OUs, SCPs, core accounts); use `aft/` for
   scaled workload vending.
3. **migration/** — execute `mgn-cutover-runbook.md` wave by wave (db → app → web);
   track with `mgn-wave-plan.csv`.

All artifacts are consistent (same CIDRs, same hub). Review and adapt ASNs, CIDRs,
emails, SD-WAN AMIs, and IDs before applying — this is a starting point, not a
turnkey deployment.
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", master)
        z.writestr("network/NETWORKING-SETUP.md",
                   _networking_setup_guide(hub, dx_speed, dx_redundant, env_cidrs, b_vms))
        z.writestr("network/ip-plan.csv", _plan_csv(plan_rows))
        _merge_zip(z, iac.vpc_network_bundle(plan_rows, region), "network/")
        z.writestr("network/connectivity.tf",
                   iac.connectivity_terraform(hub, dx_speed, dx_redundant, True, 3))
        _merge_zip(z, iac.replication_bundle(design, ["company-a-integration"]), "org/")
        z.writestr("migration/mgn-cutover-runbook.md", rb_md)
        z.writestr("migration/mgn-wave-plan.csv", rb_csv)
    return buf.getvalue()


# ===========================================================================
# 1. M&A — replicate blueprint to N organizations
# ===========================================================================

def _ma_replicate(design, derived):
    st.markdown("Treat your **current design as a golden blueprint** and replicate it across "
                "several separate AWS Organizations — e.g. post-merger entities that must each keep "
                "their own org, or standardized subsidiaries. Each target org is provisioned to the "
                "same OU structure, guardrails, and baselines.")

    c1, c2 = st.columns(2)
    k = c1.slider("Target organizations to replicate to", 1, 12, 3)
    same = c2.toggle("Identical to the blueprint", value=True,
                     help="Off lets you scale workloads per target org.")
    wl_per = workload_account_count(design)
    if not same:
        factor = st.slider("Workload accounts per target (× blueprint)", 0.25, 3.0, 1.0, 0.25)
    else:
        factor = 1.0

    n_blueprint = total_accounts(design)
    core = core_account_count(design)
    per_target_accounts = core + round(workload_account_count(design) * factor) + (1 if design.org_size != "Startup" else 0)
    new_accounts = per_target_accounts * k
    blueprint_cost = derived["cost"]["total"]
    per_target_cost = estimate_monthly_cost(_scaled(design, factor))["total"]
    total_cost = per_target_cost * k
    seq_h, aft_h = _vending_time(new_accounts)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Target orgs", k)
    m2.metric("New accounts (total)", new_accounts, help=f"{per_target_accounts}/org × {k}")
    m3.metric("Added platform cost", f"${total_cost:,.0f}/mo")
    m4.metric("Vend time (AFT)", f"~{aft_h:,.0f} h", help=f"Sequential ≈ {seq_h:,.0f} h")

    # accounts & cost across orgs
    fig = go.Figure()
    fig.add_trace(go.Bar(x=[f"Org {i+1}" for i in range(k)], y=[per_target_accounts] * k,
                         name="Accounts", marker_color="#FF9900"))
    fig.add_trace(go.Scatter(x=[f"Org {i+1}" for i in range(k)], y=[per_target_cost] * k,
                             name="Cost ($/mo)", yaxis="y2", line=dict(color="#2DD4BF", width=3)))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10),
                      yaxis=dict(title="Accounts"),
                      yaxis2=dict(title="USD/mo", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, use_container_width=True)

    st.caption("Guardrails replicated to every org: " + ", ".join(_guardrail_names(design)[:8]) + " …")

    _runbook([
        ("Day 0 — Blueprint as code", [
            "Capture the blueprint as **LZA config** or **AFT** templates (OU tree, SCPs, baselines, "
            "core accounts) — export from the IaC tab as the starting point.",
            "Create a fresh **management account** per target org and enable AWS Organizations "
            "with **ALL features**; enable trusted access for CloudTrail/Config/GuardDuty/Security Hub.",
            "Reserve unique **root email addresses** for every account across every org (a common blocker)."]),
        ("Day 1 — Foundation per org", [
            "Set up the landing zone (Control Tower or LZA) and the Security OU "
            "(**Log Archive + Audit**) first.",
            "Apply the SCP guardrails and OU structure identically across orgs (deploy from the same "
            "code, parameterized per org).",
            f"Vend the **{per_target_accounts} accounts** per org via Account Factory / **AFT** "
            "(GitOps, ~5 concurrent builds)."]),
        ("Day 2 — Operate", [
            "Delegate **GuardDuty / Security Hub / Config admin per org** (delegated admin does not "
            "span organizations — each org needs its own).",
            "Establish cross-org connectivity where needed (TGW **peering** / Cloud WAN / PrivateLink — "
            "RAM sharing does not cross organization boundaries).",
            "Set budgets + budget actions, enable drift detection, and snapshot each org as a "
            "**target** in the Drift tab."]),
    ])
    _risks([
        "Root email uniqueness across *all* accounts in *all* orgs — plan an address scheme up front.",
        "Security tooling (GuardDuty/Security Hub) delegated admin is **per-organization** — you get N "
        "separate security planes, not one; aggregate findings centrally (e.g. Security Lake).",
        "Identity: each org has its own IAM Identity Center instance — federate to a central external "
        "IdP (Okta/Entra) for one sign-on experience.",
        "Cross-org networking can't use AWS RAM; use TGW peering, Cloud WAN, or PrivateLink and watch "
        "for overlapping CIDRs.",
        "Service quotas (accounts per org default soft-limit) — request increases before bulk vending.",
    ])
    _refs(["ma_waf", "aft", "migrate_accounts"])

    st.divider()
    st.markdown("#### Generate the replication IaC")
    st.caption("Produces a reviewable Terraform scaffold: a shared blueprint **module**, one "
               "**root per target org** (separate state + provider), and an **AFT** account-request "
               "file for scaled workload vending.")
    default_names = ", ".join(f"org-{i + 1}" for i in range(k))
    names_raw = st.text_input("Target org names (comma-separated)", value=default_names)
    names = [n.strip() for n in names_raw.split(",") if n.strip()] or [f"org-{i + 1}" for i in range(k)]
    bundle = iac.replication_bundle(design, names)
    st.download_button("⬇️ Download replication IaC (Terraform + AFT, .zip)", bundle,
                       file_name="lz-replication-iac.zip", mime="application/zip",
                       use_container_width=True)


def _scaled(design, factor):
    d = copy.deepcopy(design)
    d.num_workloads = max(1, round(design.num_workloads * factor))
    return d


# ===========================================================================
# 2. M&A — absorb acquired accounts into your org
# ===========================================================================

def _ma_absorb(design, derived):
    st.markdown("Bring an **acquired company's accounts into your existing organization**. Migrated "
                "accounts land in a permissive **quarantine OU**, are assessed and baselined, then "
                "enrolled into your governance and moved to their target OUs.")

    c1, c2, c3 = st.columns(3)
    m = c1.slider("Acquired member accounts", 1, 200, 15)
    whole_org = c2.toggle("Absorbing their whole org", value=True,
                          help="Includes migrating/decommissioning their management account.")
    regulated = c3.toggle("Acquiree handles regulated data", value=False)

    n_now = total_accounts(design)
    n_after = n_now + m
    enroll_h = m * (MIN_PER_ACCOUNT / 60)
    remediate_h = m * REMEDIATION_HOURS_PER_ACCT
    # cost: scale current model to the larger fleet (approx via workloads proxy)
    after_design = copy.deepcopy(design)
    after_design.num_workloads = design.num_workloads + m
    cost_after = estimate_monthly_cost(after_design)["total"]
    cost_delta = cost_after - derived["cost"]["total"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Accounts after merge", n_after, delta=f"+{m}")
    m2.metric("Enroll time", f"~{enroll_h:,.0f} h")
    m3.metric("Baseline remediation", f"~{remediate_h:,.0f} h")
    m4.metric("Added platform cost", f"${cost_delta:,.0f}/mo")

    _runbook([
        ("Phase 1 — Prepare & quarantine", [
            "Create an **Acquired / Quarantine OU** with a **permissive SCP** (allow-all or matching "
            "the acquiree's controls) so migrated accounts aren't broken by your stricter SCPs.",
            "Inventory the acquiree: accounts, workloads, CIDRs, identity, compliance scope, spend."]),
        ("Phase 2 — Migrate accounts", [
            "Remove each member account from the **source** organization, then **invite** it to yours "
            "and accept; initially place it in the Quarantine OU.",
            ("Migrating their **management account** requires first removing **all** its member "
             "accounts (not suspending) and **deleting** the source org configuration."
             if whole_org else
             "Member accounts can be invited directly; their management account stays in their org."),
        ]),
        ("Phase 3 — Assess & remediate", [
            "Run the **Live Estate** scan / IaC import to score each account; fix FAIL/WARN findings.",
            "Apply your baselines: centralized logging, GuardDuty/Security Hub/Config, encryption, "
            "tagging." + (" Apply heavy-compliance controls (LZA pack)." if regulated else "")]),
        ("Phase 4 — Enroll & graduate", [
            "**Enroll** each account into Control Tower; move it from Quarantine to its target OU so "
            "your SCPs apply progressively.",
            "Reconcile identity (map to your Identity Center / IdP groups) and connect networking "
            "(watch for overlapping CIDRs).",
            "Consolidate billing; decommission the acquiree's management account once empty."]),
    ])
    _risks([
        "Applying strict SCPs too early can break migrated workloads — graduate from the Quarantine OU "
        "gradually.",
        "Management-account migration is irreversible busywork: empty + delete the source org first.",
        "Overlapping VPC CIDRs between the two estates block simple peering — plan re-addressing or "
        "PrivateLink.",
        "Identity reconciliation (duplicate users, conflicting SSO) is often the long pole.",
        "Compliance scope may expand — re-run the assessment with the acquiree's frameworks selected.",
    ])
    _refs(["ma_network", "enroll", "ma_waf"])


# ===========================================================================
# 3. Divestiture / carve-out
# ===========================================================================

def _divestiture(design, derived):
    st.markdown("**Spin a business unit out into its own new organization** (the inverse of "
                "absorption). Stand up a fresh management account + landing zone, migrate the unit's "
                "accounts out of your org, and re-establish independent identity, security, and "
                "networking.")

    c1, c2 = st.columns(2)
    p = c1.slider("Accounts to carve out", 1, 100, 8)
    replicate = c2.toggle("Replicate your guardrails to the new org", value=True)

    core = core_account_count(design)
    new_org_accounts = p + core + 1  # carved accounts + new core + sandbox
    standup_h = BASELINE_HOURS_PER_ORG
    migrate_h = p * (MIN_PER_ACCOUNT / 60) + p * REMEDIATION_HOURS_PER_ACCT
    carved = copy.deepcopy(design)
    carved.num_workloads = p
    new_org_cost = estimate_monthly_cost(carved)["total"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("New-org accounts", new_org_accounts, help=f"{p} carved + {core} core + 1 sandbox")
    m2.metric("New-org platform cost", f"${new_org_cost:,.0f}/mo")
    m3.metric("Stand-up effort", f"~{standup_h} h")
    m4.metric("Migration effort", f"~{migrate_h:,.0f} h")

    _runbook([
        ("Day 0 — Stand up the new org", [
            "Create a new **management account** + organization (ALL features) for the divested unit.",
            "Build its landing zone (Control Tower/LZA): Security OU, **Log Archive + Audit**, Network, "
            "Shared Services." + (" Replicate your SCP guardrails." if replicate else "")]),
        ("Day 1 — Migrate the unit", [
            "An account can belong to **one org only** — remove each carved account from your org and "
            "invite it to the new org (place in a landing OU first).",
            "Re-establish per-account baselines (logging, security tooling delegated admin in the **new** "
            "org), identity, and budgets."]),
        ("Day 2 — Separate & validate", [
            "Split networking: detach from your TGW/Cloud WAN, stand up the unit's own egress, re-point "
            "DNS/PrivateLink.",
            "Separate billing (own payer), transfer data ownership, rotate cross-account roles, and "
            "validate guardrails + drift in the new org."]),
    ])
    _risks([
        "Identity re-pointing: the unit needs its own Identity Center / IdP integration — plan a cutover.",
        "Security tooling must be re-established in the new org (delegated admin doesn't follow accounts).",
        "Shared services the unit depended on (golden AMIs, CI/CD, registries) must be duplicated or "
        "vended via PrivateLink during transition.",
        "Data residency / compliance evidence must be re-baselined under the new org.",
        "Billing/credits and Reserved Instances/Savings Plans don't transfer between payers — model the "
        "cost impact.",
    ])
    _refs(["migrate_accounts", "ma_network", "lz_to_ct"])


# ===========================================================================
# 4. Geo / data-residency expansion
# ===========================================================================

def _geo_expansion(design, derived):
    st.markdown("Expand the landing zone into a **new region / jurisdiction** (e.g. enter the EU or "
                "APAC). Adds governed regions, region-restriction guardrails, and per-region baseline "
                "infrastructure.")

    new_regions = st.multiselect("New regions to add", [r for r in REGIONS if r not in design.regions],
                                 default=[r for r in REGIONS if r not in design.regions][:1])
    residency = st.toggle("Enforce data residency (deny activity outside approved regions)", value=True)

    after = copy.deepcopy(design)
    after.regions = list(design.regions) + new_regions
    cost_before = derived["cost"]["total"]
    cost_after = estimate_monthly_cost(after)["total"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Active regions", len(after.regions), delta=f"+{len(new_regions)}")
    m2.metric("Platform cost", f"${cost_after:,.0f}/mo", delta=f"+${cost_after - cost_before:,.0f}")
    m3.metric("New region baselines", len(new_regions),
              help="Each region duplicates NAT/endpoints/security tooling.")

    _runbook([
        ("Plan", [
            "Confirm **service availability** in the target regions and any data-sovereignty rules.",
            "Decide network model: extend TGW per region or adopt **Cloud WAN** for global segmentation."]),
        ("Build", [
            "Add the regions to **Control Tower governed regions**; deploy Config conformance packs / "
            "StackSets region-wide.",
            "Stand up per-region network (TGW, centralized egress, interface endpoints) and replicate "
            "security tooling (GuardDuty/Security Hub are per-region per-account)."]),
        ("Enforce", [
            ("Apply a **region-restriction SCP** (deny `aws:RequestedRegion` outside approved regions, "
             "with global-service exceptions) at the OU/root." if residency else
             "Optionally apply a region-restriction SCP for cost/blast-radius control."),
            "Deploy workloads close to users; validate residency with preventative controls + Config."]),
    ])
    _risks([
        "Not every AWS service is available in every region — verify before committing workloads.",
        "Each added region multiplies fixed baseline cost (NAT, endpoints, security tooling).",
        "Cross-region data transfer adds cost and latency — keep data-gravity in mind.",
        "Residency SCPs must whitelist global services (IAM, CloudFront, Route 53, STS, support).",
    ])
    _refs(["aft", "ma_waf"])


# ===========================================================================
# 5. Compliance onboarding
# ===========================================================================

def _compliance(design, derived):
    st.markdown("Onboard a **new compliance framework**. Simulates the guardrails, governance, and "
                "score impact of bringing the estate into scope.")

    add = st.multiselect("Frameworks to onboard",
                         [f for f in COMPLIANCE_FRAMEWORKS if f not in design.compliance],
                         default=[f for f in COMPLIANCE_FRAMEWORKS if f not in design.compliance][:1])
    harden = st.toggle("Apply recommended hardening (logging, security tooling, isolation, LZA)",
                       value=True)

    after = copy.deepcopy(design)
    after.compliance = list(design.compliance) + add
    if harden:
        after.centralized_logging = True
        after.security_tooling = True
        after.backup_dr = True
        if after.account_strategy in ("Single account", "Account per environment"):
            after.account_strategy = "Account per workload per environment"
        heavy = {"PCI-DSS", "HIPAA", "FedRAMP", "NIST 800-53", "APRA CPS 234", "HITRUST"}
        if heavy & set(add):
            after.governance = "Landing Zone Accelerator (LZA)"

    before_a, after_a = waf.assess(design), waf.assess(after)
    before_o, after_o = waf.overall_score(before_a), waf.overall_score(after_a)
    before_comp = score_design(design)["Compliance Readiness"]
    after_comp = score_design(after)["Compliance Readiness"]
    new_guardrails = [g for g in _guardrail_names(after) if g not in _guardrail_names(design)]

    m1, m2, m3 = st.columns(3)
    m1.metric("Well-Architected", f"{after_o}/100", delta=after_o - before_o)
    m2.metric("Compliance readiness", f"{after_comp}/100", delta=after_comp - before_comp)
    m3.metric("New guardrails", len(new_guardrails))

    if new_guardrails:
        st.caption("Added SCP guardrails: " + ", ".join(new_guardrails))

    _runbook([
        ("Assess", [
            "Run a gap assessment (Well-Architected tab + Live Estate) against the new framework.",
            "Map in-scope workloads and define the **PHI/CDE/regulated OU** boundary."]),
        ("Implement", [
            "Adopt **LZA compliance packs** (FedRAMP/PCI/CCCS) or Control Tower proactive/elective "
            "controls for the framework.",
            "Enforce: account-level isolation per workload, **immutable centralized logging** (S3 Object "
            "Lock), org-wide encryption SCPs, GuardDuty/Security Hub everywhere."]),
        ("Evidence", [
            "Turn on Audit Manager / Security Hub standards for continuous evidence.",
            "Snapshot a **target** in Drift and track convergence; schedule the collector for actuals."]),
    ])
    _risks([
        "Heavy frameworks penalize weak isolation hard — single-account or shared accounts won't pass.",
        "Encryption-in-transit/at-rest SCPs can break legacy workloads — stage rollout.",
        "Evidence collection is ongoing, not one-off — automate it.",
        "Region/residency constraints may apply (e.g. FedRAMP → GovCloud).",
    ])
    _refs(["lz_to_ct", "ma_waf"])


# ===========================================================================
# 6. Scale-out (N → M workloads)
# ===========================================================================

def _scale_out(design, derived):
    st.markdown("Project growth from today's footprint to a larger one and see how accounts, cost, and "
                "scores move — and when to industrialize account vending.")

    target = st.slider("Grow to N workloads", design.num_workloads, 200,
                       min(120, max(40, design.num_workloads * 3)))
    rows = []
    step = max(1, (target - design.num_workloads) // 24 or 1)
    for wl in range(design.num_workloads, target + 1, step):
        alt = copy.deepcopy(design)
        alt.num_workloads = wl
        s = score_design(alt)
        rows.append((wl, total_accounts(alt), estimate_monthly_cost(alt)["total"],
                     s["Scalability"], s["Operational Simplicity"]))
    end = rows[-1]
    seq_h, aft_h = _vending_time(end[1] - total_accounts(design))

    m1, m2, m3 = st.columns(3)
    m1.metric("Accounts at target", end[1], delta=end[1] - total_accounts(design))
    m2.metric("Platform cost at target", f"${end[2]:,.0f}/mo",
              delta=f"+${end[2]-derived['cost']['total']:,.0f}")
    m3.metric("Net-new vend time (AFT)", f"~{aft_h:,.0f} h")

    fig = go.Figure()
    xs = [r[0] for r in rows]
    fig.add_trace(go.Scatter(x=xs, y=[r[1] for r in rows], name="Accounts", line=dict(color="#FF9900", width=3)))
    fig.add_trace(go.Scatter(x=xs, y=[r[2] for r in rows], name="Cost ($/mo)", yaxis="y2",
                             line=dict(color="#2DD4BF", width=3)))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10),
                      xaxis=dict(title="Workloads"), yaxis=dict(title="Accounts"),
                      yaxis2=dict(title="USD/mo", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=-0.3))
    st.plotly_chart(fig, use_container_width=True)

    _runbook([
        ("Industrialize vending", [
            "Adopt **AFT** (Account Factory for Terraform) — GitOps account requests + customizations so "
            "every new account is born compliant.",
            "Request **service-quota** increases (accounts per org default soft limit) ahead of demand."]),
        ("Restructure for scale", [
            "Split the Workloads OU into Prod / Non-Prod (and per-BU) children so SCPs differ by "
            "criticality.",
            "Move to centralized egress / Cloud WAN as VPC count grows; consolidate interface endpoints."]),
        ("Operate at scale", [
            "Centralize observability and findings (delegated admin, Security Lake).",
            "Enforce tagging policies + budgets per account; track drift continuously."]),
    ])
    _risks([
        "Accounts-per-org soft limit — raise quotas before bulk creation.",
        "SCP changes propagate org-wide — test in a non-prod OU first.",
        "Peering meshes grow O(n²) — adopt TGW/Cloud WAN before it bites.",
        "Per-account fixed costs (Config/Security Hub/NAT) dominate at scale — consolidate egress.",
    ])
    _refs(["aft", "enroll"])


# ===========================================================================
# 7. Hybrid network — SD-WAN + Direct Connect across accounts (draggable)
# ===========================================================================

def _hybrid_network(design, derived):
    st.markdown("Model **hybrid connectivity** between on-premises datacenters and your AWS accounts: "
                "**Direct Connect** into a **Transit Gateway / Cloud WAN** hub that any-to-any connects "
                "the VPC spokes, with an optional **SD-WAN** overlay for branch sites. The topology "
                "below is **interactive — drag nodes to rearrange, click a node for details**.")

    c1, c2, c3 = st.columns(3)
    vpcs = c1.slider("AWS accounts / VPC spokes", 1, 40, max(2, workload_account_count(design)))
    dcs = c2.slider("On-prem datacenters", 1, 6, 2)
    hub = c3.selectbox("AWS hub", ["Transit Gateway", "AWS Cloud WAN"])
    c4, c5, c6 = st.columns(3)
    dx_speed = c4.selectbox("Direct Connect speed", list(pricing.DX_SPEEDS.keys()), index=1)
    dx_redundancy = c5.selectbox("DX resiliency",
                                 ["Single connection", "Redundant (2 ports/locations)",
                                  "DX + SD-WAN/VPN backup"])
    egress_tb = c6.slider("DX egress (TB/month)", 1, 200, 20)
    sdwan = st.toggle("SD-WAN overlay for branch sites", value=True)
    branches = st.slider("Branch / remote sites", 1, 50, 8) if sdwan else 0

    # --- cost simulation ---
    speed_key = pricing.DX_SPEEDS[dx_speed]
    dx_ports = dcs * (2 if dx_redundancy.startswith("Redundant") else 1)
    dx_port_cost = dx_ports * pricing.lp(speed_key) * pricing.HOURS_PER_MONTH
    dx_data_cost = egress_tb * 1000 * pricing.lp("dx_data_transfer_out_gb")
    if hub == "Transit Gateway":
        hub_cost = pricing.lp("tgw_attachment_hour") * pricing.HOURS_PER_MONTH * vpcs
    else:
        hub_cost = (pricing.lp("cloudwan_core_edge_hour") * pricing.HOURS_PER_MONTH
                    + pricing.lp("cloudwan_attachment_hour") * pricing.HOURS_PER_MONTH * vpcs)
    sdwan_cost = (2 * pricing.lp("sdwan_appliance_hour") * pricing.HOURS_PER_MONTH) if sdwan else 0
    total = dx_port_cost + dx_data_cost + hub_cost + sdwan_cost
    speed_gbps = {"1 Gbps": 1, "10 Gbps": 10, "100 Gbps": 100}[dx_speed]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("DX connections", dx_ports, help=f"{dcs} DC × {'2 (redundant)' if dx_ports > dcs else '1'}")
    m2.metric("Aggregate DX bandwidth", f"{dx_ports * speed_gbps} Gbps")
    m3.metric("Est. network cost", f"${total:,.0f}/mo")
    resilient = not dx_redundancy.startswith("Single")
    m4.metric("Resiliency", "✅ HA" if resilient else "⚠️ Single")
    if not resilient:
        st.warning("A single Direct Connect has no redundancy — AWS recommends ≥2 connections at "
                   "separate locations, or a Site-to-Site VPN / SD-WAN backup path.", icon="⚠️")

    # --- draggable topology ---
    st.markdown("##### Topology")
    _render_network_graph(vpcs, dcs, hub, dx_speed, sdwan, branches)

    with st.expander("Cost basis"):
        st.markdown(
            f"- **DX ports:** {dx_ports} × ${pricing.lp(speed_key)}/hr × {pricing.HOURS_PER_MONTH} = "
            f"${dx_port_cost:,.0f}/mo  \n"
            f"- **DX data out:** {egress_tb} TB × ${pricing.lp('dx_data_transfer_out_gb')}/GB = "
            f"${dx_data_cost:,.0f}/mo  \n"
            f"- **{hub} hub:** ${hub_cost:,.0f}/mo  \n"
            + (f"- **SD-WAN HA pair:** ${sdwan_cost:,.0f}/mo (excl. license)  \n" if sdwan else ""))

    _runbook([
        ("Foundation", [
            "Centralize connectivity in the **Network account**: stand up the "
            f"**{hub}** as the any-to-any hub.",
            "Order **Direct Connect** (dedicated or hosted) at "
            f"{dcs} location(s); for production use **≥2 connections** at separate DX locations.",
            "Create a **Direct Connect Gateway** and associate it with the hub (transit VIF)."]),
        ("Connect spokes & on-prem", [
            f"Attach the **{vpcs} VPC spokes** to the hub (TGW attachments or Cloud WAN segments) and "
            "propagate routes.",
            "Advertise on-prem CIDRs over the transit VIF; segment prod/non-prod with route tables or "
            "Cloud WAN segments."]
            + (["Deploy an **SD-WAN** appliance pair (HA) in the Network/Transit VPC; connect "
                f"the **{branches} branch sites** via the SD-WAN overlay to the hub."] if sdwan else [])),
        ("Resilience & ops", [
            "Add a **Site-to-Site VPN** (or SD-WAN) as an encrypted backup path to Direct Connect.",
            "Enable BGP, set up Reachability Analyzer / Network Manager monitoring, and centralize "
            "egress + inspection."]),
    ])
    _risks([
        "A single Direct Connect is a single point of failure — use redundant connections or a VPN/SD-WAN backup.",
        "Overlapping CIDRs between datacenters and VPCs break routing — plan addressing and use NAT/PrivateLink where needed.",
        "Direct Connect data-transfer-out is cheaper than internet but still material at scale (modeled above).",
        "SD-WAN appliances need an HA pair and a third-party license; size for throughput.",
        "Cloud WAN suits global, multi-region, segment-driven networks; a single-region estate is usually simpler on TGW.",
    ])
    _refs(["dx", "cloudwan", "ma_network"])

    st.divider()
    st.markdown("#### Generate the connectivity IaC")
    st.caption("Terraform scaffold for this topology: DX Gateway + transit VIF(s), the "
               f"{hub} hub, a Site-to-Site VPN backup path"
               + (", and SD-WAN appliances via Transit Gateway Connect (GRE/BGP)." if sdwan else "."))
    tf = iac.connectivity_terraform(hub, dx_speed, dx_redundancy.startswith("Redundant"), sdwan, vpcs)
    st.download_button("⬇️ Download connectivity.tf (Terraform)", tf,
                       file_name="connectivity.tf", mime="text/plain",
                       use_container_width=True, key="conn_tf_net")


# env colours aligned with the architecture-diagram palette (RED / AMBER / BLUE) for consistency
_ENV_DEFS = [("prod", "Prod", "#B0084D"), ("stage", "Staging", "#FF9900"), ("dev", "Dev+Test", "#5B8DEF")]
_SUBNET_TIERS = {"public": "#3F8624", "app": "#1A476F", "db": "#6E7A8C"}


def _env_split(vpcs):
    """Always return the three environments with a VPC count each (Prod gets extras)."""
    base = max(1, vpcs // 3)
    rem = max(0, vpcs - base * 3)
    counts = {"prod": base + rem, "stage": base, "dev": base}
    return [(k, lbl, col, counts[k]) for k, lbl, col in _ENV_DEFS]


def _env_cidr_plan(supernet="10.20.0.0/16", vpc_prefix=18, subnet_prefix=22):
    """Allocate one VPC block per environment (Prod/Staging/Dev+Test) with public/app/db
    subnets. Returns (env_cidrs, rows) — env_cidrs feeds the diagram, rows feed the
    VPC/subnet IaC and the acquisition package."""
    rows, _summary = cidr.allocate_plan(supernet, vpc_prefix, 3, subnet_prefix, 1,
                                        ["public", "app", "db"], ["Prod", "Staging", "Dev+Test"])
    name_to_key = {"Prod": "prod", "Staging": "stage", "Dev+Test": "dev"}
    env_cidrs = {}
    for r in rows:
        k = name_to_key.get(r["VPC"])
        if not k:
            continue
        env_cidrs.setdefault(k, {"vpc_cidr": r["VPC CIDR"], "subnets": {}})
        env_cidrs[k]["subnets"][r["Tier"]] = r["Subnet CIDR"]
    return env_cidrs, rows


def _ou_structure_dot(design, include_quarantine=False):
    """Graphviz OU tree for the landing zone, with Prod/Staging/Dev+Test environment OUs."""
    INK, NAVY, GREEN, TEAL, AMBER, GREY, RED = (
        "#232F3E", "#1A476F", "#3F8624", "#2DD4BF", "#FF9900", "#6E7A8C", "#B0084D")
    dot = ['digraph ou {',
           'rankdir=TB; bgcolor="transparent"; nodesep="0.28"; ranksep="0.55";',
           'node [fontname="Helvetica", fontcolor="white", style="filled,rounded", shape="box", '
           'color="#3A4555", penwidth="1.1", margin="0.16,0.07", fontsize="10"];',
           'edge [color="#5A6B86", arrowsize="0.6"];',
           f'mgmt [label="Management account\\n(billing · org root)", fillcolor="{INK}", '
           f'penwidth="2", color="#FF9900"];',
           f'ou_sec [label="Security OU", fillcolor="{NAVY}", shape="folder"];',
           f'log [label="Log Archive", fillcolor="{GREEN}"];',
           f'audit [label="Audit / Security Tooling", fillcolor="{GREEN}"];',
           'mgmt -> ou_sec; ou_sec -> log; ou_sec -> audit;',
           f'ou_inf [label="Infrastructure OU", fillcolor="{NAVY}", shape="folder"];',
           f'net [label="Network\\n(DX · SD-WAN · {design.network_pattern})", fillcolor="{TEAL}", fontcolor="#0B1220"];',
           f'shared [label="Shared Services", fillcolor="{TEAL}", fontcolor="#0B1220"];',
           'mgmt -> ou_inf; ou_inf -> net; ou_inf -> shared;',
           f'ou_wl [label="Workloads OU", fillcolor="{NAVY}", shape="folder"]; mgmt -> ou_wl;']
    for ekey, elabel, ecolor in _ENV_DEFS:
        dot.append(f'ou_{ekey} [label="{elabel} OU", fillcolor="{ecolor}", shape="folder"];')
        dot.append(f'ou_wl -> ou_{ekey};')
        dot.append(f'acc_{ekey} [label="workload accounts\\n(per {elabel})", fillcolor="{AMBER}", fontcolor="#232F3E"];')
        dot.append(f'ou_{ekey} -> acc_{ekey};')
    dot.append(f'ou_sand [label="Sandbox OU", fillcolor="{GREY}", shape="folder"]; mgmt -> ou_sand;')
    dot.append(f'ou_susp [label="Suspended OU", fillcolor="#4A2530", shape="folder"]; mgmt -> ou_susp;')
    if include_quarantine:
        dot.append(f'ou_acq [label="Acquired / Quarantine OU\\n(permissive SCP)", fillcolor="{RED}", shape="folder"]; '
                   'mgmt -> ou_acq;')
        dot.append(f'acc_b [label="Company B accounts\\n(migrated → baselined → graduated)", fillcolor="{GREY}"]; '
                   'ou_acq -> acc_b;')
    dot.append('}')
    return "\n".join(dot)


def _render_network_graph(vpcs, dcs, hub, dx_speed, sdwan, branches, key="net", env_cidrs=None):
    options = ["📐 Aligned diagram", "🅰️ AWS icons"]
    if _HAS_AGRAPH:
        options.append("🖐️ Interactive (drag)")
    view = st.radio("Diagram view", options, horizontal=True, key=f"net_view_{key}",
                    label_visibility="collapsed")

    if view.startswith("🅰️"):
        png = _aws_icon_diagram(vpcs, dcs, hub, dx_speed, sdwan, branches, env_cidrs)
        if png:
            st.image(png, use_container_width=True)
            st.caption("Rendered with official AWS Architecture Icons (diagrams library).")
            return
        st.info("AWS icon view needs the `diagrams` package + the Graphviz binary — "
                "showing the aligned diagram instead.", icon="ℹ️")
    elif view.startswith("🖐️") and _HAS_AGRAPH:
        _interactive_net(vpcs, dcs, hub, dx_speed, sdwan, branches, env_cidrs)
        return

    st.graphviz_chart(_aligned_dot(vpcs, dcs, hub, dx_speed, sdwan, branches, env_cidrs),
                      use_container_width=True)
    st.caption("On-premises → Direct Connect / SD-WAN → Network-account hub → workload VPCs, "
               "segmented by environment (Prod / Staging / Dev+Test).")


def _aws_icon_diagram(vpcs, dcs, hub, dx_speed, sdwan, branches, env_cidrs=None):
    """Render the topology with official AWS Architecture Icons via the `diagrams`
    library (needs the Graphviz binary). Returns PNG bytes, or None on any failure
    so the caller can fall back to the aligned diagram."""
    try:
        import os
        import tempfile
        from diagrams import Cluster, Diagram, Edge
        from diagrams.aws.compute import EC2
        from diagrams.aws.network import DirectConnect, PrivateSubnet, PublicSubnet, TransitGateway, VPC
        from diagrams.generic.place import Datacenter
    except Exception:
        return None
    ec = env_cidrs or {}
    n_br = min(branches, 6) if sdwan else 0
    try:
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "topology")
            with Diagram("", show=False, filename=path, outformat="png", direction="LR",
                         graph_attr={"bgcolor": "transparent", "pad": "0.4", "ranksep": "1.4",
                                     "nodesep": "0.5"}):
                with Cluster("On-premises"):
                    dc_nodes = [Datacenter(f"Datacenter {i + 1}") for i in range(dcs)]
                    br_nodes = [Datacenter(f"Branch {k + 1}") for k in range(n_br)]
                with Cluster("Network account"):
                    dxgw = DirectConnect("DX Gateway")
                    hub_node = TransitGateway(hub)
                    sdwan_node = EC2("SD-WAN (HA)") if sdwan else None
                env_vpcs = []
                with Cluster("Workload accounts"):
                    for ekey, elabel, _c, _n in _env_split(vpcs):
                        c = ec.get(ekey, {})
                        vcidr, subs = c.get("vpc_cidr", ""), c.get("subnets", {})
                        with Cluster(f"{elabel}" + (f" {vcidr}" if vcidr else "")):
                            v = VPC(f"VPC · {elabel}")
                            pub = PublicSubnet(("public " + subs.get("public", "")).strip())
                            app = PrivateSubnet(("app " + subs.get("app", "")).strip())
                            db = PrivateSubnet(("db " + subs.get("db", "")).strip())
                            v - pub
                            v - app
                            v - db
                            env_vpcs.append(v)
                for dc in dc_nodes:
                    dc >> Edge(label=dx_speed) >> dxgw
                dxgw >> Edge(label="transit VIF") >> hub_node
                if sdwan and sdwan_node is not None:
                    for br in br_nodes:
                        br >> Edge(label="SD-WAN") >> sdwan_node
                    sdwan_node >> Edge(label="overlay") >> hub_node
                for v in env_vpcs:
                    hub_node >> Edge(label="attachment") >> v
            with open(path + ".png", "rb") as f:
                return f.read()
    except Exception:
        return None


def _interactive_net(vpcs, dcs, hub, dx_speed, sdwan, branches, env_cidrs=None):
    INK, AMBER, TEAL, NAVY, GREY, SLATE = "#232F3E", "#FF9900", "#2DD4BF", "#1A476F", "#6E7A8C", "#3A4555"
    ec = env_cidrs or {}
    n_br_shown = min(branches, 10) if sdwan else 0
    nodes, edges, details = [], [], {}

    def n(nid, label, color, shape="box"):
        nodes.append(Node(id=nid, label=label, color=color, shape=shape, font=_net_font(color), margin=8))

    def e(a, b, label=""):
        edges.append(Edge(source=a, target=b, label=label, color="#5A6B86"))

    n("hub", hub, INK, shape="hexagon")
    details["hub"] = f"### {hub}\nAny-to-any hub in the Network account; spokes and on-prem connect here."
    n("dxgw", "Direct Connect Gateway", NAVY, shape="diamond")
    e("dxgw", "hub", "transit VIF")
    details["dxgw"] = "### Direct Connect Gateway\nAssociates the DX transit VIF with the hub."
    for i in range(dcs):
        n(f"dc{i}", f"Datacenter {i + 1}", GREY)
        e(f"dc{i}", "dxgw", dx_speed if i == 0 else "")
        details[f"dc{i}"] = f"### On-prem datacenter {i + 1}\nConnected over Direct Connect ({dx_speed})."
    for ekey, elabel, ecolor, ecount in _env_split(vpcs):
        c = ec.get(ekey, {})
        vcidr, subs = c.get("vpc_cidr", ""), c.get("subnets", {})
        n(f"env_{ekey}", elabel, ecolor, shape="diamond")
        e("hub", f"env_{ekey}", "attachment" if ekey == "prod" else "")
        details[f"env_{ekey}"] = (f"### {elabel} environment\nSegmented workload environment "
                                  f"(own route table / segment). VPC {vcidr or '—'}.")
        n(f"vpc_{ekey}", f"VPC · {elabel}" + (f"\n{vcidr}" if vcidr else ""), AMBER)
        e(f"env_{ekey}", f"vpc_{ekey}")
        for tier, tcolor in _SUBNET_TIERS.items():
            scidr = subs.get(tier, "")
            n(f"{tier}_{ekey}", f"{tier} subnet" + (f"\n{scidr}" if scidr else ""), tcolor)
            e(f"vpc_{ekey}", f"{tier}_{ekey}")
        if ecount > 1:
            n(f"more_{ekey}", f"+{ecount - 1} VPCs", GREY)
            e(f"env_{ekey}", f"more_{ekey}")
    if sdwan:
        n("sdwan", "SD-WAN appliances (HA)", TEAL, shape="diamond")
        e("sdwan", "hub", "overlay")
        details["sdwan"] = "### SD-WAN appliances\nHA pair in the transit VPC; terminates branch overlays."
        for k in range(n_br_shown):
            n(f"br{k}", f"Branch {k + 1}", SLATE)
            e(f"br{k}", "sdwan", "SD-WAN" if k == 0 else "")
            details[f"br{k}"] = "### Branch / remote site\nConnected via the SD-WAN overlay."
    cfg = Config(width="100%", height=480, directed=True, physics=True,
                 nodeHighlightBehavior=True, highlightColor=AMBER,
                 node={"labelProperty": "label"}, link={"renderLabel": True})
    clicked = agraph(nodes=nodes, edges=edges, config=cfg)
    if clicked and clicked in details:
        st.info(details[clicked])
    else:
        st.caption("💡 Drag nodes to rearrange · click a node for details.")


def _aligned_dot(vpcs, dcs, hub, dx_speed, sdwan, branches, env_cidrs=None):
    INK, AMBER, TEAL, NAVY, GREY, SLATE = "#232F3E", "#FF9900", "#2DD4BF", "#1A476F", "#6E7A8C", "#3A4555"
    ec = env_cidrs or {}
    n_br_shown = min(branches, 10) if sdwan else 0
    if True:
        dot = ['digraph net {',
               'rankdir=LR; bgcolor="transparent"; pad="0.35"; nodesep="0.32"; ranksep="1.1"; '
               'splines="spline";',
               'node [fontname="Helvetica", fontcolor="white", style="filled,rounded", '
               'shape="box", color="#3A4555", penwidth="1.2", margin="0.18,0.09"];',
               'edge [color="#5A6B86", fontcolor="#9FB0C8", fontname="Helvetica", fontsize="10", '
               'arrowsize="0.7"];']
        dot.append('subgraph cluster_onprem { label="On-premises"; fontcolor="#8C9CB8"; '
                   'color="#3A4555"; style="dashed,rounded";')
        for i in range(dcs):
            dot.append(f'dc{i} [label="Datacenter {i + 1}", fillcolor="{GREY}"];')
        for k in range(n_br_shown):
            dot.append(f'br{k} [label="Branch {k + 1}", fillcolor="{SLATE}"];')
        if sdwan and branches > n_br_shown:
            dot.append(f'brmore [label="+{branches - n_br_shown} sites", fillcolor="{GREY}"];')
        dot.append('}')
        dot.append('subgraph cluster_net { label="Network account"; fontcolor="#8C9CB8"; '
                   'color="#1A476F"; style="rounded";')
        dot.append(f'dxgw [label="Direct Connect\\nGateway", fillcolor="{NAVY}", shape="box"];')
        dot.append(f'hub [label="{hub}", fillcolor="{INK}", penwidth="2", color="#FF9900"];')
        if sdwan:
            dot.append(f'sdwan [label="SD-WAN\\nappliances (HA)", fillcolor="{TEAL}", fontcolor="#0B1220"];')
        dot.append('}')
        dot.append('subgraph cluster_wl { label="Workload accounts — per environment"; '
                   'fontcolor="#8C9CB8"; color="#C47400"; style="rounded";')
        tier_colors = {"public": "#3F8624", "app": "#1A476F", "db": "#6E7A8C"}
        for ekey, elabel, ecolor, ecount in _env_split(vpcs):
            c = ec.get(ekey, {})
            vcidr, subs = c.get("vpc_cidr", ""), c.get("subnets", {})
            dot.append(f'subgraph cluster_{ekey} {{ label="{elabel}{(" · " + vcidr) if vcidr else ""}"; '
                       f'fontcolor="{ecolor}"; color="{ecolor}"; style="rounded,bold";')
            dot.append(f'vpc_{ekey} [label="VPC · {elabel}", fillcolor="{AMBER}", fontcolor="#232F3E"];')
            for tier, tcolor in tier_colors.items():
                scidr = subs.get(tier, "")
                slbl = f"{tier} subnet\\n{scidr}" if scidr else f"{tier} subnet\\n(multi-AZ)"
                dot.append(f'{tier}_{ekey} [label="{slbl}", fillcolor="{tcolor}", fontsize="9"];')
                dot.append(f'vpc_{ekey} -> {tier}_{ekey} [arrowhead="none", color="#3A4555"];')
            if ecount > 1:
                dot.append(f'more_{ekey} [label="+{ecount - 1} VPCs", fillcolor="{GREY}", fontsize="9"];')
                dot.append(f'vpc_{ekey} -> more_{ekey} [style="dotted", arrowhead="none", color="#3A4555"];')
            dot.append('}')
        dot.append('}')
        for i in range(dcs):
            dot.append(f'dc{i} -> dxgw [label="{dx_speed if i == 0 else ""}"];')
        dot.append('dxgw -> hub [label="transit VIF"];')
        if sdwan:
            for k in range(n_br_shown):
                dot.append(f'br{k} -> sdwan [label="{"SD-WAN" if k == 0 else ""}"];')
            if branches > n_br_shown:
                dot.append('brmore -> sdwan;')
            dot.append('sdwan -> hub [label="overlay"];')
        for ekey, _lbl, _c, _n in _env_split(vpcs):
            dot.append(f'hub -> vpc_{ekey} [label="{"attachment" if ekey == "prod" else ""}"];')
        dot.append('}')
    return "\n".join(dot)


# ===========================================================================
# 8. AWS MGN — migrate EC2 instances from an acquired account
# ===========================================================================

def _mgn_migration(design, derived):
    st.markdown("Migrate EC2 instances from an **acquired AWS account** into your organization using "
                "**AWS Application Migration Service (MGN)** — continuous block-level replication into "
                "a staging area, then test and **cutover in waves**.")

    c1, c2, c3 = st.columns(3)
    instances = c1.number_input("EC2 instances to migrate", 1, 5000, 68)
    avg_gb = c2.slider("Avg storage per instance (GB)", 20, 2000, 100, 10)
    speed = c3.selectbox("Replication link bandwidth", ["1 Gbps", "10 Gbps", "500 Mbps"], index=0)
    c4, c5, c6 = st.columns(3)
    wave_size = c4.slider("Instances per cutover wave", 1, 50, 10)
    same_region = c5.toggle("Same region (cross-account)", value=True,
                            help="Off = cross-region (adds inter-region transfer + longer sync).")
    test_first = c6.toggle("Test-launch before cutover", value=True)

    total_gb = instances * avg_gb
    gbps = {"1 Gbps": 1.0, "10 Gbps": 10.0, "500 Mbps": 0.5}[speed]
    # initial sync: bits over the link at ~50% effective throughput
    sync_hours = (total_gb * 8) / (gbps * 0.5) / 3600
    waves = math.ceil(instances / wave_size)
    cutover_days = math.ceil(waves * (1.5 if test_first else 1.0))  # ~one wave/day with testing

    # cost: staging EBS for the migration window + replication fleet + transfer
    sync_months = max(0.25, sync_hours / 730)
    staging_ebs = total_gb * pricing.lp("ebs_gp3_gb") * max(1, math.ceil(sync_months))
    rep_servers = max(1, math.ceil(instances / 15))  # ~1 replication server per 15 source disks
    rep_compute = rep_servers * pricing.lp("mgn_replication_server_hour") * max(sync_hours, 730 * sync_months)
    transfer = 0 if same_region else total_gb * pricing.lp("interregion_transfer_gb")
    migration_cost = staging_ebs + rep_compute + transfer

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Instances", int(instances))
    m2.metric("Total data", f"{total_gb/1000:,.1f} TB")
    m3.metric("Initial sync", f"~{sync_hours:,.0f} h", help="At ~50% of link bandwidth")
    m4.metric("Cutover waves", waves, help=f"~{cutover_days} day(s) with {wave_size}/wave")
    st.metric("Est. one-time migration cost (staging + transfer)", f"${migration_cost:,.0f}")

    _runbook([
        ("Prepare (target account = your org)", [
            "Initialize **MGN** in the target account/region; create the **replication settings template** "
            "(staging subnet, instance type, EBS gp3, security group).",
            "Establish **network connectivity** between the acquired (source) account and the target "
            "(VPC peering / Transit Gateway / Direct Connect) for replication traffic.",
            "Set up **cross-account IAM** roles and the MGN service-linked role; map source→target subnets, "
            "security groups, and instance types in a launch template."]),
        ("Replicate", [
            f"Install the **MGN replication agent** on the **{int(instances)} source EC2 instances** (or use "
            "agentless where supported); MGN begins **continuous block-level replication** to the staging area.",
            f"Wait for **initial sync** (~{sync_hours:,.0f} h for {total_gb/1000:,.1f} TB at this bandwidth); "
            "ongoing changes replicate continuously after that."]),
        ("Test", [
            "Launch **test instances** from replicated state into an isolated target subnet; validate boot, "
            "drivers, application, and connectivity **without impacting the source**.",
            "Resolve boot/driver/licensing issues and refine the launch template."] if test_first else
            ["(Testing skipped — higher risk; recommended for production workloads.)"]),
        ("Cutover (in waves)", [
            f"Group the fleet into **{waves} wave(s)** of ~{wave_size}; per wave: quiesce app, do a final "
            "sync, **launch cutover instances**, repoint DNS / load balancers, and validate.",
            "Keep the source running until validated; MGN supports rollback before you finalize."]),
        ("Finalize", [
            "Mark each server **migration complete**, terminate replication servers and staging volumes "
            "(stops replication cost), and decommission the source account once empty.",
            "Enroll the migrated workloads under your guardrails (logging, GuardDuty/Security Hub, backup)."]),
    ])
    _risks([
        "Application dependencies & boot order — migrate dependent tiers in the same wave.",
        "OS/licensing: Windows/BYOL and KMS-encrypted volumes need pre-checks; some AMIs need driver fixes.",
        "Databases need quiescing or native replication for consistency — MGN is block-level, not app-aware.",
        "Cutover downtime per wave — schedule maintenance windows; keep rollback ready until finalize.",
        "Cross-account/cross-org networking + security-group/subnet mapping must be correct before cutover.",
        "MGN is free for 2160 hours per server (90 days) — long-running staging incurs EBS/compute cost (modeled).",
    ])
    _refs(["mgn", "mgn_account", "ma_network"])

    st.divider()
    st.markdown("#### Generate the per-wave cutover runbook")
    st.caption("Produces an executable checklist (and CSV wave plan) — VMs grouped into applications "
               "so dependent tiers (db → app → web) cut over together.")
    _runbook_downloads(instances, wave_size, key="mgn", target="target-account")


# ===========================================================================
# 9. M&A Integration (plug-and-play) — connect + absorb + migrate end-to-end
# ===========================================================================

def _ma_integration(design, derived):
    st.markdown("**End-to-end M&A integration blueprint.** Company A (acquirer) connects a **new AWS "
                "account** to its **datacenter** via **Direct Connect over SD-WAN**, then migrates "
                "Company B's **VMs into Company A using AWS MGN** — all under Company A's existing "
                "landing zone. A repeatable, *plug-and-play* pattern.")

    st.markdown("###### Scenario parameters")
    c1, c2, c3 = st.columns(3)
    a_accounts = c1.number_input("Company A existing AWS accounts", 1, 2000, 30)
    b_vms = c2.number_input("Company B VMs to migrate (MGN)", 1, 5000, 68)
    avg_gb = c3.slider("Avg storage per VM (GB)", 20, 2000, 100, 10)
    c4, c5, c6 = st.columns(3)
    dx_speed = c4.selectbox("Direct Connect speed", list(pricing.DX_SPEEDS.keys()), index=0)
    hub = c5.selectbox("AWS hub", ["Transit Gateway", "AWS Cloud WAN"])
    dx_redundant = c6.toggle("Redundant DX (2 connections)", value=True)

    # --- connectivity cost (1 datacenter, new account VPC + spokes) ---
    dx_ports = 2 if dx_redundant else 1
    speed_gbps = {"1 Gbps": 1, "10 Gbps": 10, "100 Gbps": 100}[dx_speed]
    dx_cost = dx_ports * pricing.lp(pricing.DX_SPEEDS[dx_speed]) * pricing.HOURS_PER_MONTH
    sdwan_cost = 2 * pricing.lp("sdwan_appliance_hour") * pricing.HOURS_PER_MONTH
    hub_attach = pricing.lp("tgw_attachment_hour") * pricing.HOURS_PER_MONTH * 3  # new acct + a couple spokes
    net_monthly = dx_cost + sdwan_cost + hub_attach

    # --- MGN migration of Company B VMs ---
    total_gb = b_vms * avg_gb
    sync_hours = (total_gb * 8) / (speed_gbps * 0.5) / 3600
    waves = math.ceil(b_vms / 10)
    sync_months = max(0.25, sync_hours / 730)
    staging = total_gb * pricing.lp("ebs_gp3_gb") * max(1, math.ceil(sync_months))
    rep = max(1, math.ceil(b_vms / 15)) * pricing.lp("mgn_replication_server_hour") * max(sync_hours, 730 * sync_months)
    migration_once = staging + rep

    accounts_after = int(a_accounts) + 1  # + the new integration account

    st.markdown("###### Integration at a glance")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Accounts after", accounts_after, delta="+1 new")
    m2.metric("DC↔AWS bandwidth", f"{dx_ports * speed_gbps} Gbps",
              help=f"{dx_ports} DX × {dx_speed} (SD-WAN overlay)")
    m3.metric("VMs via MGN", int(b_vms), help=f"{total_gb/1000:,.1f} TB · ~{sync_hours:,.0f} h initial sync")
    m4.metric("Cutover waves", waves)
    n1, n2 = st.columns(2)
    n1.metric("Connectivity cost", f"${net_monthly:,.0f}/mo")
    n2.metric("One-time migration cost", f"${migration_once:,.0f}")

    # --- Address plan (CIDR) for the three environments ---
    st.markdown("###### Address plan — per environment (Prod / Staging / Dev+Test)")
    ap1, ap2 = st.columns([1, 2])
    supernet = ap1.text_input("New-workloads supernet (IPAM pool)", value="10.20.0.0/16",
                              key="int_supernet")
    try:
        env_cidrs, plan_rows = _env_cidr_plan(supernet)
    except Exception as ex:  # noqa: BLE001
        ap2.error(f"Invalid supernet: {ex}")
        env_cidrs, plan_rows = {}, []
    if plan_rows:
        ap2.dataframe(
            [{"Environment": r["VPC"], "VPC CIDR": r["VPC CIDR"], "Subnet": r["Tier"],
              "Subnet CIDR": r["Subnet CIDR"], "Usable (AWS)": r["Usable (AWS)"]} for r in plan_rows],
            use_container_width=True, hide_index=True)
    # overlap check vs Company B + datacenter
    oc1, oc2 = st.columns(2)
    company_b = oc1.text_input("Company B existing CIDR(s)", value="10.20.5.0/24, 172.31.0.0/16",
                               key="int_companyb")
    dc_cidr = oc2.text_input("Datacenter CIDR(s)", value="10.100.0.0/16", key="int_dc")
    check_list = [r["Subnet CIDR"] for r in plan_rows] + \
        [c.strip() for c in (company_b + "," + dc_cidr).split(",") if c.strip()]
    overlaps, invalid = cidr.find_overlaps(check_list)
    if invalid:
        st.warning("Unparseable CIDR(s): " + ", ".join(invalid), icon="⚠️")
    if overlaps:
        st.error(f"❌ {len(overlaps)} overlap(s) — re-address before peering/migration: "
                 + "; ".join(f"{a} ↔ {b}" for a, b in overlaps[:6]))
    else:
        st.success("✅ No overlaps between the new environment CIDRs, Company B, and the datacenter.")

    st.markdown("###### Target topology — segmented by environment")
    _render_network_graph(vpcs=6, dcs=1, hub=hub, dx_speed=dx_speed, sdwan=True, branches=3,
                          key="integration", env_cidrs=env_cidrs)

    st.markdown("###### Landing Zone — OU structure (after integration)")
    st.graphviz_chart(_ou_structure_dot(design, include_quarantine=True), use_container_width=True)
    st.caption("Company B's accounts land in the **Acquired / Quarantine OU** (permissive SCP), are "
               "baselined, then graduate into the Prod / Staging / Dev+Test OUs.")

    st.markdown("###### 🎞️ End-state architecture (animated)")
    end_view = st.radio(
        "View", ["Two-region overview (us-east-1 + us-west-2)", "Single-region deep-dive (us-east-1)",
                 "Multi-account org view"],
        horizontal=True, key="endstate_view", label_visibility="collapsed")
    if end_view.startswith("Multi-account"):
        st.caption("**Org-wide account view** — a **horizontal band of platform accounts** "
                   "(**Network** with the **Transit Gateway**, **Security**, **Shared Services**, "
                   "**Deployment**, **ITSM**, **Monitoring**) over the **TGW attachment fabric**, and "
                   "**vertical application accounts** (App1…) **drilled down Account → VPC → subnet** "
                   "(public/app/db, per region). **Every VPC attaches to the Network-account TGW**; the "
                   "regional TGWs are **peered** and all attachments are **centrally inspected**.")
        _end_html = arch_diagram.accounts_view(("us-east-1", "us-west-2"))
        _h, _fn = 760, "accounts-network-view.html"
    elif end_view.startswith("Single"):
        st.caption("**Deep-dive** of one region fully expanded — **two AZs**, every **subnet** "
                   "(public/app/db), **route tables**, **AWS Network Firewall endpoints per AZ**, "
                   "**NAT per AZ**, **IGW**, **TGW attachments**, and **Shared Services** "
                   "(Active Directory · ITSM · AWS SSO · Route 53 Resolver · endpoints). Flows show "
                   "**① ingress, ② egress, ③ east-west — all inspected by the firewall**.")
        _end_html = arch_diagram.single_region_zoom("us-east-1", "10.20.0.0/16", dx_speed)
        _h, _fn = 780, "end-state-single-region.html"
    else:
        st.caption("Detailed end state across **us-east-1 + us-west-2** — datacenter + branches, "
                   "**Direct Connect** + **VPN backup**, per-region **Transit Gateway** with "
                   "**inter-region peering**, **AWS Network Firewall** (inspection), **NAT/egress**, "
                   "**SD-WAN**, the **Security** account, **Shared Services (AD · ITSM · AWS SSO)**, "
                   "and the **Prod/Staging/Dev+Test VPCs with public/app/db subnets**. Dashes/packets animate "
                   "the **traffic flow** (legend at the bottom).")
        _end_html = arch_diagram.animated_endstate(("us-east-1", "us-west-2"), dx_speed, True)
        _h, _fn = 700, "end-state-two-region.html"
    st.components.v1.html(_end_html, height=_h, scrolling=True)
    st.download_button("⬇️ This diagram (animated HTML)", _end_html, file_name=_fn,
                       mime="text/html", use_container_width=True, key="endstate_dl")

    st.divider()
    st.markdown("#### ✅ Well-Architected alignment — how this M&A scenario satisfies every pillar")
    st.caption("Maps each of the six AWS Well-Architected pillars to the concrete design decisions in "
               "**this acquisition**. The scored, design-wide assessment + the WAF-aligned capability map "
               "live in the **Well-Architected** tab.")
    _dx_txt = "two Direct Connect links (HA)" if dx_redundant else \
        "a Direct Connect link with a Site-to-Site VPN backup"
    _waf_map = [
        ("🛠️ Operational Excellence", [
            ("Perform operations as code (repeatable)", f"New account vended via **Account Factory / AFT**; "
             "**connectivity.tf** + the integration IaC bundle codify DX/SD-WAN/hub/VPCs — the whole "
             "pattern is **replayable for the next acquisition**."),
            ("Observability & change management", "**Monitoring account** (CloudWatch · Coralogix) + "
             "centralized logs to **Log Archive**; **ITSM** (ServiceNow) integration for change control."),
            ("Small, reversible changes", f"Cutover in **{waves} wave(s) of ~10 VMs**, each test-launched "
             "first; **rollback retained until finalize**."),
        ]),
        ("🔒 Security", [
            ("Strong identity & isolation", "**IAM Identity Center (AWS SSO)** federation; the **Acquired / "
             "Quarantine OU** (permissive SCP) isolates Company B until baselined, then graduates into the "
             "**Prod / Staging / Dev+Test** OUs."),
            ("Detective controls (delegated admin)", "**Security account**: GuardDuty · Security Hub · AWS "
             "Config · **org CloudTrail** · **Log Archive (S3 Object Lock)** · Access Analyzer."),
            ("Network & data protection", "**AWS Network Firewall** (appliance-mode) inspects **all** "
             "VPC↔VPC + on-prem traffic; **encrypted** DX + Site-to-Site VPN; KMS-encrypted volumes."),
        ]),
        ("♻️ Reliability", [
            ("No single point of failure", f"Hybrid link uses **{_dx_txt}** with **DX⇄VPN auto-failover**; "
             "**multi-AZ (×2)** subnets; **HA SD-WAN** appliance pair."),
            ("Multi-region foundation", f"**{hub}** any-to-any with **inter-region TGW peering** "
             "(us-east-1 + us-west-2) — transitive, no full-mesh."),
            ("Safe migration", f"**MGN** block replication + **test-launch in an isolated subnet** "
             "(validate without impacting source); **CIDR-overlap checks** before peering."),
        ]),
        ("⚡ Performance Efficiency", [
            ("Scalable network topology", "**TGW transitive** routing avoids O(n²) VPC peering; **SD-WAN "
             "overlay** (TGW Connect/GRE) optimizes the on-prem path; **PrivateLink** endpoints."),
            ("Right-sized compute", "MGN **launch templates** map each source VM to a right-sized instance "
             "type; **gp3** staging volumes."),
            ("Data-driven placement", "Region/AZ placement per latency + data-residency; centralized "
             "egress/NAT keeps the data path efficient."),
        ]),
        ("💰 Cost Optimization", [
            ("Cost-aware account structure", "**Account-per-environment/workload** → granular **cost "
             "allocation** + **showback / chargeback** (Consumption Management); shared centralized **NAT**."),
            ("Eliminate waste", f"**Decommission Company B's {int(b_vms)} source VMs / datacenter** after "
             "cutover (stop paying twice); right-sizing; MGN free for the first 90 days."),
            ("Plan & track spend", "Budgeting & planning, cost-allocation tags, **Resource Optimization** "
             "(Financial Governance)."),
        ]),
        ("🌱 Sustainability", [
            ("Reduce provisioned footprint", f"Retire Company B's **on-prem datacenter** ({int(b_vms)} VMs) "
             "onto shared, multi-tenant **platform accounts**; managed services (Managed AD) cut idle capacity."),
            ("Match supply to demand", "Auto-scaling + right-sizing align capacity to load; consolidate "
             "onto fewer, better-utilized regions."),
        ]),
    ]
    _wcols = st.columns(2)
    for _i, (_p, _rows) in enumerate(_waf_map):
        with _wcols[_i % 2]:
            with st.expander(f"{_p}  —  ✅ satisfied", expanded=(_i < 2)):
                for _crit, _how in _rows:
                    st.markdown(f"- ✅ **{_crit}** — {_how}")
    st.success("All six Well-Architected pillars are addressed by concrete decisions in this scenario "
               "(see each pillar above).")
    _waf_md = "# M&A Landing Zone — AWS Well-Architected alignment\n\n"
    for _p, _rows in _waf_map:
        _waf_md += f"## {_p}\n\n" + "".join(f"- **{_c}** — {_h}\n" for _c, _h in _rows) + "\n"
    st.download_button("⬇️ WAF alignment for this scenario (Markdown)", _waf_md,
                       file_name="ma-waf-alignment.md", mime="text/markdown",
                       use_container_width=True, key="ma_waf_md_dl")

    _runbook([
        ("Phase 0 — Connect the new account to the datacenter (DX over SD-WAN)", [
            f"In Company A's **Network account**, use the **{hub}** as the any-to-any hub.",
            f"Order **Direct Connect** ({'2 connections for HA' if dx_redundant else '1 connection'}, "
            f"{dx_speed}) to the datacenter; create a **DX Gateway** + transit VIF to the hub.",
            "Deploy an **SD-WAN** appliance pair (HA) in the transit VPC; bring the datacenter in over the "
            "SD-WAN overlay with the DX as the underlay; add a **Site-to-Site VPN** backup path.",
            "**Vend the new AWS account** under Company A's org (Account Factory / AFT), apply baselines + "
            "guardrails, and attach its VPC to the hub."]),
        ("Phase 1 — Prepare MGN in the target (Company A)", [
            "Initialize **AWS MGN** in the target account/region; build the **replication settings template** "
            "(staging subnet, gp3, security group) and the launch template (subnet/SG/instance-type mapping).",
            "Ensure **connectivity from Company B** to the MGN staging area over the DX/SD-WAN (or a temporary "
            "VPN); set up cross-account IAM + the MGN service-linked role."]),
        ("Phase 2 — Replicate & test the 68 VMs", [
            f"Install the **MGN agent** on Company B's **{int(b_vms)} VMs**; continuous block replication "
            f"begins (~{sync_hours:,.0f} h initial sync for {total_gb/1000:,.1f} TB).",
            "**Test-launch** into an isolated subnet; validate boot/drivers/app and fix the launch template "
            "**without impacting the source**."]),
        ("Phase 3 — Cutover in waves & integrate", [
            f"Cut over in **{waves} wave(s)** (~10 VMs each): quiesce, final sync, launch, repoint DNS/LB, "
            "validate; keep rollback until finalize.",
            "Enroll migrated workloads under Company A's guardrails (logging, GuardDuty/Security Hub, backup); "
            "federate identity; **decommission Company B's source** once complete."]),
    ])
    _risks([
        "Single Direct Connect = single point of failure — use 2 connections or a VPN/SD-WAN backup (modeled above).",
        "Overlapping CIDRs between the datacenter, Company B, and Company A VPCs will block routing — plan addressing.",
        "MGN is block-level, not app-aware — quiesce databases or use native replication for consistency.",
        "Migrate dependent application tiers in the same wave; schedule per-wave downtime windows.",
        "Security tooling delegated admin is per-organization — the new account joins Company A's existing planes.",
        "Windows/BYOL licensing and KMS-encrypted volumes need pre-checks before cutover.",
    ])
    _refs(["dx", "mgn", "mgn_account", "ma_network", "aft"])

    st.divider()
    st.markdown("#### Plug-and-play: generate the integration IaC")
    st.caption("Emits the Terraform scaffold to vend the new integration account and replicate Company "
               "A's blueprint — a reusable starting point for repeating this pattern across acquisitions.")
    ic1, ic2 = st.columns(2)
    bundle = iac.replication_bundle(design, ["company-a-integration"])
    ic1.download_button("⬇️ Integration account IaC (Terraform + AFT, .zip)", bundle,
                        file_name="ma-integration-iac.zip", mime="application/zip",
                        use_container_width=True)
    conn_tf = iac.connectivity_terraform(hub, dx_speed, dx_redundant, True, 5)
    ic2.download_button("⬇️ Phase-0 connectivity.tf (DX + SD-WAN + hub)", conn_tf,
                        file_name="connectivity.tf", mime="text/plain",
                        use_container_width=True, key="conn_tf_integration")

    st.divider()
    st.markdown("#### 🌐 Detailed networking setup guide")
    st.caption("A step-by-step runbook to build the hybrid network for this scenario: order Direct "
               "Connect, build the hub, transit VIF, SD-WAN overlay (TGW Connect), VPN backup, "
               "per-environment segmentation with the CIDRs above, the MGN replication path, and "
               "validation.")
    st.download_button("⬇️ Download networking-setup.md",
                       _networking_setup_guide(hub, dx_speed, dx_redundant, env_cidrs, b_vms,
                                               dc_cidr=dc_cidr, b_cidr=company_b),
                       file_name="networking-setup.md", mime="text/markdown",
                       use_container_width=True, key="netguide_integration")

    st.divider()
    st.markdown("#### Plug-and-play: per-wave MGN cutover runbook for Company B's VMs")
    st.caption("An executable checklist (+ CSV wave plan) to migrate the VMs in dependency-safe waves.")
    _runbook_downloads(b_vms, 10, key="integration", target="company-a")

    st.divider()
    st.markdown("#### 📦 One-click: the whole acquisition package")
    st.caption("Bundles every artifact into one consistent `.zip` — IP plan + VPC/subnet IaC + "
               "connectivity (DX + SD-WAN + hub) + integration account IaC + MGN cutover runbook — "
               "with a master README and deploy order.")
    st.download_button("⬇️ Download acquisition-playbook.zip",
                       _acquisition_package(design, hub, dx_speed, dx_redundant, b_vms,
                                            plan_rows=plan_rows, env_cidrs=env_cidrs),
                       file_name="acquisition-playbook.zip", mime="application/zip",
                       use_container_width=True, type="primary")


# ---------------------------------------------------------------------------
# Registry + entry point
# ---------------------------------------------------------------------------
_SCENARIOS = [
    ("🧩 M&A Integration — connect + absorb + migrate (plug-and-play)", _ma_integration),
    ("🏢 M&A — replicate blueprint to N orgs", _ma_replicate),
    ("🤝 M&A — absorb acquired accounts", _ma_absorb),
    ("🌐 Hybrid network — Direct Connect + SD-WAN across accounts", _hybrid_network),
    ("🖥️ MGN — migrate EC2/VMs from an acquired account", _mgn_migration),
    ("✂️ Divestiture — carve out a business unit", _divestiture),
    ("🌍 Expansion — new region / data residency", _geo_expansion),
    ("🛡️ Compliance — onboard a new framework", _compliance),
    ("📈 Scale-out — grow N→M workloads", _scale_out),
]


def render(design, derived):
    st.subheader("Scenario playbooks")
    st.caption("Pick an enterprise event and simulate it against your **current design** (the golden "
               "blueprint). Each playbook computes live numbers and a grounded, AWS-aligned runbook. "
               "Estimates are directional — validate before executing.")
    titles = [t for t, _ in _SCENARIOS]
    choice = st.selectbox("Scenario", titles, key="playbook_choice")
    st.divider()
    fn = dict(_SCENARIOS)[choice]
    fn(design, derived)
