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
    "organizations:ListParents",
    "organizations:ListPolicies", "organizations:ListPoliciesForTarget",
    "organizations:ListAwsServiceAccessForOrganization",
    "organizations:ListDelegatedAdministrators",
    "controltower:ListLandingZones", "controltower:GetLandingZone",
    "controltower:ListEnabledControls",
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

    # Confirm Control Tower via the real API (not inference) when permitted.
    result["control_tower"] = _detect_control_tower(session, cfg, result)

    # Map account_id -> parent OU id (for structural strategy inference).
    parents = {}
    try:
        for a in result["accounts"]:
            p = org.list_parents(ChildId=a["Id"]).get("Parents", [])
            if p:
                parents[a["Id"]] = p[0]["Id"]
    except Exception as e:
        result["warnings"].append(f"list_parents: {e}")
    result["account_parents"] = parents

    result.update(_derive_signals(result))
    result["ok"] = True
    return result


def _detect_control_tower(session, cfg, result: dict) -> dict:
    """Confirm Control Tower via the controltower API. Returns
    {confirmed, status?, version?, governed_regions?, drift?, error?}."""
    ct = {"confirmed": False}
    try:
        client = session.client("controltower", config=cfg)
    except Exception as e:  # service/region may be unavailable
        ct["error"] = f"controltower client unavailable: {e}"
        return ct
    try:
        lzs = client.list_landing_zones().get("landingZones", [])
        if not lzs:
            ct["error"] = "No Control Tower landing zone in this account/region."
            return ct
        arn = lzs[0].get("arn")
        ct["arn"] = arn
        detail = client.get_landing_zone(landingZoneIdentifier=arn).get("landingZone", {})
        manifest = detail.get("manifest", {}) or {}
        ct["confirmed"] = True
        ct["status"] = detail.get("status")
        ct["version"] = detail.get("version")
        ct["drift"] = detail.get("driftStatus", {}).get("status")
        gr = manifest.get("governedRegions") or []
        ct["governed_regions"] = gr if isinstance(gr, list) else []
    except Exception as e:  # AccessDenied / not enrolled / API not available
        ct["error"] = f"Control Tower API not accessible: {e}"
    return ct


_ENV_SUFFIXES = ("-prod", "-dev", "-test", "-stage", "-staging", "-qa", "-uat", "-nonprod",
                 "prod", "dev", "test", "staging")


def _infer_strategy(accounts: list, parents: dict) -> tuple[str, str]:
    """Infer the account strategy from structure, not just count.

    Returns (strategy, evidence). Looks for environment-suffixed account names
    (workload-per-env), distinct workload stems, and the account/OU spread.
    """
    workload_names = [a["Name"] for a in accounts
                      if a["Name"].lower() not in (
                          "management", "log archive", "log-archive", "audit",
                          "security-tooling", "network", "shared-services", "shared services")]
    n_wl = len(workload_names)
    if len(accounts) <= 1:
        return "Single account", "only one account in the organization"

    env_tagged = [w for w in workload_names
                  if any(w.lower().endswith(s) or f"-{s}" in w.lower() for s in _ENV_SUFFIXES)]
    # distinct workload stems (strip env suffix)
    stems = set()
    for w in workload_names:
        lw = w.lower()
        for s in sorted(_ENV_SUFFIXES, key=len, reverse=True):
            if lw.endswith(s) or lw.endswith("-" + s):
                lw = lw[: lw.rfind(s)].rstrip("-")
                break
        stems.add(lw)

    if env_tagged and len(env_tagged) >= max(2, n_wl // 2) and len(stems) >= 2:
        return ("Account per workload per environment",
                f"{len(env_tagged)} env-suffixed accounts across {len(stems)} workload(s)")
    if n_wl <= 1:
        return "Account per environment", "single workload stem / few accounts"
    if len(stems) >= 3:
        return "Account per workload", f"{len(stems)} distinct workload stems, no env split"
    if n_wl <= 4:
        return "Account per environment", f"{n_wl} workload accounts resemble per-env split"
    return "Account per workload", f"{n_wl} workload accounts (env split not evident)"


def _derive_signals(r: dict) -> dict:
    """Map the scanned estate onto an LZDesign + a confidence-rated signal table."""
    accounts = r.get("accounts", [])
    ous = r.get("ous", [])
    policies = r.get("policies", [])
    services = r.get("services", [])
    ct = r.get("control_tower", {}) or {}
    parents = r.get("account_parents", {})
    names = {a["Name"].lower() for a in accounts}
    ou_names = {o["Name"].lower() for o in ous}
    n = len(accounts)

    signals = []

    def sig(what, detected, detail, confidence):
        signals.append({"Signal": what, "Detected": "✅" if detected else "—",
                        "Confidence": confidence, "Detail": detail})

    has_log_archive = any(k in names for k in ("log archive", "log-archive", "logarchive", "logging"))
    has_audit = any(k in names for k in ("audit", "security-tooling", "security tooling", "security"))
    has_security_ou = "security" in ou_names
    ct_confirmed = bool(ct.get("confirmed"))
    ct_inferred = (not ct_confirmed) and (
        ("controltower.amazonaws.com" in services) or (has_security_ou and has_log_archive and has_audit))
    ct_present = ct_confirmed or ct_inferred
    sso_on = any("sso" in s for s in services)
    gd_on = any("guardduty" in s for s in services)
    sh_on = any("securityhub" in s for s in services)
    config_on = any("config" in s for s in services)
    ct_trail = any("cloudtrail" in s for s in services)
    custom_scps = [p for p in policies if not p["AwsManaged"]]

    # Confidence: "Confirmed" = authoritative API; "High"/"Medium" = inferred.
    sig("AWS Organizations (ALL features)", r["org"]["FeatureSet"] == "ALL",
        r["org"]["FeatureSet"], "Confirmed")
    if ct_confirmed:
        gr = ", ".join(ct.get("governed_regions", []) or []) or "n/a"
        sig("Control Tower landing zone", True,
            f"controltower API: status={ct.get('status')}, v{ct.get('version')}, "
            f"drift={ct.get('drift')}, governed regions=[{gr}]", "Confirmed")
    else:
        sig("Control Tower landing zone", ct_inferred,
            ct.get("error", "inferred from Security OU + Log Archive + Audit accounts"),
            "Medium (inferred)" if ct_inferred else "Confirmed-absent")
    sig("Log Archive account", has_log_archive, "account name match", "High")
    sig("Security tooling / Audit account", has_audit, "account name match", "High")
    sig("IAM Identity Center", sso_on, "sso.amazonaws.com trusted access", "Confirmed")
    sig("GuardDuty org integration", gd_on, "guardduty.amazonaws.com trusted access", "Confirmed")
    sig("Security Hub org integration", sh_on, "securityhub.amazonaws.com trusted access", "Confirmed")
    sig("AWS Config org integration", config_on, "config.amazonaws.com trusted access", "Confirmed")
    sig("Org CloudTrail", ct_trail, "cloudtrail.amazonaws.com trusted access", "Confirmed")
    sig(f"Custom SCPs ({len(custom_scps)})", len(custom_scps) > 0,
        ", ".join(p["Name"] for p in custom_scps[:6]) or "only FullAWSAccess", "Confirmed")
    sig(f"OU structure ({len(ous)} OUs)", len(ous) >= 3,
        ", ".join(sorted({o['Name'] for o in ous})[:8]), "Confirmed")

    strategy, strat_evidence = _infer_strategy(accounts, parents)
    sig(f"Account strategy: {strategy}", True, f"structural inference — {strat_evidence}",
        "Medium (inferred)")

    governance = ("AWS Control Tower" if ct_present
                  else "None (single account, no org)" if n <= 1
                  else "Custom (Organizations + SCPs)")

    governed_regions = ct.get("governed_regions") if ct_confirmed else None

    mapped = LZDesign(
        org_size="Enterprise" if n > 40 else "Mid-market" if n > 12 else "SMB" if n > 3 else "Startup",
        compliance=[],
        num_teams=max(1, n // 6),
        num_workloads=max(1, n - 5),
        environments=["dev", "test", "prod"],
        regions=governed_regions or ["us-east-1"],
        account_strategy=strategy,
        network_pattern="Transit Gateway hub-and-spoke",  # not detectable from Organizations alone
        identity_model="IAM Identity Center (SSO)" if sso_on else "IAM users per account",
        governance=governance,
        centralized_logging=has_log_archive and ct_trail,
        security_tooling=gd_on and config_on,
        backup_dr=any("backup" in s for s in services),
    )
    undetectable = ["network_pattern (assumed TGW)", "compliance frameworks",
                    "backup coverage detail"]
    if not governed_regions:
        undetectable.append("regions in active use")
    return {"signals": signals, "mapped_design": mapped,
            "control_tower": ct, "undetectable": undetectable}


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
