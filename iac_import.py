"""Reverse mode — ingest real IaC and assess it.

The inverse of `iac.py`: take an uploaded Terraform (`.tf`), Landing Zone
Accelerator config (`.yaml`), or CloudFormation (`.yaml`/`.json`) file, infer an
approximate ``LZDesign`` from what it declares, and run the same Well-Architected
assessment used for designs. Inspired by the AWS Well-Architected IaC Analyzer,
but local, deterministic, and transparent about confidence.

Parsing is intentionally lightweight and tolerant (regex for HCL, a YAML/JSON
load for the rest) — it surfaces *signals* with a confidence rating rather than
pretending to be a full HCL engine, and reports what it could not detect.
"""

from __future__ import annotations

import json
import re

from lz_core import LZDesign
from live_aws import _infer_strategy  # reuse structural account-strategy inference

_SERVICE_HINTS = {
    "guardduty": "guardduty.amazonaws.com",
    "securityhub": "securityhub.amazonaws.com",
    "config": "config.amazonaws.com",
    "cloudtrail": "cloudtrail.amazonaws.com",
    "sso": "sso.amazonaws.com",
    "backup": "backup.amazonaws.com",
}

_CORE_NAMES = ("management", "log archive", "log-archive", "logarchive", "audit",
               "security-tooling", "security tooling", "network", "shared-services",
               "shared services")


def _sig(signals, what, detected, detail, confidence):
    signals.append({"Signal": what, "Detected": "✅" if detected else "—",
                    "Confidence": confidence, "Detail": detail})


def detect_format(filename: str, text: str) -> str:
    name = (filename or "").lower()
    head = text.lstrip()[:400].lower()
    if name.endswith(".tf") or "resource \"aws_organizations" in text or "aws_organizations_" in text:
        return "terraform"
    if "awstemplateformatversion" in head or '"awstemplateformatversion"' in head:
        return "cloudformation"
    if name.endswith((".yaml", ".yml")) or "organizationalunits:" in text.lower() or "serviceControlPolicies".lower() in text.lower():
        return "lza"
    if name.endswith(".json"):
        return "cloudformation"
    return "terraform"


def parse_iac(filename: str, text: str) -> dict:
    """Dispatch on detected format. Returns a result dict like live_aws.scan_organization."""
    fmt = detect_format(filename, text)
    try:
        if fmt == "terraform":
            res = _parse_terraform(text)
        elif fmt == "lza":
            res = _parse_lza(text)
        else:
            res = _parse_cloudformation(text)
        res["ok"] = True
        res["format"] = fmt
        return res
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "format": fmt, "error": f"Could not parse {fmt}: {e}"}


# ---------------------------------------------------------------------------
# Terraform — robust HCL parse via python-hcl2, with a regex fallback.
#
# Both paths produce the same normalized `norm` dict, which a single builder
# turns into signals + a mapped design. python-hcl2 handles real-world
# formatting (comments, nested blocks, expressions) far better than regex; the
# regex path is a dependency-free safety net.
# ---------------------------------------------------------------------------

def _unq(s) -> str:
    """Normalize an hcl2 token: strip ${...} interpolation wrappers and any
    surrounding double quotes. python-hcl2 < 5 strips quotes itself; >= 5 keeps
    them on keys and values — this makes the parser tolerant of both."""
    s = str(s)
    if s.startswith("${") and s.endswith("}"):
        s = s[2:-1]
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s


def _strip_interp(s: str) -> str:
    return _unq(s)


def _as_str_list(v) -> list:
    if isinstance(v, list):
        out = [_unq(x) for x in v]
        return [x for x in out if "${" not in x and "each." not in x]
    if isinstance(v, str):
        return re.findall(r'"([^"]+)"', v)
    return []


def _empty_norm() -> dict:
    return dict(account_names=[], ou_names=[], scp_names=[], wl_from_local=[],
                envs_from_local=[], per_env=False, per_wl=False, per_env_only=False,
                region_list=[], feature_all=False, principals=set())


def _norm_from_hcl2(text: str) -> dict:
    import hcl2  # raises ImportError if not installed
    try:
        parsed = hcl2.loads(text)
    except AttributeError:  # older API
        import io
        parsed = hcl2.load(io.StringIO(text))

    resources = parsed.get("resource", []) or []

    def iter_res(rtype):
        for block in resources:
            if not isinstance(block, dict):
                continue
            for rtype_key, body in block.items():
                if _unq(rtype_key) != rtype or not isinstance(body, dict):
                    continue
                for label_key, attrs in body.items():
                    yield _unq(label_key), (attrs or {})

    n = _empty_norm()
    for label, attrs in iter_res("aws_organizations_account"):
        fe = attrs.get("for_each")
        if fe is not None:
            s = str(fe)
            if "setproduct" in s:
                n["per_env"] = True
            elif "local.workloads" in s:
                n["per_wl"] = True
            elif "local.environments" in s:
                n["per_env_only"] = True
            continue  # generated accounts are materialized from locals below
        nm = _unq(attrs.get("name", "")) if attrs.get("name") is not None else ""
        if nm and "${" not in nm and "each." not in nm:
            n["account_names"].append(nm)
        elif "${" not in label:
            n["account_names"].append(label.replace("_", "-"))

    for _label, attrs in iter_res("aws_organizations_organizational_unit"):
        nm = attrs.get("name")
        if nm is not None:
            n["ou_names"].append(_unq(nm))

    for label, attrs in iter_res("aws_organizations_policy"):
        nm = attrs.get("name")
        n["scp_names"].append(_unq(nm) if nm is not None else label)

    for _label, attrs in iter_res("aws_organizations_organization"):
        if _unq(attrs.get("feature_set", "")) == "ALL":
            n["feature_all"] = True
        sp = attrs.get("aws_service_access_principals")
        if isinstance(sp, list):
            n["principals"] |= {_unq(x) for x in sp}

    locals_map = {}
    for block in parsed.get("locals", []) or []:
        if isinstance(block, dict):
            for k, v in block.items():
                locals_map[_unq(k)] = v
    n["wl_from_local"] = _as_str_list(locals_map.get("workloads"))
    n["envs_from_local"] = _as_str_list(locals_map.get("environments"))
    n["region_list"] = _as_str_list(locals_map.get("approved_regions"))

    # Supplement structural flags with reliable substring hints — the for_each
    # may reference an intermediate local (e.g. setproduct lives in a separate
    # locals block, not on the account resource).
    n["per_env"] = n["per_env"] or "setproduct(local.workloads" in text
    n["per_wl"] = n["per_wl"] or "toset(local.workloads)" in text
    n["per_env_only"] = n["per_env_only"] or "toset(local.environments)" in text
    return n


def _norm_from_regex(text: str) -> dict:
    n = _empty_norm()
    acct_blocks = re.findall(r'resource\s+"aws_organizations_account"\s+"([^"]+)"\s*{([^}]*)}',
                             text, re.DOTALL)
    for label, body in acct_blocks:
        if "for_each" in body or "each." in body:
            continue
        m = re.search(r'name\s*=\s*"([^"$]+)"', body)
        if m:
            n["account_names"].append(m.group(1))
        elif "${" not in label:
            n["account_names"].append(label.replace("_", "-"))

    n["ou_names"] = re.findall(
        r'resource\s+"aws_organizations_organizational_unit"\s+"[^"]+"\s*{[^}]*?name\s*=\s*"([^"]+)"',
        text, re.DOTALL)
    n["scp_names"] = re.findall(r'resource\s+"aws_organizations_policy"\s+"([^"]+)"', text)

    workloads_local = re.search(r'workloads\s*=\s*\[([^\]]*)\]', text)
    envs_local = re.search(r'environments\s*=\s*\[([^\]]*)\]', text)
    n["wl_from_local"] = re.findall(r'"([^"]+)"', workloads_local.group(1)) if workloads_local else []
    n["envs_from_local"] = re.findall(r'"([^"]+)"', envs_local.group(1)) if envs_local else []
    n["per_env"] = "setproduct(local.workloads" in text
    n["per_wl"] = "toset(local.workloads)" in text
    n["per_env_only"] = "toset(local.environments)" in text
    n["feature_all"] = bool(re.search(r'feature_set\s*=\s*"ALL"', text))
    regions = re.findall(r'approved_regions\s*=\s*\[([^\]]*)\]', text)
    n["region_list"] = re.findall(r'"([^"]+)"', regions[0]) if regions else []
    return n


def _parse_terraform(text: str) -> dict:
    parser = "python-hcl2"
    try:
        norm = _norm_from_hcl2(text)
    except Exception:  # ImportError or any parse error -> regex safety net
        norm = _norm_from_regex(text)
        parser = "regex"
    return _build_tf_result(norm, text, parser)


def _build_tf_result(norm: dict, text: str, parser: str) -> dict:
    signals, warnings = [], []

    # Materialize workload accounts for the declared for_each pattern only.
    synth_accounts = [{"Name": n, "Id": str(i)} for i, n in enumerate(norm["account_names"])]
    base = len(synth_accounts)
    if norm["per_env"] and norm["wl_from_local"] and norm["envs_from_local"]:
        for w in norm["wl_from_local"]:
            for e in norm["envs_from_local"]:
                synth_accounts.append({"Name": f"{w}-{e}", "Id": str(base)}); base += 1
    elif norm["per_wl"] and norm["wl_from_local"]:
        for w in norm["wl_from_local"]:
            synth_accounts.append({"Name": w, "Id": str(base)}); base += 1
    elif norm["per_env_only"] and norm["envs_from_local"]:
        for e in norm["envs_from_local"]:
            synth_accounts.append({"Name": f"workloads-{e}", "Id": str(base)}); base += 1

    # service detection: precise (hcl2 principals) ∪ textual fallback
    principals = norm.get("principals", set())
    svc = {k: (hint in principals) or (hint in text) for k, hint in _SERVICE_HINTS.items()}

    guardrail_hits = []
    for key, needle in (("Region restriction", "RequestedRegion"),
                        ("Deny leave org", "LeaveOrganization"),
                        ("Protect security services", "StopLogging"),
                        ("Require IMDSv2", "MetadataHttpTokens"),
                        ("Deny root user", "iam::*:root"),
                        ("Suspended deny-all", "DenyAll")):
        if needle.lower() in text.lower():
            guardrail_hits.append(key)

    has_iam_users = bool(re.search(r'resource\s+"aws_iam_user"', text))
    has_ct = bool(re.search(r'resource\s+"aws_controltower', text)) or "controltower" in text.lower()

    _sig(signals, "Parser", True, f"parsed with {parser}",
         "Confirmed" if parser == "python-hcl2" else "High")
    _sig(signals, "AWS Organizations (ALL features)", norm["feature_all"],
         "feature_set = ALL" if norm["feature_all"] else "feature_set not ALL / not found", "High")
    _sig(signals, f"OU structure ({len(norm['ou_names'])} OUs)", len(norm["ou_names"]) >= 3,
         ", ".join(norm["ou_names"][:8]) or "none parsed", "High")
    _sig(signals, f"Accounts declared ({len(synth_accounts)})", len(synth_accounts) > 1,
         ", ".join(a["Name"] for a in synth_accounts[:6]) or "none", "Medium")
    for label, key in (("GuardDuty trusted access", "guardduty"),
                       ("Security Hub trusted access", "securityhub"),
                       ("AWS Config trusted access", "config"),
                       ("Org CloudTrail trusted access", "cloudtrail"),
                       ("IAM Identity Center", "sso"),
                       ("AWS Backup trusted access", "backup")):
        _sig(signals, label, svc[key], f"{_SERVICE_HINTS[key]} present" if svc[key] else "not referenced", "High")
    _sig(signals, f"Service Control Policies ({len(norm['scp_names'])})", len(norm["scp_names"]) > 0,
         ", ".join(guardrail_hits[:6]) or (", ".join(norm["scp_names"][:4]) or "none"), "High")
    _sig(signals, "IAM users present (anti-pattern)", has_iam_users,
         "aws_iam_user resource found" if has_iam_users else "none", "High")

    strategy, strat_evidence = _infer_strategy(synth_accounts, {})
    _sig(signals, f"Account strategy: {strategy}", True, f"structural inference — {strat_evidence}",
         "Medium (inferred)")

    has_log_archive = any("log" in a["Name"].lower() and "arch" in a["Name"].lower()
                          or a["Name"].lower() in ("log-archive", "logarchive") for a in synth_accounts)
    governance = ("AWS Control Tower" if has_ct
                  else "Landing Zone Accelerator (LZA)" if "lza" in text.lower()
                  else "Custom (Organizations + SCPs)" if (norm["scp_names"] or norm["ou_names"])
                  else "None (single account, no org)")

    mapped = LZDesign(
        org_size="Enterprise" if len(synth_accounts) > 40 else "Mid-market" if len(synth_accounts) > 12
        else "SMB" if len(synth_accounts) > 3 else "Startup",
        compliance=[],
        num_teams=max(1, len(synth_accounts) // 6),
        num_workloads=max(1, len(norm["wl_from_local"]) or max(1, len(synth_accounts) - 5)),
        environments=norm["envs_from_local"] or ["dev", "test", "prod"],
        regions=norm["region_list"] or ["us-east-1"],
        account_strategy=strategy,
        network_pattern="Transit Gateway hub-and-spoke",  # not reliably detectable from org TF
        identity_model="IAM users per account" if has_iam_users and not svc["sso"]
        else "IAM Identity Center (SSO)",
        governance=governance,
        centralized_logging=has_log_archive and svc["cloudtrail"],
        security_tooling=svc["guardduty"] and svc["config"],
        backup_dr=svc["backup"],
    )
    return {"signals": signals, "mapped_design": mapped, "warnings": warnings,
            "guardrails": guardrail_hits, "parser": parser,
            "undetectable": ["network pattern", "compliance frameworks", "right-sizing"]}


# ---------------------------------------------------------------------------
# Landing Zone Accelerator config (YAML)
# ---------------------------------------------------------------------------

def _load_yaml(text: str):
    import yaml  # PyYAML ships with Streamlit's deps
    return yaml.safe_load(text)


def _parse_lza(text: str) -> dict:
    signals, warnings = [], []
    ou_names = re.findall(r'-\s*name:\s*([^\n#]+)', text)
    ous = [o.strip() for o in ou_names]
    scps = re.findall(r'policy:\s*([^\n#]+)', text)
    enabled_regions = re.search(r'enabledRegions:\s*\[([^\]]*)\]', text) or re.search(
        r'enabledRegions:\s*\n((?:\s*-\s*[^\n]+\n?)+)', text)
    regions = []
    if enabled_regions:
        regions = re.findall(r'[\w-]+-\d', enabled_regions.group(1))
    workload_accounts = re.findall(r'workloadAccounts:(.*?)(?:\n\w|\Z)', text, re.DOTALL)
    wl_count = len(re.findall(r'-\s*name:', workload_accounts[0])) if workload_accounts else 0
    per_env = bool(re.search(r'organizationalUnit:\s*Workloads/(Prod|NonProd)', text))

    _sig(signals, "LZA organization config", True, f"{len(ous)} OU entries parsed", "High")
    _sig(signals, f"Service Control Policies ({len(scps)})", len(scps) > 0,
         ", ".join(s.strip() for s in scps[:5]) or "none", "High")
    _sig(signals, f"Workload accounts ({wl_count})", wl_count > 0,
         "per-env split" if per_env else "flat", "Medium")
    _sig(signals, f"Enabled regions ({len(regions)})", len(regions) > 0,
         ", ".join(regions[:6]) or "default", "High")

    strategy = "Account per workload per environment" if per_env else (
        "Account per workload" if wl_count > 1 else "Account per environment")
    _sig(signals, f"Account strategy: {strategy}", True, "from LZA accounts config", "Medium (inferred)")

    mapped = LZDesign(
        org_size="Enterprise" if wl_count > 25 else "Mid-market" if wl_count > 8 else "SMB",
        compliance=[], num_teams=max(1, wl_count // 4), num_workloads=max(1, wl_count or 5),
        environments=["dev", "test", "staging", "prod"] if per_env else ["dev", "test", "prod"],
        regions=regions or ["us-east-1"],
        account_strategy=strategy,
        network_pattern="Centralized egress + TGW",
        identity_model="IAM Identity Center (SSO)",
        governance="Landing Zone Accelerator (LZA)",
        centralized_logging=True, security_tooling=True, backup_dr="backupPolicies" in text)
    return {"signals": signals, "mapped_design": mapped, "warnings": warnings,
            "guardrails": [s.strip() for s in scps], "undetectable": ["network detail", "compliance frameworks"]}


# ---------------------------------------------------------------------------
# CloudFormation (YAML/JSON) — light pass for Organizations resources
# ---------------------------------------------------------------------------

def _parse_cloudformation(text: str) -> dict:
    signals, warnings = [], []
    try:
        doc = json.loads(text)
    except Exception:
        doc = _load_yaml(text)
    resources = (doc or {}).get("Resources", {}) if isinstance(doc, dict) else {}
    types = [r.get("Type", "") for r in resources.values() if isinstance(r, dict)]
    n_ou = sum(t == "AWS::Organizations::OrganizationalUnit" for t in types)
    n_acct = sum(t == "AWS::Organizations::Account" for t in types)
    n_scp = sum(t == "AWS::Organizations::Policy" for t in types)
    has_org = any(t == "AWS::Organizations::Organization" for t in types)

    acct_names = []
    for r in resources.values():
        if isinstance(r, dict) and r.get("Type") == "AWS::Organizations::Account":
            nm = r.get("Properties", {}).get("AccountName") or r.get("Properties", {}).get("Email", "")
            if nm:
                acct_names.append(str(nm))

    _sig(signals, "Organizations resources", has_org or n_acct > 0,
         f"{n_acct} accounts, {n_ou} OUs, {n_scp} SCPs", "High")
    synth = [{"Name": n, "Id": str(i)} for i, n in enumerate(acct_names)]
    strategy, ev = _infer_strategy(synth or [{"Name": "a", "Id": "0"}], {})
    _sig(signals, f"Account strategy: {strategy}", True, ev, "Low (CFN naming)")

    mapped = LZDesign(
        org_size="Mid-market" if n_acct > 12 else "SMB" if n_acct > 3 else "Startup",
        compliance=[], num_teams=max(1, n_acct // 6), num_workloads=max(1, n_acct - 5 if n_acct > 5 else n_acct),
        environments=["dev", "test", "prod"], regions=["us-east-1"],
        account_strategy=strategy, network_pattern="Transit Gateway hub-and-spoke",
        identity_model="IAM Identity Center (SSO)",
        governance="Custom (Organizations + SCPs)" if (n_scp or n_ou) else "None (single account, no org)",
        centralized_logging=any("log" in n.lower() for n in acct_names),
        security_tooling=False, backup_dr=False)
    return {"signals": signals, "mapped_design": mapped, "warnings": warnings,
            "guardrails": [], "undetectable": ["trusted services", "network", "compliance",
                                               "guardrail contents (CFN policy bodies not deep-parsed)"]}
