"""CIDR / IP address planner for landing-zone network design.

Pure-stdlib (`ipaddress`) utility with four modes:
  * Inspect   — full breakdown of one CIDR (hosts, usable, AWS-usable).
  * Allocate  — carve a supernet into per-VPC and per-subnet blocks and **show
                clearly how the address space is utilized** (treemap + metrics +
                usable-host table + CSV).
  * Overlap   — detect overlaps across a list of CIDRs (e.g. Company A vs B vs
                datacenter during an M&A migration).
  * Subdivide — split a CIDR into equal subnets of a chosen prefix.
"""

from __future__ import annotations

import ipaddress

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# AWS reserves 5 IPs in every subnet (network, VPC router, DNS, future, broadcast).
AWS_RESERVED = 5


def inspect(cidr: str) -> dict:
    net = ipaddress.ip_network(cidr, strict=False)
    total = net.num_addresses
    if net.version == 4 and net.prefixlen <= 30:
        usable = total - 2
        first, last = str(net.network_address + 1), str(net.broadcast_address - 1)
    else:
        usable, first, last = total, str(net.network_address), str(net.broadcast_address)
    return {
        "CIDR": str(net), "Version": f"IPv{net.version}",
        "Network address": str(net.network_address),
        "Broadcast": str(net.broadcast_address) if net.version == 4 else "—",
        "Netmask": str(net.netmask), "Prefix": f"/{net.prefixlen}",
        "Total addresses": total, "Usable hosts": max(0, usable),
        "AWS-usable hosts": max(0, total - AWS_RESERVED),
        "First usable": first, "Last usable": last,
        "Private (RFC1918)": "Yes" if net.is_private else "No",
    }


def subdivide(cidr: str, new_prefix: int) -> list:
    return list(ipaddress.ip_network(cidr, strict=False).subnets(new_prefix=new_prefix))


def find_overlaps(cidrs: list) -> tuple[list, list]:
    """Return (overlaps, invalid). overlaps = [(a, b)] pairs that overlap."""
    nets, invalid = [], []
    for c in cidrs:
        c = c.strip()
        if not c:
            continue
        try:
            nets.append((c, ipaddress.ip_network(c, strict=False)))
        except ValueError:
            invalid.append(c)
    overlaps = []
    for i in range(len(nets)):
        for j in range(i + 1, len(nets)):
            if nets[i][1].overlaps(nets[j][1]):
                overlaps.append((nets[i][0], nets[j][0]))
    return overlaps, invalid


def allocate_plan(supernet: str, vpc_prefix: int, n_vpcs: int, subnet_prefix: int,
                  n_azs: int, tiers: list, vpc_names=None):
    """Carve `supernet` into n_vpcs blocks of /vpc_prefix; within each, allocate
    tiers × AZs subnets of /subnet_prefix. Returns (rows, summary)."""
    sn = ipaddress.ip_network(supernet, strict=False)
    if vpc_prefix < sn.prefixlen:
        raise ValueError("VPC prefix must be longer (smaller block) than the supernet prefix.")
    if subnet_prefix < vpc_prefix:
        raise ValueError("Subnet prefix must be longer (smaller block) than the VPC prefix.")

    vpc_blocks = list(sn.subnets(new_prefix=vpc_prefix))
    n_alloc = min(n_vpcs, len(vpc_blocks))
    need_per_vpc = len(tiers) * n_azs
    vpc_addrs = 2 ** (32 - vpc_prefix)
    subnet_addrs = 2 ** (32 - subnet_prefix)
    subnets_per_vpc = vpc_addrs // subnet_addrs

    rows = []
    for vi in range(n_alloc):
        vb = vpc_blocks[vi]
        name = (vpc_names[vi] if vpc_names and vi < len(vpc_names) else f"vpc-{vi + 1}")
        subs = list(vb.subnets(new_prefix=subnet_prefix))
        idx = 0
        for tier in tiers:
            for az in range(n_azs):
                if idx < len(subs):
                    s = subs[idx]
                    rows.append({
                        "VPC": name, "VPC CIDR": str(vb),
                        "Subnet": f"{tier}-az{az + 1}", "Subnet CIDR": str(s),
                        "Addresses": s.num_addresses,
                        "Usable (AWS)": max(0, s.num_addresses - AWS_RESERVED),
                    })
                    idx += 1

    used_subnet_addrs = min(need_per_vpc, subnets_per_vpc) * subnet_addrs
    summary = {
        "supernet": str(sn),
        "supernet_addrs": sn.num_addresses,
        "vpc_blocks_total": len(vpc_blocks),
        "vpcs_allocated": n_alloc,
        "vpc_addrs": vpc_addrs,
        "subnet_addrs": subnet_addrs,
        "subnets_per_vpc_capacity": subnets_per_vpc,
        "subnets_per_vpc_used": min(need_per_vpc, subnets_per_vpc),
        "used_subnet_addrs_per_vpc": used_subnet_addrs,
        "supernet_util_pct": round(100 * n_alloc * vpc_addrs / sn.num_addresses, 1),
        "vpc_util_pct": round(100 * used_subnet_addrs / vpc_addrs, 1),
        "total_aws_usable": sum(r["Usable (AWS)"] for r in rows),
        "enough_subnets": subnets_per_vpc >= need_per_vpc,
    }
    return rows, summary


def _utilization_treemap(rows, summary):
    """Treemap: supernet -> VPCs -> subnets, with explicit free/unallocated blocks."""
    labels, parents, values, colors = [], [], [], []
    root = f"Supernet {summary['supernet']}"
    labels.append(root); parents.append(""); values.append(summary["supernet_addrs"]); colors.append("#101A2C")

    by_vpc = {}
    for r in rows:
        by_vpc.setdefault((r["VPC"], r["VPC CIDR"]), []).append(r)

    for (vpc, vcidr), subs in by_vpc.items():
        vlabel = f"{vpc}\n{vcidr}"
        labels.append(vlabel); parents.append(root); values.append(summary["vpc_addrs"]); colors.append("#1A476F")
        used = 0
        for r in subs:
            labels.append(f"{r['Subnet']}\n{r['Subnet CIDR']}")
            parents.append(vlabel); values.append(r["Addresses"]); colors.append("#FF9900")
            used += r["Addresses"]
        free = summary["vpc_addrs"] - used
        if free > 0:
            labels.append(f"free · {vpc}"); parents.append(vlabel); values.append(free); colors.append("#2A3548")

    sn_free = summary["supernet_addrs"] - summary["vpcs_allocated"] * summary["vpc_addrs"]
    if sn_free > 0:
        labels.append("unallocated VPC space"); parents.append(root); values.append(sn_free); colors.append("#2A3548")

    fig = go.Figure(go.Treemap(
        labels=labels, parents=parents, values=values, branchvalues="total",
        marker_colors=colors, textinfo="label+value+percent parent",
        hovertemplate="<b>%{label}</b><br>%{value:,} addresses<br>%{percentParent} of parent<extra></extra>"))
    fig.update_layout(height=460, margin=dict(l=8, r=8, t=8, b=8))
    return fig


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render(design=None):
    st.subheader("CIDR / IP address planner")
    st.caption("Plan non-overlapping VPC and subnet address space for the landing zone, "
               "see exactly how the IPs are utilized, and catch overlaps before they break "
               "routing (a common M&A / migration pitfall).")

    mode = st.radio("Mode", ["📊 Allocate IP plan", "🔎 Inspect a CIDR",
                             "⚠️ Overlap check", "✂️ Subdivide"], horizontal=True)
    st.divider()

    if mode.startswith("📊"):
        _render_allocate(design)
    elif mode.startswith("🔎"):
        _render_inspect()
    elif mode.startswith("⚠️"):
        _render_overlap()
    else:
        _render_subdivide()


def _render_allocate(design):
    c1, c2, c3 = st.columns(3)
    supernet = c1.text_input("Supernet (your IPAM pool)", value="10.0.0.0/8")
    default_vpcs = 4
    if design is not None:
        try:
            from lz_core import workload_account_count
            default_vpcs = max(1, min(64, workload_account_count(design)))
        except Exception:
            pass
    n_vpcs = c2.slider("VPCs to allocate", 1, 64, default_vpcs)
    vpc_prefix = c3.slider("VPC block size (prefix)", 12, 24, 16,
                           help="/16 = 65,536 addresses per VPC")
    c4, c5, c6 = st.columns(3)
    subnet_prefix = c4.slider("Subnet size (prefix)", vpc_prefix + 1, 28, max(vpc_prefix + 1, 24))
    n_azs = c5.slider("Availability Zones", 1, 4, 3)
    tiers_str = c6.text_input("Subnet tiers", value="public, app, db")
    tiers = [t.strip() for t in tiers_str.split(",") if t.strip()] or ["public", "private"]

    try:
        rows, s = allocate_plan(supernet, vpc_prefix, n_vpcs, subnet_prefix, n_azs, tiers)
    except ValueError as e:
        st.error(str(e))
        return

    if not s["enough_subnets"]:
        st.warning(f"A /{vpc_prefix} VPC only fits {s['subnets_per_vpc_capacity']} subnets of "
                   f"/{subnet_prefix}, but {len(tiers)} tiers × {n_azs} AZs need "
                   f"{len(tiers) * n_azs}. Increase the VPC size or shrink the subnets.", icon="⚠️")

    # --- utilization metrics ---
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("VPCs allocated", f"{s['vpcs_allocated']} / {s['vpc_blocks_total']}",
              help=f"/{vpc_prefix} blocks available in {s['supernet']}")
    m2.metric("Supernet utilized", f"{s['supernet_util_pct']}%",
              help=f"{s['vpcs_allocated'] * s['vpc_addrs']:,} of {s['supernet_addrs']:,} addresses")
    m3.metric("Per-VPC utilized", f"{s['vpc_util_pct']}%",
              help=f"{s['used_subnet_addrs_per_vpc']:,} of {s['vpc_addrs']:,} addresses in subnets")
    m4.metric("Total AWS-usable hosts", f"{s['total_aws_usable']:,}",
              help="Across all allocated subnets (5 IPs/subnet reserved by AWS)")

    st.markdown("##### How the address space is utilized")
    st.plotly_chart(_utilization_treemap(rows, s), use_container_width=True)
    st.caption("Amber = subnets · navy = VPC blocks · grey = free/unallocated. "
               "Click a block to zoom in.")

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("⬇️ Download IP plan (CSV)", df.to_csv(index=False),
                       file_name="landing-zone-ip-plan.csv", mime="text/csv")


def _render_inspect():
    cidr = st.text_input("CIDR", value="10.0.0.0/16")
    try:
        info = inspect(cidr)
    except ValueError as e:
        st.error(f"Invalid CIDR: {e}")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Total addresses", f"{info['Total addresses']:,}")
    m2.metric("Usable hosts", f"{info['Usable hosts']:,}")
    m3.metric("AWS-usable hosts", f"{info['AWS-usable hosts']:,}",
              help="AWS reserves 5 IPs per subnet")
    st.dataframe(pd.DataFrame([{"Property": k, "Value": str(v)} for k, v in info.items()]),
                 use_container_width=True, hide_index=True)


def _render_overlap():
    st.caption("One CIDR per line. Example pre-filled with an M&A case (Company B overlaps Company A).")
    text = st.text_area("CIDRs to check", height=160,
                        value="10.0.0.0/16        # Company A VPCs\n"
                              "10.0.5.0/24        # Company B (OVERLAPS!)\n"
                              "172.16.0.0/16      # Company A datacenter\n"
                              "192.168.0.0/24     # Branch office")
    cidrs = [line.split("#")[0].strip() for line in text.splitlines()]
    overlaps, invalid = find_overlaps(cidrs)
    if invalid:
        st.error("Invalid CIDR(s): " + ", ".join(invalid))
    if overlaps:
        st.error(f"❌ {len(overlaps)} overlap(s) found — these will break routing/peering:")
        st.dataframe(pd.DataFrame([{"CIDR A": a, "CIDR B": b} for a, b in overlaps]),
                     use_container_width=True, hide_index=True)
        st.caption("Re-address one side, or use private NAT / PrivateLink for the overlapping ranges.")
    elif not invalid:
        st.success("✅ No overlaps — these ranges can be peered / routed together.")


def _render_subdivide():
    c1, c2 = st.columns(2)
    cidr = c1.text_input("CIDR to split", value="10.0.0.0/20")
    new_prefix = c2.slider("Into /", 1, 32, 24)
    try:
        subs = subdivide(cidr, new_prefix)
    except ValueError as e:
        st.error(f"Cannot subdivide: {e}")
        return
    st.metric("Resulting subnets", f"{len(subs):,}")
    df = pd.DataFrame([{"#": i + 1, "Subnet": str(sn_),
                        "Addresses": sn_.num_addresses,
                        "Usable (AWS)": max(0, sn_.num_addresses - AWS_RESERVED)}
                       for i, sn_ in enumerate(subs[:512])])
    st.dataframe(df, use_container_width=True, hide_index=True)
    if len(subs) > 512:
        st.caption(f"Showing first 512 of {len(subs):,} subnets.")
