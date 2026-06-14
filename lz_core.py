"""Core domain logic for AWS Landing Zone Studio.

Account math, scoring model, cost estimation, and guardrail recommendations.
All cost figures are rough monthly estimates (USD) for demo/education purposes —
real costs depend on usage, region, and negotiated pricing.
"""

from dataclasses import dataclass, field, asdict

import pricing

# ---------------------------------------------------------------------------
# Design options
# ---------------------------------------------------------------------------

ORG_SIZES = ["Startup", "SMB", "Mid-market", "Enterprise"]

COMPLIANCE_FRAMEWORKS = [
    "PCI-DSS", "HIPAA", "SOC 2", "ISO 27001", "FedRAMP",
    "APRA CPS 234", "GDPR", "NIST 800-53", "HITRUST", "CIS AWS Foundations",
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


class _Dim:
    """Accumulates a dimension score while recording every contributing factor.

    This makes the scoring rubric fully auditable: ``factors`` is a list of
    (label, delta) so the UI / LLM / report can explain *why* a score is what it
    is, rather than presenting an opaque number.
    """

    def __init__(self, base_label: str, base: float):
        self.value = float(base)
        self.factors = [{"factor": base_label, "delta": round(float(base))}]

    def add(self, label: str, delta: float):
        if delta:
            self.value += delta
            self.factors.append({"factor": label, "delta": round(delta, 1)})
        return self

    def result(self):
        return _clamp(self.value), self.factors


HEAVY_COMPLIANCE = {"PCI-DSS", "HIPAA", "FedRAMP", "NIST 800-53", "APRA CPS 234"}


def _score_with_breakdown(d: LZDesign):
    """Compute all dimension scores AND a per-factor breakdown.

    Numerically identical to the previous opaque model — the only change is that
    every adjustment is now labelled and recorded.
    """
    n_acct = total_accounts(d)

    # --- Security & blast radius ---
    sec = _Dim(f"Account strategy: {d.account_strategy}", {
        "Single account": 15, "Account per environment": 55,
        "Account per workload": 70, "Account per workload per environment": 90,
    }[d.account_strategy])
    if d.security_tooling:
        sec.add("Org-wide security tooling (GuardDuty/Security Hub/Config)", 8)
    if d.centralized_logging:
        sec.add("Centralized logging (Log Archive)", 5)
    if d.identity_model == "IAM users per account":
        sec.add("IAM users (long-lived credentials)", -20)
    elif d.identity_model.startswith("External IdP"):
        sec.add("External IdP federation", 5)
    if d.governance == "None (single account, no org)":
        sec.add("No organization / governance", -25)

    # --- Scalability ---
    scal = _Dim(f"Account strategy: {d.account_strategy}", {
        "Single account": 10, "Account per environment": 45,
        "Account per workload": 75, "Account per workload per environment": 88,
    }[d.account_strategy])
    if d.network_pattern == "Flat VPC peering":
        scal.add(f"Flat peering mesh collapses with scale ({n_acct} accts)", -min(40, n_acct * 2))
    elif d.network_pattern == "AWS Cloud WAN":
        scal.add("Cloud WAN global segmentation", 10)
    elif d.network_pattern == "Centralized egress + TGW":
        scal.add("Centralized egress + TGW", 6)
    else:
        scal.add("Transit Gateway hub-and-spoke", 4)
    if d.governance in ("AWS Control Tower", "Landing Zone Accelerator (LZA)"):
        scal.add("Automated account vending", 8)

    # --- Operational simplicity (higher = easier to run) ---
    ops = _Dim(f"Fleet size penalty ({n_acct} accounts)", 95 - min(70, n_acct * 1.2))
    if d.governance == "AWS Control Tower":
        ops.add("Control Tower managed baselines", 22)
    elif d.governance == "Landing Zone Accelerator (LZA)":
        ops.add("LZA config-driven baselines", 15)
    elif d.governance == "Custom (Organizations + SCPs)":
        ops.add("Custom org (self-managed)", 5)
    if d.identity_model == "IAM users per account":
        ops.add("Per-account IAM user sprawl", -15)
    if len(d.regions) > 2:
        ops.add(f"Multi-region operations ({len(d.regions)} regions)", -(len(d.regions) - 2) * 4)
    if d.network_pattern == "Flat VPC peering" and n_acct > 8:
        ops.add("Peering mesh management overhead", -15)

    # --- Cost efficiency ---
    overhead = estimate_monthly_cost(d)["total"]
    workload_proxy = max(1, d.num_workloads) * 400  # assumed workload spend proxy
    ratio = overhead / (overhead + workload_proxy)
    cost_eff = _Dim(f"Platform overhead is ~{ratio:.0%} of modeled total spend", 100 - ratio * 160)
    if d.account_strategy == "Single account":
        cost_eff.add("Single account = least platform overhead", 10)

    # --- Compliance readiness ---
    comp = _Dim("Baseline", 30)
    if d.centralized_logging:
        comp.add("Centralized, immutable logging", 20)
    if d.security_tooling:
        comp.add("Org-wide threat detection", 15)
    if d.account_strategy != "Single account":
        comp.add("Account-level isolation", 15)
    if d.governance in ("AWS Control Tower", "Landing Zone Accelerator (LZA)"):
        comp.add("Managed controls / control catalog", 15)
    if d.identity_model != "IAM users per account":
        comp.add("Federated identity", 5)
    if HEAVY_COMPLIANCE & set(d.compliance):
        if d.account_strategy == "Single account":
            comp.add("Regulated workload in a single account", -30)
        if not d.centralized_logging:
            comp.add("Regulated workload without central logging", -15)
        if d.governance == "None (single account, no org)":
            comp.add("Regulated workload without an organization", -20)

    dims = {
        "Security & Blast Radius": sec, "Scalability": scal,
        "Operational Simplicity": ops, "Cost Efficiency": cost_eff,
        "Compliance Readiness": comp,
    }
    scores = {name: dim.result()[0] for name, dim in dims.items()}
    breakdown = {name: dim.result()[1] for name, dim in dims.items()}
    return scores, breakdown


def score_design(d: LZDesign) -> dict:
    """Return the 0-100 score per design dimension (see explain_scores for why)."""
    return _score_with_breakdown(d)[0]


def explain_scores(d: LZDesign) -> dict:
    """Return {dimension: [{factor, delta}, ...]} — the auditable rubric trace."""
    return _score_with_breakdown(d)[1]


# ---------------------------------------------------------------------------
# Cost model — derived from sourced list prices x stated usage assumptions.
#
# Every line item is computed as (published list price) x (assumed usage) so the
# number is traceable, not asserted. See pricing.py for the prices and their
# sources, and the "basis" returned below for the per-item derivation. Pass
# live_prices (from pricing.fetch_live_prices) to override the clean hourly SKUs
# with the caller's real, current regional prices.
# ---------------------------------------------------------------------------

# Back-compat re-export (moved to pricing.py).
REGION_PRICE_INDEX = pricing.REGION_PRICE_INDEX
HOURS_PER_MONTH = pricing.HOURS_PER_MONTH


def estimate_monthly_cost(d: LZDesign, live_prices: dict | None = None) -> dict:
    """Estimate monthly platform overhead with a traceable derivation.

    Returns {items, total, region_index, basis, price_source}. ``basis`` lists,
    per line item, the human-readable formula and the AWS price source so any
    figure can be checked and tuned.
    """
    lpx = live_prices or {}

    def price(key: str) -> float:
        return lpx.get(key, pricing.lp(key))

    def src(key: str) -> str:
        s = pricing.LIST_PRICES[key]["source"]
        return ("LIVE Price List API — " if key in lpx else "") + s

    n_acct = max(1, total_accounts(d))
    n_region = max(1, len(d.regions))
    n_wl = max(1, workload_account_count(d))
    hpm = pricing.HOURS_PER_MONTH

    items: dict[str, float] = {}
    basis: list[dict] = []

    def add(label: str, amount: float, formula: str, source: str):
        items[label] = items.get(label, 0.0) + amount
        basis.append({"item": label, "monthly": round(amount, 2),
                      "formula": formula, "source": source})

    # -- Security tooling (usage-derived per account x region) --
    if d.security_tooling:
        gd = (pricing.ua("guardduty_cloudtrail_events_million_per_acct") * price("guardduty_cloudtrail_million")
              + pricing.ua("guardduty_flowlog_gb_per_acct") * price("guardduty_flowlogs_gb"))
        add("GuardDuty (org-wide)", gd * n_acct * n_region,
            f"({pricing.ua('guardduty_cloudtrail_events_million_per_acct')}M evt x ${price('guardduty_cloudtrail_million')} "
            f"+ {pricing.ua('guardduty_flowlog_gb_per_acct')}GB x ${price('guardduty_flowlogs_gb')}) "
            f"x {n_acct} acct x {n_region} region",
            src("guardduty_cloudtrail_million"))
        sh = pricing.ua("securityhub_checks_per_acct") * price("securityhub_check_thousand")
        add("Security Hub", sh * n_acct * n_region,
            f"{pricing.ua('securityhub_checks_per_acct'):,} checks x ${price('securityhub_check_thousand')} "
            f"x {n_acct} acct x {n_region} region",
            src("securityhub_check_thousand"))

    cfg = (pricing.ua("config_items_per_acct") * price("config_item")
           + pricing.ua("config_rule_evals_per_acct") * price("config_rule_eval"))
    add("AWS Config (recorder + rules)", cfg * n_acct * n_region,
        f"({pricing.ua('config_items_per_acct'):,} items x ${price('config_item')} "
        f"+ {pricing.ua('config_rule_evals_per_acct'):,} evals x ${price('config_rule_eval')}) "
        f"x {n_acct} acct x {n_region} region",
        src("config_item"))

    if d.centralized_logging:
        ct = pricing.ua("cloudtrail_extra_events_100k_per_acct") * price("cloudtrail_extra_copy_100k") * n_acct
        s3 = pricing.ua("log_archive_gb") * price("s3_standard_gb")
        add("CloudTrail extra copies + S3 log archive", ct + s3,
            f"{pricing.ua('cloudtrail_extra_events_100k_per_acct')}x100k evt x ${price('cloudtrail_extra_copy_100k')} "
            f"x {n_acct} acct + {pricing.ua('log_archive_gb')}GB x ${price('s3_standard_gb')}",
            src("cloudtrail_extra_copy_100k"))

    # -- Networking (hourly SKUs x 730 + data processing) --
    nat_mo = price("nat_gateway_hour") * hpm
    if d.network_pattern in ("Transit Gateway hub-and-spoke", "Centralized egress + TGW"):
        vpcs = min(n_acct, n_wl + 2)
        add("Transit Gateway attachments", price("tgw_attachment_hour") * hpm * vpcs * n_region,
            f"${price('tgw_attachment_hour')}/hr x {hpm}h x {vpcs} VPC x {n_region} region",
            src("tgw_attachment_hour"))
        add("TGW data processing", pricing.ua("tgw_processed_gb_per_vpc") * price("tgw_data_gb") * vpcs,
            f"{pricing.ua('tgw_processed_gb_per_vpc')}GB x ${price('tgw_data_gb')} x {vpcs} VPC",
            src("tgw_data_gb"))
        n_nat = 2 if d.network_pattern == "Centralized egress + TGW" else min(vpcs, 6)
        nat_label = "Centralized NAT gateways" if n_nat == 2 else "Distributed NAT gateways"
        add(nat_label, nat_mo * n_nat * n_region,
            f"${price('nat_gateway_hour')}/hr x {hpm}h x {n_nat} GW x {n_region} region",
            src("nat_gateway_hour"))
        nat_gb = (pricing.ua("nat_processed_gb_per_vpc") * price("nat_gateway_gb")
                  * (n_nat if d.network_pattern == "Centralized egress + TGW" else vpcs) * n_region)
        add("NAT data processing", nat_gb,
            f"{pricing.ua('nat_processed_gb_per_vpc')}GB x ${price('nat_gateway_gb')} egress processed",
            src("nat_gateway_gb"))
    elif d.network_pattern == "AWS Cloud WAN":
        add("Cloud WAN core network edges", price("cloudwan_core_edge_hour") * hpm * n_region,
            f"${price('cloudwan_core_edge_hour')}/hr x {hpm}h x {n_region} region",
            src("cloudwan_core_edge_hour"))
        att = min(n_acct, n_wl + 2)
        add("Cloud WAN attachments", price("cloudwan_attachment_hour") * hpm * att,
            f"${price('cloudwan_attachment_hour')}/hr x {hpm}h x {att} attachment",
            src("cloudwan_attachment_hour"))
        add("Cloud WAN data processing", pricing.ua("tgw_processed_gb_per_vpc") * price("cloudwan_data_gb") * att,
            f"{pricing.ua('tgw_processed_gb_per_vpc')}GB x ${price('cloudwan_data_gb')} x {att}",
            src("cloudwan_data_gb"))
    else:  # flat peering
        n_nat = min(n_acct, 8)
        add("NAT gateways (per VPC)", nat_mo * n_nat * n_region,
            f"${price('nat_gateway_hour')}/hr x {hpm}h x {n_nat} GW x {n_region} region",
            src("nat_gateway_hour"))
        add("NAT data processing", pricing.ua("nat_processed_gb_per_vpc") * price("nat_gateway_gb") * n_nat * n_region,
            f"{pricing.ua('nat_processed_gb_per_vpc')}GB x ${price('nat_gateway_gb')} x {n_nat} VPC",
            src("nat_gateway_gb"))

    if d.backup_dr:
        store = pricing.ua("backup_gb_per_workload") * n_wl * price("backup_gb")
        add("AWS Backup (warm storage)", store,
            f"{pricing.ua('backup_gb_per_workload')}GB x {n_wl} workload x ${price('backup_gb')}",
            src("backup_gb"))
        if n_region > 1:
            xfer = pricing.ua("backup_gb_per_workload") * n_wl * price("interregion_transfer_gb") * (n_region - 1)
            add("Cross-region backup copy (transfer)", xfer,
                f"{pricing.ua('backup_gb_per_workload')}GB x {n_wl} x ${price('interregion_transfer_gb')} "
                f"x {n_region - 1} extra region",
                src("interregion_transfer_gb"))

    # -- Regional price index (applied to the whole estimate) --
    idx = pricing.region_index(d.regions)
    items = {k: v * idx for k, v in items.items()}
    for b in basis:
        b["monthly"] = round(b["monthly"] * idx, 2)

    total = round(sum(items.values()), 2)
    price_source = "live AWS Price List API (partial) + sourced list prices" if lpx else "sourced AWS list prices"
    return {
        "items": {k: round(v, 2) for k, v in items.items()},
        "total": total,
        "region_index": round(idx, 3),
        "basis": basis,
        "price_source": price_source,
    }


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
        ("Require EBS encryption by default", "Deny ec2:CreateVolume when Encrypted=false", "All OUs"),
        ("Deny disabling Access Analyzer", "Deny access-analyzer:Delete* / Update*", "All OUs"),
        ("Enforce S3 TLS-only access", "Deny s3:* when aws:SecureTransport=false", "All OUs"),
    ],
    "APRA CPS 234": [
        ("Data residency: restrict to AU regions", "Deny * outside ap-southeast-2/4", "All OUs"),
        ("Deny disabling CloudTrail / Config", "cloudtrail:StopLogging, config:Stop*/Delete*", "All OUs"),
        ("Require MFA for sensitive actions", "Deny when aws:MultiFactorAuthPresent=false", "Workloads OU"),
    ],
    "GDPR": [
        ("Data residency: restrict to EU regions", "Deny * outside eu-* regions", "Data OUs"),
        ("Deny public S3 buckets (personal data)", "s3:PutBucketPublicAccessBlock enforcement", "Data OUs"),
        ("Deny cross-region replication out of EU", "Deny s3:PutReplicationConfiguration to non-EU", "Data OUs"),
    ],
    "HITRUST": [
        ("Require encryption at rest (EBS/RDS/S3)", "Deny create when encryption disabled", "Workloads OU"),
        ("Enforce centralized audit logging", "Deny cloudtrail:StopLogging / DeleteTrail", "All OUs"),
        ("Restrict to approved, in-scope regions", "Deny * outside approved regions", "All OUs"),
        ("Deny disabling threat detection", "guardduty:Delete*/Disable*, securityhub:Disable*", "All OUs"),
    ],
    "CIS AWS Foundations": [
        ("Require IMDSv2 on instance launch", "ec2:RunInstances MetadataHttpTokens=required", "Workloads OU"),
        ("Deny S3 public access", "Deny disabling s3:PutBucketPublicAccessBlock / account block", "All OUs"),
        ("Deny security-group ingress 0.0.0.0/0 to 22/3389", "Deny ec2:AuthorizeSecurityGroupIngress on admin ports", "Workloads OU"),
        ("Deny disabling default EBS encryption", "Deny ec2:DisableEbsEncryptionByDefault", "All OUs"),
    ],
    "SOC 2": [
        ("Deny disabling GuardDuty", "guardduty:Delete*/Disassociate*/Disable*", "All OUs"),
        ("Deny disabling Security Hub", "securityhub:DisableSecurityHub / Disassociate*", "All OUs"),
        ("Deny CloudTrail / Config tampering", "cloudtrail:StopLogging, config:Stop*/Delete*", "All OUs"),
        ("Protect the log archive bucket", "Deny s3:Delete* / PutBucketPolicy on log archive", "Security OU"),
    ],
    "ISO 27001": [
        ("Deny security service tampering", "securityhub:Disable*, access-analyzer:Delete*", "All OUs"),
        ("Enforce encryption in transit", "Deny s3:* / es:* when aws:SecureTransport=false", "Workloads OU"),
        ("Deny disabling AWS Config", "config:DeleteConfigurationRecorder / Stop*", "All OUs"),
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
