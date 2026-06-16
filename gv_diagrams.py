"""Graphviz diagram builders for AWS Landing Zone Studio."""

import graphviz
from lz_core import LZDesign, workload_account_count

# shared palette (this module's BLUE is navy; GREY is the cooler gv variant)
from colors import AMBER as AWS_ORANGE, DARK, NAVY as BLUE, GREEN, RED, GREY_GV as GREY


def org_structure_diagram(d: LZDesign) -> graphviz.Digraph:
    """Render the AWS Organizations OU / account tree for a design."""
    g = graphviz.Digraph("org")
    g.attr(rankdir="TB", bgcolor="transparent", pad="0.3")
    g.attr("node", shape="box", style="rounded,filled", fontname="Helvetica",
           fontsize="11", color="white", fontcolor="white")
    g.attr("edge", color=GREY, arrowsize="0.6")

    single = d.account_strategy == "Single account"
    no_org = d.governance == "None (single account, no org)"

    if single or no_org:
        g.node("acct", "Single AWS Account\n(everything together)", fillcolor=RED)
        g.node("warn", "No isolation:\nblast radius = 100%", shape="note", fillcolor=DARK)
        g.edge("acct", "warn", style="dashed")
        return g

    g.node("root", "AWS Organization Root\n(Management Account)", fillcolor=DARK)

    # Security OU
    g.node("sec_ou", "Security OU", fillcolor=BLUE)
    g.edge("root", "sec_ou")
    g.node("log", "Log Archive", fillcolor=GREEN)
    g.node("audit", "Security Tooling\n(Audit)", fillcolor=GREEN)
    g.edge("sec_ou", "log")
    g.edge("sec_ou", "audit")

    # Infrastructure OU
    g.node("infra_ou", "Infrastructure OU", fillcolor=BLUE)
    g.edge("root", "infra_ou")
    if d.network_pattern != "Flat VPC peering":
        net_label = {
            "Transit Gateway hub-and-spoke": "Network\n(Transit Gateway)",
            "Centralized egress + TGW": "Network\n(TGW + central egress)",
            "AWS Cloud WAN": "Network\n(Cloud WAN core)",
        }.get(d.network_pattern, "Network")
        g.node("net", net_label, fillcolor=GREEN)
        g.edge("infra_ou", "net")
    g.node("shared", "Shared Services\n(CI/CD, AMIs, ECR)", fillcolor=GREEN)
    g.edge("infra_ou", "shared")

    # Workloads OU
    g.node("wl_ou", "Workloads OU", fillcolor=BLUE)
    g.edge("root", "wl_ou")

    n_env = max(1, len(d.environments))
    if d.account_strategy == "Account per environment":
        for env in d.environments:
            g.node(f"env_{env}", f"{env.title()} Account\n(all workloads)", fillcolor=AWS_ORANGE, fontcolor=DARK)
            g.edge("wl_ou", f"env_{env}")
    elif d.account_strategy == "Account per workload":
        shown = min(d.num_workloads, 4)
        for i in range(shown):
            g.node(f"wl_{i}", f"Workload {i + 1}\n(all envs)", fillcolor=AWS_ORANGE, fontcolor=DARK)
            g.edge("wl_ou", f"wl_{i}")
        if d.num_workloads > shown:
            g.node("wl_more", f"… +{d.num_workloads - shown} more", fillcolor=GREY)
            g.edge("wl_ou", "wl_more")
    else:  # per workload per environment
        prod = [e for e in d.environments if e == "prod"]
        nonprod = [e for e in d.environments if e != "prod"]
        if prod:
            g.node("prod_ou", "Prod OU", fillcolor=BLUE)
            g.edge("wl_ou", "prod_ou")
            shown = min(d.num_workloads, 3)
            for i in range(shown):
                g.node(f"p_{i}", f"Workload {i + 1}\nprod", fillcolor=AWS_ORANGE, fontcolor=DARK)
                g.edge("prod_ou", f"p_{i}")
            if d.num_workloads > shown:
                g.node("p_more", f"… +{d.num_workloads - shown} more", fillcolor=GREY)
                g.edge("prod_ou", "p_more")
        if nonprod:
            g.node("np_ou", "Non-Prod OU", fillcolor=BLUE)
            g.edge("wl_ou", "np_ou")
            n_np = d.num_workloads * len(nonprod)
            g.node("np_accts", f"{n_np} accounts\n({d.num_workloads} workloads x {len(nonprod)} envs)",
                   fillcolor=AWS_ORANGE, fontcolor=DARK)
            g.edge("np_ou", "np_accts")

    # Sandbox + policy staging
    if d.org_size != "Startup":
        g.node("sb_ou", "Sandbox OU", fillcolor=BLUE)
        g.edge("root", "sb_ou")
        g.node("sb", "Sandbox accounts\n(experimentation,\nspend-capped)", fillcolor=GREY)
        g.edge("sb_ou", "sb")
    g.node("susp", "Suspended OU\n(deny-all SCP)", fillcolor=GREY)
    g.edge("root", "susp")

    return g


def network_diagram(d: LZDesign) -> graphviz.Digraph:
    """Render the network topology for a design."""
    g = graphviz.Digraph("net")
    g.attr(rankdir="LR", bgcolor="transparent", pad="0.3")
    g.attr("node", shape="box", style="rounded,filled", fontname="Helvetica",
           fontsize="11", color="white", fontcolor="white")
    g.attr("edge", color=GREY, arrowsize="0.6", fontname="Helvetica", fontsize="9", fontcolor=GREY)

    n_spokes = min(workload_account_count(d), 5)

    if d.network_pattern == "Flat VPC peering":
        nodes = [f"vpc{i}" for i in range(min(n_spokes + 1, 5))]
        for i, n in enumerate(nodes):
            g.node(n, f"VPC {i + 1}\n+ NAT GW", fillcolor=AWS_ORANGE, fontcolor=DARK)
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                g.edge(nodes[i], nodes[j], dir="none", label="peering")
        g.node("warn", f"Full mesh = n(n-1)/2 peerings\nNo transitive routing", shape="note", fillcolor=DARK)
        return g

    if d.network_pattern == "AWS Cloud WAN":
        g.node("cwan", "Cloud WAN\nCore Network", fillcolor=DARK)
        for r in d.regions[:3]:
            g.node(f"edge_{r}", f"Edge\n{r}", fillcolor=BLUE)
            g.edge("cwan", f"edge_{r}", dir="none")
        for i in range(n_spokes):
            g.node(f"s{i}", f"Workload VPC {i + 1}", fillcolor=AWS_ORANGE, fontcolor=DARK)
            tgt = f"edge_{d.regions[i % max(1, min(len(d.regions), 3))]}" if d.regions else "cwan"
            g.edge(f"s{i}", tgt, dir="none")
        return g

    # TGW patterns
    g.node("tgw", "Transit Gateway\n(Network account)", fillcolor=DARK)
    if d.network_pattern == "Centralized egress + TGW":
        g.node("egress", "Egress VPC\n(NAT + FW)", fillcolor=RED)
        g.edge("tgw", "egress", dir="none", label="0.0.0.0/0")
        g.node("inet", "Internet", shape="ellipse", fillcolor=GREY)
        g.edge("egress", "inet")
        g.node("insp", "Inspection VPC\n(optional NFW)", fillcolor=BLUE)
        g.edge("tgw", "insp", dir="none")
    for i in range(n_spokes):
        g.node(f"s{i}", f"Workload VPC {i + 1}", fillcolor=AWS_ORANGE, fontcolor=DARK)
        g.edge(f"s{i}", "tgw", dir="none", label="attachment")
    if workload_account_count(d) > n_spokes:
        g.node("more", f"… +{workload_account_count(d) - n_spokes} more VPCs", fillcolor=GREY)
        g.edge("more", "tgw", dir="none")
    return g
