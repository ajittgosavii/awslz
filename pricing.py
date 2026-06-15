"""Transparent, sourced pricing model for AWS Landing Zone Studio.

The goal of this module is to remove "hand-tuned magic numbers" from the cost
estimate. Every figure here is either:

  1. A **published AWS on-demand list price** (us-east-1, USD), with the source
     and the price-per-unit recorded inline — see ``LIST_PRICES``; or
  2. A **stated usage assumption** for usage-based services (GuardDuty, Security
     Hub, Config, CloudTrail), so the monthly figure is *derived* as
     ``list_price x assumed_usage`` rather than asserted — see ``USAGE``.

Both are surfaced to the user (and to the LLM / PDF) via the "cost basis" table
so any number can be traced back to a price and an assumption and adjusted.

Optionally, ``fetch_live_prices()`` queries the **AWS Price List Query API**
(read-only) for the clean hourly SKUs (NAT Gateway, Transit Gateway attachment,
Cloud WAN core edge) and overlays the real, current price for the caller's
region. It degrades gracefully to the published list prices if the API is
unavailable or the credentials lack ``pricing:GetProducts``.

List prices captured from the public AWS pricing pages and are indicative; AWS
changes prices and they vary by region. Treat the output as a planning estimate,
not a quote. Run ``fetch_live_prices`` (or AWS Pricing Calculator) for authority.
"""

from __future__ import annotations

HOURS_PER_MONTH = 730  # AWS billing convention

# ---------------------------------------------------------------------------
# Published list prices (us-east-1, on-demand, USD). Each carries its source.
# ---------------------------------------------------------------------------
# A price entry: {value, unit, monthly?, source, note}
LIST_PRICES = {
    "nat_gateway_hour": {
        "value": 0.045, "unit": "per NAT gateway-hour",
        "source": "Amazon VPC pricing (NAT gateway), us-east-1",
        "note": "Plus $0.045 per GB processed (see data-transfer assumptions).",
    },
    "nat_gateway_gb": {
        "value": 0.045, "unit": "per GB processed",
        "source": "Amazon VPC pricing (NAT gateway data processing), us-east-1",
        "note": "",
    },
    "tgw_attachment_hour": {
        "value": 0.05, "unit": "per TGW attachment-hour",
        "source": "AWS Transit Gateway pricing, us-east-1",
        "note": "Plus $0.02 per GB of data processed.",
    },
    "tgw_data_gb": {
        "value": 0.02, "unit": "per GB processed",
        "source": "AWS Transit Gateway pricing (data processing), us-east-1",
        "note": "",
    },
    "cloudwan_core_edge_hour": {
        "value": 0.50, "unit": "per Core Network Edge-hour (per region)",
        "source": "AWS Cloud WAN pricing, us-east-1",
        "note": "~$365/region/month before attachments and data processing.",
    },
    "cloudwan_attachment_hour": {
        "value": 0.05, "unit": "per attachment-hour",
        "source": "AWS Cloud WAN pricing (attachment), us-east-1",
        "note": "",
    },
    "cloudwan_data_gb": {
        "value": 0.02, "unit": "per GB processed",
        "source": "AWS Cloud WAN pricing (data processing), us-east-1",
        "note": "",
    },
    # Usage-based unit prices (the volume is supplied by USAGE assumptions).
    "guardduty_cloudtrail_million": {
        "value": 4.00, "unit": "per 1M CloudTrail mgmt events analyzed",
        "source": "Amazon GuardDuty pricing (first 500M events tier), us-east-1",
        "note": "",
    },
    "guardduty_flowlogs_gb": {
        "value": 1.00, "unit": "per GB VPC flow + DNS logs analyzed",
        "source": "Amazon GuardDuty pricing (first 500 GB tier), us-east-1",
        "note": "",
    },
    "securityhub_check_thousand": {
        "value": 0.0010, "unit": "per security check (shown per 1k)",
        "source": "AWS Security Hub pricing (first 100k checks tier), us-east-1",
        "note": "Plus $0.00003 per finding ingested.",
    },
    "config_item": {
        "value": 0.003, "unit": "per configuration item recorded",
        "source": "AWS Config pricing, us-east-1",
        "note": "",
    },
    "config_rule_eval": {
        "value": 0.001, "unit": "per rule evaluation (first 100k tier)",
        "source": "AWS Config pricing (conformance pack / rule evaluation), us-east-1",
        "note": "",
    },
    "cloudtrail_extra_copy_100k": {
        "value": 2.00, "unit": "per 100k mgmt events (additional copies)",
        "source": "AWS CloudTrail pricing, us-east-1",
        "note": "First copy of management events is free.",
    },
    "s3_standard_gb": {
        "value": 0.023, "unit": "per GB-month (S3 Standard)",
        "source": "Amazon S3 pricing (first 50 TB tier), us-east-1",
        "note": "Log archive storage.",
    },
    "backup_gb": {
        "value": 0.05, "unit": "per GB-month (warm backup storage)",
        "source": "AWS Backup pricing (EBS/EC2 warm storage), us-east-1",
        "note": "Cross-region copy adds inter-region transfer.",
    },
    "interregion_transfer_gb": {
        "value": 0.02, "unit": "per GB inter-region transfer",
        "source": "AWS data transfer pricing (US inter-region), us-east-1",
        "note": "Used for cross-region log/backup copies.",
    },
    # --- Hybrid connectivity (Direct Connect / SD-WAN) ---
    "dx_port_hour_1g": {
        "value": 0.30, "unit": "per 1 Gbps dedicated DX port-hour",
        "source": "AWS Direct Connect pricing (dedicated 1 Gbps), US",
        "note": "~$219/month per port before data transfer.",
    },
    "dx_port_hour_10g": {
        "value": 2.25, "unit": "per 10 Gbps dedicated DX port-hour",
        "source": "AWS Direct Connect pricing (dedicated 10 Gbps), US",
        "note": "~$1,642/month per port.",
    },
    "dx_port_hour_100g": {
        "value": 22.50, "unit": "per 100 Gbps dedicated DX port-hour",
        "source": "AWS Direct Connect pricing (dedicated 100 Gbps), US",
        "note": "~$16,425/month per port.",
    },
    "dx_data_transfer_out_gb": {
        "value": 0.02, "unit": "per GB DX data transfer out",
        "source": "AWS Direct Connect data transfer out (US), us-east-1",
        "note": "Inbound is free; rate varies by DX location.",
    },
    "sdwan_appliance_hour": {
        "value": 0.085, "unit": "per SD-WAN appliance-hour (c5.large EC2)",
        "source": "Amazon EC2 c5.large on-demand, us-east-1",
        "note": "Excludes third-party SD-WAN software license; HA pair = 2 appliances.",
    },
    # --- AWS MGN (Application Migration Service) ---
    "ebs_gp3_gb": {
        "value": 0.08, "unit": "per GB-month (gp3 staging volume)",
        "source": "Amazon EBS gp3 pricing, us-east-1",
        "note": "MGN staging-area replication volumes.",
    },
    "mgn_replication_server_hour": {
        "value": 0.0208, "unit": "per replication server-hour (t3.small)",
        "source": "Amazon EC2 t3.small on-demand, us-east-1",
        "note": "MGN staging replication servers; MGN service itself is free for 2160 hrs/server.",
    },
}

# Direct Connect speed -> list-price key
DX_SPEEDS = {"1 Gbps": "dx_port_hour_1g", "10 Gbps": "dx_port_hour_10g",
             "100 Gbps": "dx_port_hour_100g"}

# ---------------------------------------------------------------------------
# Stated usage assumptions (per account / per month) — these are the levers that
# turn usage-based unit prices into a monthly number. Surfaced and editable.
# ---------------------------------------------------------------------------
USAGE = {
    "guardduty_cloudtrail_events_million_per_acct": {
        "value": 4.0, "unit": "M CloudTrail mgmt events / account / month",
        "rationale": "~130k events/day for a moderately active account.",
    },
    "guardduty_flowlog_gb_per_acct": {
        "value": 15.0, "unit": "GB VPC flow + DNS logs / account / month",
        "rationale": "Small/medium workload account traffic sample.",
    },
    "securityhub_checks_per_acct": {
        "value": 9000, "unit": "security checks / account / month",
        "rationale": "Foundational + CIS standards across a baseline account.",
    },
    "config_items_per_acct": {
        "value": 4000, "unit": "configuration items / account / month",
        "rationale": "Recording supported resource types in a baseline account.",
    },
    "config_rule_evals_per_acct": {
        "value": 6000, "unit": "rule evaluations / account / month",
        "rationale": "~20 managed rules re-evaluated on change + periodic.",
    },
    "cloudtrail_extra_events_100k_per_acct": {
        "value": 1.3, "unit": "x100k additional-copy mgmt events / account / month",
        "rationale": "Second copy to the org trail / log archive.",
    },
    "log_archive_gb": {
        "value": 200.0, "unit": "GB resident log archive storage",
        "rationale": "Centralized CloudTrail/Config/flow logs (grows over time).",
    },
    "nat_processed_gb_per_vpc": {
        "value": 100.0, "unit": "GB egress processed / workload VPC / month",
        "rationale": "Outbound patching, API, and image-pull traffic.",
    },
    "tgw_processed_gb_per_vpc": {
        "value": 50.0, "unit": "GB east-west processed / VPC / month",
        "rationale": "Inter-account/service traffic across the TGW.",
    },
    "backup_gb_per_workload": {
        "value": 120.0, "unit": "GB backup storage / workload",
        "rationale": "Snapshots + AMIs warm tier per workload.",
    },
}

# Modeled total AWS spend for ONE workload across all its environments, per month.
# Used only as the denominator for the "platform overhead as a share of total
# spend" cost-efficiency ratio — it is a planning proxy, not a billed figure.
# A mid-market production workload (compute + storage + managed DB + supporting
# services, dev/test/prod) typically runs a few thousand USD/month; $2,500 is a
# deliberately conservative midpoint so the ratio is not skewed by an
# unrealistically small denominator.
WORKLOAD_SPEND_PROXY_PER_MONTH = 2500.0

# Relative price index vs us-east-1 (applied to the whole estimate). Indicative.
REGION_PRICE_INDEX = {
    "us-east-1": 1.00, "us-west-2": 1.00, "eu-west-1": 1.05, "eu-central-1": 1.09,
    "ap-southeast-1": 1.10, "ap-southeast-2": 1.12, "ap-south-1": 1.02, "sa-east-1": 1.35,
}


def lp(key: str) -> float:
    """List-price value lookup."""
    return LIST_PRICES[key]["value"]


def ua(key: str) -> float:
    """Usage-assumption value lookup."""
    return USAGE[key]["value"]


def region_index(regions: list) -> float:
    if not regions:
        return 1.0
    return sum(REGION_PRICE_INDEX.get(r, 1.05) for r in regions) / len(regions)


# ---------------------------------------------------------------------------
# Optional: live AWS Price List Query API overlay (read-only)
# ---------------------------------------------------------------------------

# The Price List Query API is only served from these regions.
_PRICING_ENDPOINT_REGION = "us-east-1"

# AWS "location" names keyed by region code (Price List API filters on location).
_REGION_LOCATION = {
    "us-east-1": "US East (N. Virginia)", "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)", "eu-central-1": "EU (Frankfurt)",
    "ap-southeast-1": "Asia Pacific (Singapore)", "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-south-1": "Asia Pacific (Mumbai)", "sa-east-1": "South America (Sao Paulo)",
}


def _first_on_demand_price(client, service_code: str, filters: list) -> float | None:
    """Return the first USD on-demand per-unit price for a product query."""
    import json as _json

    resp = client.get_products(ServiceCode=service_code, Filters=filters, MaxResults=1)
    for item in resp.get("PriceList", []):
        doc = _json.loads(item) if isinstance(item, str) else item
        terms = doc.get("terms", {}).get("OnDemand", {})
        for term in terms.values():
            for dim in term.get("priceDimensions", {}).values():
                usd = dim.get("pricePerUnit", {}).get("USD")
                if usd:
                    try:
                        return float(usd)
                    except ValueError:
                        return None
    return None


def fetch_live_prices(access_key: str, secret_key: str, session_token: str | None,
                      region: str) -> dict:
    """Best-effort live overlay of the clean hourly SKUs for ``region``.

    Returns {ok, region, prices: {key: value}, errors: [...]}. Never raises.
    Requires read-only ``pricing:GetProducts``.
    """
    out = {"ok": False, "region": region, "prices": {}, "errors": []}
    location = _REGION_LOCATION.get(region)
    if not location:
        out["errors"].append(f"No Price List location mapping for {region}.")
        return out
    try:
        import boto3
        from botocore.config import Config

        session = boto3.Session(
            aws_access_key_id=access_key.strip(),
            aws_secret_access_key=secret_key.strip(),
            aws_session_token=(session_token or "").strip() or None,
            region_name=_PRICING_ENDPOINT_REGION,
        )
        client = session.client(
            "pricing", config=Config(retries={"max_attempts": 2}, read_timeout=20))
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"pricing client init failed: {e}")
        return out

    def f(field, value):
        return {"Type": "TERM_MATCH", "Field": field, "Value": value}

    queries = {
        "nat_gateway_hour": ("AmazonVPC", [
            f("location", location), f("groupDescription", "Hourly charge for NAT Gateways")]),
        "tgw_attachment_hour": ("AmazonVPC", [
            f("location", location), f("operation", "TransitGatewayVPC"),
            f("usagetype", "TransitGateway-Hours")]),
    }
    for key, (svc, filters) in queries.items():
        try:
            price = _first_on_demand_price(client, svc, filters)
            if price is not None:
                out["prices"][key] = price
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{key}: {e}")

    out["ok"] = bool(out["prices"])
    return out
