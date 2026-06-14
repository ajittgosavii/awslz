"""Scheduled drift collector — headless 'actual' snapshot writer.

Runs the read-only AWS Organizations scan, maps the estate onto a design,
scores it with the same Well-Architected engine the app uses, and writes an
**actual** snapshot to the shared SQLite store. Point a cron job / Windows Task
Scheduler / CI schedule at this and the app's Drift tab fills in automatically.

Credentials: pass --access-key/--secret-key, or set the standard AWS_* env vars,
or rely on an attached instance/role (the default boto3 chain — recommended).

The store location MUST match the app's: set LZ_DB_PATH to the same path the app
uses (or run from the same directory so both use ./lz_scenarios.db).

Examples:
    # Using an EC2 instance role, nightly, into the same DB the app reads:
    LZ_DB_PATH=/data/lz_scenarios.db python drift_collector.py --user operator --region us-east-1

    # Using explicit temporary credentials:
    python drift_collector.py --access-key AKIA... --secret-key ... --region us-east-1
"""

from __future__ import annotations

import argparse
import os
import sys

import live_aws
import store
import waf
from lz_core import score_design


def collect(user: str, label: str, region: str,
            access_key: str = "", secret_key: str = "", session_token: str = "") -> dict:
    """Run a scan and persist an 'actual' snapshot. Returns a small result dict."""
    scan = live_aws.scan_organization(access_key, secret_key, session_token or None, region)
    if not scan.get("ok"):
        return {"ok": False, "error": scan.get("error", "scan failed")}

    design = scan["mapped_design"]
    assessment = waf.assess(design)
    overall = waf.overall_score(assessment)
    scores = score_design(design)

    org_id = scan.get("org", {}).get("Id", "unknown")
    if not label or label == "auto":
        label = f"auto {org_id}"

    if not store.available():
        return {"ok": False, "error": f"store not writable at {os.environ.get('LZ_DB_PATH', './lz_scenarios.db')}"}

    store.save_snapshot(user, label, "actual", design.to_dict(), overall, scores)
    return {"ok": True, "user": user, "label": label, "org": org_id,
            "waf_overall": overall, "accounts": len(scan.get("accounts", [])),
            "control_tower": bool((scan.get("control_tower") or {}).get("confirmed"))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write an 'actual' drift snapshot from a live AWS scan.")
    ap.add_argument("--user", default=os.environ.get("LZ_USER", "operator"),
                    help="Snapshot owner (must match the app user). Default: operator.")
    ap.add_argument("--label", default="auto",
                    help="Snapshot label. Default 'auto' -> 'auto <org-id>'.")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"),
                    help="Region for API calls. Default: us-east-1.")
    ap.add_argument("--access-key", default=os.environ.get("AWS_ACCESS_KEY_ID", ""))
    ap.add_argument("--secret-key", default=os.environ.get("AWS_SECRET_ACCESS_KEY", ""))
    ap.add_argument("--session-token", default=os.environ.get("AWS_SESSION_TOKEN", ""))
    args = ap.parse_args(argv)

    res = collect(args.user, args.label, args.region,
                  args.access_key, args.secret_key, args.session_token)
    if not res.get("ok"):
        print(f"drift_collector: ERROR — {res.get('error')}", file=sys.stderr)
        return 1
    print(f"drift_collector: saved actual snapshot user={res['user']} label='{res['label']}' "
          f"org={res['org']} WAF={res['waf_overall']} accounts={res['accounts']} "
          f"control_tower={'yes' if res['control_tower'] else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
