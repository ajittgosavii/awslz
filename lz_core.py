"""Core domain logic for AWS Landing Zone Studio.

Account math, scoring model, cost estimation, and guardrail recommendations.
All cost figures are rough monthly estimates (USD) for demo/education purposes —
real costs depend on usage, region, and negotiated pricing.
"""

from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# Design options
# ---------------------------------------------------------------------------

ORG_SIZES = ["Startup", "SMB", "Mid-market", "Enterprise"]

COMPLIANCE_FRAMEWORKS = [
    "PCI-DSS", "HIPAA", "SOC 2", "ISO 27001", "FedRAMP",
    "APRA CPS 234", "GDPR", "NIST 800-53",
]

ACCOUNT_STRATEGIES = [
    "Single account",
    "Account per environment",
    "Account per workload",
    "Account per workload per environment",
]

NETWORK_PATTERNS = [
    "Flat VPC peering",
    "Transit Gateway hub-and-spoke",
    "Centralized egress + TGW",
    "AWS Cloud WAN",
]

IDENTITY_MODELS = [
    "IAM users per account",
    "IAM Identity Center (SSO)",
    "External IdP federation (Okta/Entra ID)",
]

GOVERNANCE_TOOLING = [
    "AWS Control Tower",
    "Landing Zone Accelerator (LZA)",
    "Custom (Organizations + SCPs)",
    "None (single account, no org)",
]

ENVIRONMENTS = ["dev", "test", "staging", "prod"]

REGIONS = [
    "us-east-1", "us-west-2", "eu-west-1", "eu-central-1",
    "ap-southeast-1", "ap-southeast-2", "ap-south-1", "sa-east-1",
]


@dataclass
class LZDesign:
    org_size: str = "Mid-market"
    compliance: list = field(default_factory=lambda: ["SOC 2"])
    num_teams: int = 5
    num_workloads: int = 8
    environments: list = field(default_factory=lambda: ["dev", "test", "prod"])
    regions: list = field(default_factory=lambda: ["us-east-1"])
    account_strategy: str = "Account per workload per environment"
    network_pattern: str = "Transit Gateway hub-and-spoke"
    identity_model: str = "IAM Identity Center (SSO)"
    governance: str = "AWS Control Tower"
    centralized_logging: bool = True
    security_tooling: bool = True  # GuardDuty, Security Hub, Config org-wide
    backup_dr: bool = False

    def to_dict(self):
        return asdict(self)


# ---------------------------------------------------------------------------
# Account math
# ---------------------------------------------------------------------------

CORE_ACCOUNTS = [
    ("Management", "Root / billing / org management — no workloads ever"),
    ("Log Archive", "Immutable, centralized CloudTrail / Config / VPC flow logs"),
    ("Security Tooling (Audit)", "GuardDuty / Security Hub / Detective delegated admin"),
    ("Network", "Transit Gateway, centralized egress, Route 53 Resolver"),
    ("Shared Services", "CI/CD, golden AMIs, container registries, directories"),
]


def workload_account_count(d: LZDesign) -> int:
    n_env = max(1, len(d.environments))
    if d.account_strategy == "Single account":
        return 1
    if d.account_strategy == "Account per environment":
        return n_env
    if d.account_strategy == "Account per workload":
        return d.num_workloads
    # Account per workload per environment
    return d.num_workloads * n_env


def core_account_count(d: LZDesign) -> int:
    if d.governance == "None (single account, no org)":
        return 0
    if d.account_strategy == "Single account":
        return 0
    n = 3  # Management, Log Archive, Security Tooling minimum
    if d.network_pattern != "Flat VPC peering":
        n += 1  # Network account
    if d.num_workloads >= 5 or d.org_size in ("Mid-market", "Enterprise"):
        n += 1  # Shared Services
    return n


def total_accounts(d: LZDesign) -> int:
    if d.account_strategy == "Single account" or d.governance == "None (single account, no org)":
        return 1
    return core_account_count(d) + workload_account_count(d) + (1 if d.org_size != "Startup" else 0)  # +1 sandbox


# ---------------------------------------------------------------------------
# Scoring model (0–100 per dimension)
# ---------------------------------------------------------------------------

def _clamp(x):
    return max(0, min(100, round(x)))


def score_design(d: LZDesign) -> dict:
    n_env = max(1, len(d.environments))
    n_acct = total_accounts(d)

    # --- Security & blast radius ---
    strategy_security = {
        "Single account": 15,
        "Account per environment": 55,
        "Account per workload": 70,
        "Account per workload per environment": 90,
    }[d.account_strategy]
    sec = strategy_security
    if d.security_tooling:
        sec += 8
    if d.centralized_logging:
        sec += 5
    if d.identity_model == "IAM users per account":
        sec -= 20
    elif d.identity_model.startswith("External IdP"):
        sec += 5
    if d.governance == "None (single account, no org)":
        sec -= 25

    # --- Scalability ---
    scal = {
        "Single account": 10,
        "Account per environment": 45,
        "Account per workload": 75,
        "Account per workload per environment": 88,
    }[d.account_strategy]
    if d.network_pattern == "Flat VPC peering":
        # peering meshes collapse beyond ~10 VPCs
        scal -= min(40, n_acct * 2)
    elif d.network_pattern == "AWS Cloud WAN":
        scal += 10
    elif d.network_pattern == "Centralized egress + TGW":
        scal += 6
    else:
        scal += 4
    if d.governance in ("AWS Control Tower", "Landing Zone Accelerator (LZA)"):
        scal += 8  # account vending automation

    # --- Operational simplicity (higher = easier to run) ---
    ops = 95 - min(70, n_acct * 1.2)
    if d.governance == "AWS Control Tower":
        ops += 22
    elif d.governance == "Landing Zone Accelerator (LZA)":
        ops += 15
    elif d.governance == "Custom (Organizations + SCPs)":
        ops += 5
    if d.identity_model == "IAM users per account":
        ops -= 15
    if len(d.regions) > 2:
        ops -= (len(d.regions) - 2) * 4
    if d.network_pattern == "Flat VPC peering" and n_acct > 8:
        ops -= 15

    # --- Cost efficiency ---
    cost_eff = 80
    overhead = estimate_monthly_cost(d)["total"]
    workload_proxy = max(1, d.num_workloads) * 400  # assumed workload spend proxy
    ratio = overhead / (overhead + workload_proxy)
    cost_eff = 100 - ratio * 160
    if d.account_strategy == "Single account":
        cost_eff += 10  # least platform overhead (but worst everything else)

    # --- Compliance readiness ---
    comp = 30
    if d.centralized_logging:
        comp += 20
    if d.security_tooling:
        comp += 15
    if d.account_strategy != "Single account":
        comp += 15
    if d.governance in ("AWS Control Tower", "Landing Zone Accelerator (LZA)"):
        comp += 15
    if d.identity_model != "IAM users per account":
        comp += 5
    heavy = {"PCI-DSS", "HIPAA", "FedRAMP", "NIST 800-53", "APRA CPS 234"}
    if heavy & set(d.compliance):
        # heavy frameworks penalize weak isolation hard
        if d.account_strategy == "Single account":
            comp -= 30
        if not d.centralized_logging:
            comp -= 15
        if d.governance == "None (single account, no org)":
            comp -= 20

    return {
        "Security & Blast Radius": _clamp(sec),
        "Scalability": _clamp(scal),
        "Operational Simplicity": _clamp(ops),
        "Cost Efficiency": _clamp(cost_eff),
        "Compliance Readiness": _clamp(comp),
    }


# ---------------------------------------------------------------------------
# Cost model (rough monthly USD estimates — demo purposes)
# ---------------------------------------------------------------------------

# Relative price index vs us-east-1 (rough, demo purposes)
REGION_PRICE_INDEX = {
    "us-east-1": 1.00, "us-west-2": 1.00, "eu-west-1": 1.05, "eu-central-1": 1.09,
    "ap-southeast-1": 1.10, "ap-southeast-2": 1.12, "ap-south-1": 1.02, "sa-east-1": 1.35,
}

UNIT_COSTS = {
    "guardduty_per_account": 25.0,
    "securityhub_per_account": 12.0,
    "config_per_account": 18.0,
    "cloudtrail_org_extra_copies": 2.0,   # first management-event copy free
    "tgw_attachment": 36.5,                # per VPC attachment per month
    "tgw_data_per_acct": 10.0,             # nominal data processing
    "nat_gateway": 32.4,                   # per NAT GW month (excl. data)
    "cloudwan_core_edge": 250.0,           # per region edge
    "identity_center": 0.0,
    "control_tower": 0.0,                  # CT itself free; underlying services billed
}


def estimate_monthly_cost(d: LZDesign) -> dict:
    n_acct = max(1, total_accounts(d))
    n_region = max(1, len(d.regions))
    items = {}

    if d.security_tooling:
        items["GuardDuty (org-wide)"] = UNIT_COSTS["guardduty_per_account"] * n_acct * n_region
        items["Security Hub"] = UNIT_COSTS["securityhub_per_account"] * n_acct * n_region
    items["AWS Config (rules + recorder)"] = UNIT_COSTS["config_per_account"] * n_acct * n_region
    if d.centralized_logging:
        items["CloudTrail extra copies + S3 log archive"] = 40 + UNIT_COSTS["cloudtrail_org_extra_copies"] * n_acct

    # Networking
    if d.network_pattern in ("Transit Gateway hub-and-spoke", "Centralized egress + TGW"):
        vpcs = min(n_acct, workload_account_count(d) + 2)
        items["Transit Gateway attachments"] = UNIT_COSTS["tgw_attachment"] * vpcs * n_region
        items["TGW data processing (nominal)"] = UNIT_COSTS["tgw_data_per_acct"] * vpcs
        if d.network_pattern == "Centralized egress + TGW":
            items["Centralized NAT gateways"] = UNIT_COSTS["nat_gateway"] * 2 * n_region
        else:
            items["Distributed NAT gateways"] = UNIT_COSTS["nat_gateway"] * min(vpcs, 6) * n_region
    elif d.network_pattern == "AWS Cloud WAN":
        items["Cloud WAN core network edges"] = UNIT_COSTS["cloudwan_core_edge"] * n_region
        items["Cloud WAN attachments"] = 36.5 * min(n_acct, workload_account_count(d) + 2)
    else:  # flat peering
        items["NAT gateways (per VPC)"] = UNIT_COSTS["nat_gateway"] * min(n_acct, 8) * n_region
        items["VPC peering data (nominal)"] = 15.0 * min(n_acct, 10)

    if d.backup_dr:
        items["AWS Backup + cross-region copies (nominal)"] = 60.0 * n_region

    # Region price index: scale by the average index of selected regions
    idx = sum(REGION_PRICE_INDEX.get(r, 1.05) for r in d.regions) / n_region
    items = {k: v * idx for k, v in items.items()}

    total = round(sum(items.values()), 2)
    return {"items": {k: round(v, 2) for k, v in items.items()}, "total": total,
            "region_index": round(idx, 3)}


# ---------------------------------------------------------------------------
# Guardrails / SCP recommendations
# ---------------------------------------------------------------------------

BASE_SCPS = [
    ("Deny leaving the organization", "organizations:LeaveOrganization", "All OUs"),
    ("Deny root user actions", "Deny * when aws:PrincipalArn = root", "All workload OUs"),
    ("Region restriction", "Deny * outside approved regions", "All OUs"),
    ("Deny CloudTrail tampering", "cloudtrail:StopLogging / DeleteTrail", "All OUs"),
    ("Deny Config tampering", "config:Delete* / Stop*", "All OUs"),
    ("Protect Log Archive bucket", "Deny s3:Delete* on log archive", "Security OU"),
]

COMPLIANCE_SCPS = {
    "PCI-DSS": [
        ("Require encryption in transit", "Deny s3:* when aws:SecureTransport=false", "Workloads OU"),
        ("Deny public S3 buckets", "s3:PutBucketPublicAccessBlock enforcement", "Workloads OU"),
    ],
    "HIPAA": [
        ("Deny unencrypted EBS volumes", "ec2:CreateVolume when Encrypted=false", "Workloads OU"),
        ("Deny non-HIPAA-eligible services", "Allow-list of eligible services", "PHI OU"),
    ],
    "FedRAMP": [
        ("Restrict to GovCloud / approved regions", "Region condition", "All OUs"),
        ("Deny non-FIPS endpoints (where applicable)", "Endpoint policy controls", "All OUs"),
    ],
    "NIST 800-53": [
        ("Deny IMDSv1 instance launches", "ec2:RunInstances MetadataHttpTokens=required", "Workloads OU"),
    ],
    "APRA CPS 234": [
        ("Data residency: restrict to AU regions", "Deny * outside ap-southeast-2/4", "All OUs"),
    ],
    "GDPR": [
        ("Data residency: restrict to EU regions", "Deny * outside eu-* regions", "Data OUs"),
    ],
    "HITRUST": [],
    "SOC 2": [
        ("Deny disabling GuardDuty", "guardduty:Delete*/Disassociate*", "All OUs"),
    ],
    "ISO 27001": [
        ("Deny security service tampering", "securityhub:Disable*, access-analyzer:Delete*", "All OUs"),
    ],
}


def recommend_guardrails(d: LZDesign) -> list:
    rows = list(BASE_SCPS)
    for fw in d.compliance:
        rows.extend(COMPLIANCE_SCPS.get(fw, []))
    if d.org_size == "Enterprise":
        rows.append(("Deny VPC creation outside Network account", "ec2:CreateVpc deny in workload OUs", "Workloads OU"))
    # dedupe preserving order
    seen, out = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Rule-based pattern recommendation (the "suggestion" engine baseline)
# ---------------------------------------------------------------------------

def recommend_design(org_size, compliance, num_workloads, num_teams, regions) -> LZDesign:
    heavy = {"PCI-DSS", "HIPAA", "FedRAMP", "NIST 800-53", "APRA CPS 234"}
    is_regulated = bool(heavy & set(compliance))

    if org_size == "Startup" and not is_regulated and num_workloads <= 3:
        strategy = "Account per environment"
        network = "Flat VPC peering" if num_workloads <= 2 else "Transit Gateway hub-and-spoke"
        governance = "AWS Control Tower"
    elif org_size in ("Startup", "SMB"):
        strategy = "Account per workload" if num_workloads <= 6 else "Account per workload per environment"
        network = "Transit Gateway hub-and-spoke"
        governance = "AWS Control Tower"
    elif org_size == "Mid-market":
        strategy = "Account per workload per environment"
        network = "Centralized egress + TGW"
        governance = "AWS Control Tower"
    else:  # Enterprise
        strategy = "Account per workload per environment"
        network = "AWS Cloud WAN" if len(regions) >= 3 else "Centralized egress + TGW"
        governance = "Landing Zone Accelerator (LZA)" if is_regulated else "AWS Control Tower"

    return LZDesign(
        org_size=org_size,
        compliance=list(compliance),
        num_teams=num_teams,
        num_workloads=num_workloads,
        environments=["dev", "test", "prod"] if org_size != "Enterprise" else ["dev", "test", "staging", "prod"],
        regions=list(regions) or ["us-east-1"],
        account_strategy=strategy,
        network_pattern=network,
        identity_model="External IdP federation (Okta/Entra ID)" if org_size == "Enterprise" else "IAM Identity Center (SSO)",
        governance=governance,
        centralized_logging=True,
        security_tooling=True,
        backup_dr=is_regulated or org_size == "Enterprise",
    )
