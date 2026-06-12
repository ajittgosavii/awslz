"""Live AWS estate assessment — read-only AWS Organizations scan via boto3.

The user supplies temporary, READ-ONLY credentials. Only List*/Describe* calls
are made (Organizations, IAM Identity Center discovery). Nothing is written.
The scan maps the real estate onto an approximate LZDesign so the same WAF
assessment engine can score it, and reports the raw signals it used.
"""

import graphviz

from lz_core import LZDesign

READONLY_CALLS = [
    "organizations:DescribeOrganization", "organizations:ListRoots",
    "organizations:ListOrganizationalUnitsForParent", "organizations:ListAccounts",
    "organizations:ListPolicies", "organizations:ListAwsServiceAccessForOrganization",
    "organizations:ListDelegatedAdministrators",
]


def scan_organization(access_key: str, secret_key: str, session_token: str | None,
                      region: str) -> dict:
    """Scan the org. Returns {ok, error?, org, accounts, ous, policies, services,
    delegated, signals, mapped_design}."""
    import boto3
    from botocore.config import Config

    session = boto3.Session(
        aws_access_key_id=access_key.strip(),
        aws_secret_access_key=secret_key.strip(),
        aws_session_token=(session_token or "").strip() or None,
        region_name=region,
    )
    cfg = Config(retries={"max_attempts": 2}, connect_timeout=8, read_timeout=15)
    org = session.client("organizations", config=cfg)
    result = {"ok": False, "warnings": []}

    try:
        desc = org.describe_organization()["Organization"]
    except Exception as e:
        result["error"] = f"Cannot reach AWS Organizations: {e}"
        return result

    result["org"] = {
        "Id": desc.get("Id"), "FeatureSet": desc.get("FeatureSet"),
        "MasterAccountEmail": desc.get("MasterAccountEmail", ""),
    }

    # Accounts
    accounts = []
    try:
        paginator = org.get_paginator("list_accounts")
        for page in paginator.paginate():
            accounts.extend(page["Accounts"])
    except Exception as e:
        result["warnings"].append(f"list_accounts: {e}")
    result["accounts"] = [
        {"Id": a["Id"], "Name": a["Name"], "Status": a["Status"]} for a in accounts]

    # OU tree (depth-limited)
    ous = []
    try:
        roots = org.list_roots()["Roots"]
        stack = [(r["Id"], r["Name"], 0) for r in roots]
        while stack:
            parent_id, parent_name, depth = stack.pop()
            if depth >= 4:
                continue
            resp = org.list_organizational_units_for_parent(ParentId=parent_id)
            for ou in resp.get("OrganizationalUnits", []):
                ous.append({"Id": ou["Id"], "Name": ou["Name"], "Parent": parent_name})
                stack.append((ou["Id"], ou["Name"], depth + 1))
    except Exception as e:
        result["warnings"].append(f"OU walk: {e}")
    result["ous"] = ous

    # SCPs
    policies = []
    try:
        resp = org.list_policies(Filter="SERVICE_CONTROL_POLICY")
        policies = [{"Name": p["Name"], "AwsManaged": p["AwsManaged"]}
                    for p in resp.get("Policies", [])]
    except Exception as e:
        result["warnings"].append(f"list_policies: {e}")
    result["policies"] = policies

    # Trusted service access + delegated admins
    services, delegated = [], []
    try:
        resp = org.list_aws_service_access_for_organization()
        services = [s["ServicePrincipal"] for s in resp.get("EnabledServicePrincipals", [])]
    except Exception as e:
        result["warnings"].append(f"service access: {e}")
    try:
        resp = org.list_delegated_administrators()
        delegated = [a["Name"] for a in resp.get("DelegatedAdministrators", [])]
    except Exception as e:
        result["warnings"].append(f"delegated admins: {e}")
    result["services"] = services
    result["delegated"] = delegated

    result.update(_derive_signals(result))
    result["ok"] = True
    return result


def _derive_signals(r: dict) -> dict:
    """Heuristically map the scanned estate onto an LZDesign + signal table."""
    accounts = r.get("accounts", [])
    ous = r.get("ous", [])
    policies = r.get("policies", [])
    services = r.get("services", [])
    names = {a["Name"].lower() for a in accounts}
    ou_names = {o["Name"].lower() for o in ous}
    n = len(accounts)

    signals = []

    def sig(what, detected, detail):
        signals.append({"Signal": what, "Detected": "✅" if detected else "—", "Detail": detail})

    has_log_archive = any(k in names for k in ("log archive", "log-archive", "logarchive", "logging"))
    has_audit = any(k in names for k in ("audit", "security-tooling", "security tooling", "security"))
    has_security_ou = "security" in ou_names
    ct_likely = ("controltower.amazonaws.com" in services) or (
        has_security_ou and has_log_archive and has_audit)
    sso_on = any("sso" in s for s in services)
    gd_on = any("guardduty" in s for s in services)
    sh_on = any("securityhub" in s for s in services)
    config_on = any("config" in s for s in services)
    ct_trail = any("cloudtrail" in s for s in services)
    custom_scps = [p for p in policies if not p["AwsManaged"]]

    sig("AWS Organizations (ALL features)", r["org"]["FeatureSet"] == "ALL", r["org"]["FeatureSet"])
    sig("Control Tower footprint", ct_likely,
        "controltower service / Security OU + Log Archive + Audit accounts")
    sig("Log Archive account", has_log_archive, "account name match")
    sig("Security tooling / Audit account", has_audit, "account name match")
    sig("IAM Identity Center", sso_on, "sso.amazonaws.com trusted access")
    sig("GuardDuty org integration", gd_on, "guardduty.amazonaws.com trusted access")
    sig("Security Hub org integration", sh_on, "securityhub.amazonaws.com trusted access")
    sig("AWS Config org integration", config_on, "config.amazonaws.com trusted access")
    sig("Org CloudTrail", ct_trail, "cloudtrail.amazonaws.com trusted access")
    sig(f"Custom SCPs ({len(custom_scps)})", len(custom_scps) > 0,
        ", ".join(p["Name"] for p in custom_scps[:6]) or "only FullAWSAccess")
    sig(f"OU structure ({len(ous)} OUs)", len(ous) >= 3,
        ", ".join(sorted({o['Name'] for o in ous})[:8]))

    # Map to an approximate design
    if n <= 1:
        strategy = "Single account"
    elif n <= 5:
        strategy = "Account per environment"
    elif n <= 12:
        strategy = "Account per workload"
    else:
        strategy = "Account per workload per environment"

    governance = ("AWS Control Tower" if ct_likely
                  else "Custom (Organizations + SCPs)" if len(custom_scps) > 0 or len(ous) >= 2
                  else "None (single account, no org)" if n <= 1
                  else "Custom (Organizations + SCPs)")

    mapped = LZDesign(
        org_size="Enterprise" if n > 40 else "Mid-market" if n > 12 else "SMB" if n > 3 else "Startup",
        compliance=[],
        num_teams=max(1, n // 6),
        num_workloads=max(1, n - 5),
        environments=["dev", "test", "prod"],
        regions=["us-east-1"],
        account_strategy=strategy,
        network_pattern="Transit Gateway hub-and-spoke",  # not detectable from Organizations alone
        identity_model="IAM Identity Center (SSO)" if sso_on else "IAM users per account",
        governance=governance,
        centralized_logging=has_log_archive and ct_trail,
        security_tooling=gd_on and config_on,
        backup_dr=any("backup" in s for s in services),
    )
    return {"signals": signals, "mapped_design": mapped,
            "undetectable": ["network_pattern (assumed TGW)", "compliance frameworks",
                             "regions in active use", "backup coverage detail"]}


def estate_diagram(r: dict) -> graphviz.Digraph:
    g = graphviz.Digraph("estate")
    g.attr(rankdir="TB", bgcolor="transparent", pad="0.3")
    g.attr("node", shape="box", style="rounded,filled", fontname="Helvetica",
           fontsize="11", color="white", fontcolor="white")
    g.attr("edge", color="#5A6B86", arrowsize="0.6")

    g.node("root", f"Organization\n{r['org']['Id']}", fillcolor="#232F3E")
    by_parent = {}
    for ou in r.get("ous", []):
        by_parent.setdefault(ou["Parent"], []).append(ou["Name"])
    shown = 0
    for parent, children in by_parent.items():
        for name in children:
            if shown >= 14:
                break
            nid = f"ou_{name}_{shown}"
            g.node(nid, f"OU: {name}", fillcolor="#1A476F")
            g.edge("root", nid)
            shown += 1
    n_acct = len(r.get("accounts", []))
    g.node("accts", f"{n_acct} accounts", fillcolor="#FF9900", fontcolor="#232F3E")
    g.edge("root", "accts")
    return g
