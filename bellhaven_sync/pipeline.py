"""Daily pipeline: scrape -> fetch CRM -> match -> store proposals.

This never writes to the CRM. Writes only happen in the review app, after approval.

    python -m bellhaven_sync.pipeline run                 # normal daily run
    python -m bellhaven_sync.pipeline run --dry-run       # print proposals, store nothing
    python -m bellhaven_sync.pipeline run --locations-file tests/fixtures/locations.json
    python -m bellhaven_sync.pipeline status
"""
import argparse
import json
import sys
from collections import Counter

from . import config
from .matcher import KIND_LABELS, build_proposals
from .store import Store


def safety_guard(store, run_id, site_stats):
    """Decide whether 'no longer on the website' proposals are trustworthy this run."""
    reasons = []
    n = site_stats.get("locations", 0)
    if site_stats.get("parse_failures"):
        reasons.append(f"{len(site_stats['parse_failures'])} community pages failed to parse")
    claim = site_stats.get("directory_claim")
    in_dir = n - len(site_stats.get("not_in_directory", []))
    if claim and in_dir < claim:
        reasons.append(f"directory says {claim} communities but only {in_dir} were scraped from it")
    prev = store.last_ok_run(before_id=run_id) if store else None
    if prev and prev["stats"].get("locations"):
        if n < config.MIN_LOCATION_RATIO * prev["stats"]["locations"]:
            reasons.append(f"location count dropped from {prev['stats']['locations']} to {n}")
    return (not reasons), reasons


def run(store, crm, locations=None, site_stats=None, today=None, log=print):
    run_id = store.start_run()
    try:
        if locations is None:
            from .scraper import scrape
            locations, site_stats = scrape()
        site_stats = site_stats or {"locations": len(locations), "parse_failures": [],
                                    "directory_claim": None, "not_in_directory": []}
        if not locations:
            raise RuntimeError("Website returned no locations; aborting without proposing anything")
        accounts = crm.list_accounts()
        contacts = crm.list_contacts()
        if not accounts:
            raise RuntimeError("CRM returned no accounts; aborting")

        orphan_ok, guard_reasons = safety_guard(store, run_id, site_stats)
        if not orphan_ok:
            log("Safety guard: skipping 'no longer on website' proposals: " + "; ".join(guard_reasons))

        proposals, report = build_proposals(locations, accounts, contacts, today=today, orphan_check=orphan_ok)
        outcomes = Counter(store.upsert_proposal(run_id, p) for p in proposals)
        obsolete = store.obsolete_unseen(run_id)

        stats = {
            "locations": len(locations), "accounts": len(accounts), "contacts": len(contacts),
            "generated": len(proposals), "new": outcomes["new"], "kept_pending": outcomes["kept"],
            "suppressed_already_decided": outcomes["suppressed"], "reopened": outcomes["reopened"],
            "obsoleted": obsolete, "confirmed_no_change": len(report["confirmed"]),
            "orphan_check": orphan_ok, "guard_reasons": guard_reasons, "site": site_stats,
        }
        store.finish_run(run_id, True, stats, report)
        log(f"Run {run_id}: {len(locations)} locations, {len(accounts)} accounts -> "
            f"{len(proposals)} findings: {outcomes['new']} new, {outcomes['kept']} still pending, "
            f"{outcomes['suppressed']} already decided, {outcomes['reopened']} reopened, "
            f"{obsolete} obsolete; {len(report['confirmed'])} confirmed with no change.")
        return stats
    except Exception as e:
        store.finish_run(run_id, False, {"error": str(e)}, {})
        raise


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bellhaven_sync.pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="scrape, match and store proposals (never writes to the CRM)")
    r.add_argument("--dry-run", action="store_true", help="print proposals without storing them")
    r.add_argument("--locations-file", help="use a saved locations JSON instead of scraping")
    sub.add_parser("status", help="show proposal counts and recent runs")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        store = Store(config.DB_PATH)
        print("Proposals by status:", store.counts())
        for rr in store.runs(5):
            print(rr["id"], rr["started_at"], "ok" if rr["ok"] else "FAILED",
                  {k: rr["stats"].get(k) for k in ("generated", "new", "suppressed_already_decided", "obsoleted")})
        return 0

    if not config.TOKEN:
        print("Set BH_TOKEN first.", file=sys.stderr)
        return 2
    from .crm import CRMClient
    crm = CRMClient()
    locations = site_stats = None
    if args.locations_file:
        locations = json.load(open(args.locations_file))
    store = Store(":memory:" if args.dry_run else config.DB_PATH)
    run(store, crm, locations, site_stats)
    if args.dry_run:
        for p in store.list(["pending"]):
            print(f"[{KIND_LABELS[p['kind']]}] {p['title']}")
            for c in p["changes"]:
                print(f"    {c['record']}: {c['field']}  {c['current']!r} -> {c['proposed']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
