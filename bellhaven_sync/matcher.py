"""Match website locations to CRM accounts and turn findings into proposals.

A proposal is a self-contained, reviewable unit:
  kind, subject, title, summary   what and why, in plain language
  changes                         the before/after diff shown to the reviewer
  evidence                        website record, CRM records, score breakdown, billing check
  warnings                        things the reviewer should look at twice
  ops                             ordered API operations the applier runs on approval

Op types
  patch_account   {account_id, fields, expect, append_note}
  create_account  {ref, fields}            (the new id is stored under ref)
  patch_contact   {contact_id, fields, expect}
Field values of the form "$ref:<name>" are resolved at apply time.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date

from . import config
from .normalize import (is_po_box, money, name_similarity, norm_street, phone_digits,
                        ratio, squash, street_number, to_crm_care_types, zip5)

FRIENDLY = {"name": "name", "billing_street": "street", "billing_city": "city", "billing_state": "state",
            "billing_zip": "ZIP", "care_type": "care type", "status": "status"}

KIND_LABELS = {
    "update": "Update details",
    "reparent": "Move under Bellhaven",
    "chow_new_account": "Change of ownership: new account",
    "chow_link_existing": "Change of ownership: link to new owner",
    "create": "New location",
    "duplicate": "Duplicate record",
    "orphan_inactive": "No longer on website",
    "orphan_duplicate": "Taken over by another operator",
    "orphan_needs_review": "No longer on website, has open AR",
    "needs_review": "Possible match, needs a human check",
}


# ---------------------------------------------------------------- helpers
def fingerprint(kind, subject, ops):
    """Stable identity of a finding: excludes notes, expectations and evidence."""
    canon = []
    for op in ops:
        o = {k: v for k, v in op.items() if k not in ("append_note", "expect")}
        if "fields" in o:
            o["fields"] = {k: v for k, v in o["fields"].items() if k != "note"}
        canon.append(o)
    raw = json.dumps([kind, subject, canon], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def usd(v):
    return f"${money(v):,.0f}"


def is_parent_account(a):
    return "(parent account)" in (a.get("name") or "").lower()


def is_resolved(a):
    """Already handled historically: duplicates and CHOW'd accounts are frozen."""
    return bool(a.get("duplicate_of_account") or a.get("chow_current_account"))


def has_billing(a):
    return money(a.get("lifetime_revenue")) > 0 or money(a.get("outstanding_ar")) > 0


def chow_required(a):
    """The SOP: revenue history AND outstanding AR > 0 -> keep the old account."""
    return money(a.get("lifetime_revenue")) > 0 and money(a.get("outstanding_ar")) > 0


def find_parent_id(accounts, override=""):
    if override:
        return override
    for a in accounts:
        if squash(a.get("name")).lower() == config.PARENT_NAME.lower():
            return a["account_id"]
    for a in accounts:
        n = (a.get("name") or "").lower()
        if "bellhaven" in n and "parent account" in n:
            return a["account_id"]
    raise ValueError("Bellhaven parent account not found; set BH_PARENT_ID")


def acct_evidence(a, contacts_by, parent_names):
    return {
        "account_id": a["account_id"],
        "name": a.get("name"),
        "parent": parent_names.get(a.get("parent_id")) or a.get("parent_name") or "(none)",
        "parent_id": a.get("parent_id") or "",
        "street": a.get("billing_street"), "city": a.get("billing_city"),
        "state": a.get("billing_state"), "zip": a.get("billing_zip"),
        "care_type": a.get("care_type"), "status": a.get("status"), "phone": a.get("phone"),
        "lifetime_revenue": money(a.get("lifetime_revenue")),
        "outstanding_ar": money(a.get("outstanding_ar")),
        "note": a.get("note") or "",
        "contacts": [{"name": c.get("name"), "title": c.get("title"), "email": c.get("email")}
                     for c in contacts_by.get(a["account_id"], [])],
    }


def loc_evidence(loc):
    keys = ("name", "street", "city", "state", "zip", "care_offerings", "administrator",
            "phone", "url", "in_directory", "linked_from")
    return {k: loc.get(k) for k in keys}


def billing_check(a, moving):
    rev, ar = money(a.get("lifetime_revenue")), money(a.get("outstanding_ar"))
    if not moving:
        outcome = "Parent is not changing, so the change-of-ownership rule does not apply."
    elif rev > 0 and ar > 0:
        outcome = ("Revenue history and open AR: keep this account exactly as it is, create a new "
                   "account under the correct parent, and point chow_current_account at it.")
    elif rev > 0:
        outcome = "Revenue history but no open AR: re-parent the existing account directly."
    elif ar > 0:
        outcome = "Open AR but no revenue history: re-parent the existing account directly."
    else:
        outcome = "No billing history: re-parent the existing account directly."
    return {"lifetime_revenue": rev, "outstanding_ar": ar, "parent_change": moving,
            "chow_required": moving and rev > 0 and ar > 0, "outcome": outcome}


# ---------------------------------------------------------------- scoring
def score(loc, acc, contacts):
    """Points for each independent signal that this account is this facility."""
    br = []
    ls, as_ = norm_street(loc.get("street")), norm_street(acc.get("billing_street"))
    same_zip = zip5(loc.get("zip")) and zip5(loc.get("zip")) == zip5(acc.get("billing_zip"))
    same_state = (loc.get("state") or "").upper() == (acc.get("billing_state") or "").upper()
    same_city = same_state and (loc.get("city") or "").lower() == (acc.get("billing_city") or "").strip().lower()

    if ls and ls == as_ and (same_zip or same_city):
        br.append(("address", 60, f"Same street address ({acc.get('billing_street')})"))
    elif (street_number(ls) and street_number(ls) == street_number(as_)
          and (same_zip or same_city) and ratio(ls, as_) >= 0.8):
        br.append(("address", 50, f"Same street number, near-identical street ({acc.get('billing_street')})"))
    if same_zip:
        br.append(("zip", 5, "Same ZIP"))
    if same_city:
        br.append(("city", 10, "Same city and state"))
    if phone_digits(loc.get("phone")) and phone_digits(loc.get("phone")) == phone_digits(acc.get("phone")):
        br.append(("phone", 20, "Same phone number"))
    admin = squash(loc.get("administrator")).lower()
    if admin and any(squash(c.get("name")).lower() == admin for c in contacts):
        br.append(("administrator", 20, f"Website administrator {loc.get('administrator')} is a contact on this account"))
    if same_state:
        sim = name_similarity(loc.get("name"), acc.get("name"))
        pts = round(20 * sim)
        if pts:
            br.append(("name", pts, f"Name similarity {sim:.0%}"))
    return sum(p for _, p, _ in br), br


# ---------------------------------------------------------------- builders
class Builder:
    def __init__(self, locations, accounts, contacts, today=None, orphan_check=True,
                 parent_override=""):
        self.today = today or date.today().isoformat()
        self.locations = sorted(locations, key=lambda l: l["slug"])
        self.accounts = accounts
        self.by_id = {a["account_id"]: a for a in accounts}
        self.pid = find_parent_id(accounts, parent_override or config.PARENT_ID_OVERRIDE)
        self.parent_names = {a["account_id"]: a["name"] for a in accounts if is_parent_account(a)}
        self.contacts_by = defaultdict(list)
        for c in contacts:
            if c.get("is_active", True):
                self.contacts_by[c.get("account_id")].append(c)
        self.pool = [a for a in accounts if not is_parent_account(a) and not is_resolved(a)]
        self.orphan_check = orphan_check
        self.proposals, self.confirmed, self.matched = [], [], set()

    # -- small utilities
    def pname(self, pid):
        return self.parent_names.get(pid, "(no parent)") if pid else "(no parent)"

    def ev(self, a):
        return acct_evidence(a, self.contacts_by, self.parent_names)

    def note(self, text):
        return f"{config.NOTE_TAG} {self.today}: {text}"

    def add(self, kind, subject, title, summary, ops, changes, evidence, warnings=None):
        self.proposals.append({
            "kind": kind, "subject": subject, "title": title, "summary": summary,
            "ops": ops, "changes": changes, "evidence": evidence,
            "warnings": warnings or [], "fingerprint": fingerprint(kind, subject, ops),
        })

    def new_account_fields(self, loc):
        types = to_crm_care_types(loc.get("care_offerings"))
        return {
            "name": loc["name"], "parent_id": self.pid,
            "billing_street": loc["street"], "billing_city": loc["city"],
            "billing_state": loc["state"], "billing_zip": loc["zip"],
            "care_type": types[0] if types else "", "phone": loc.get("phone") or "",
            "status": "Active",
        }

    # -- main entry
    def build(self):
        scored = {}
        for loc in self.locations:
            for a in self.pool:
                scored[(loc["slug"], a["account_id"])] = score(loc, a, self.contacts_by.get(a["account_id"], []))

        # Each account belongs to at most one location: its best-scoring one.
        best = {}
        for (slug, aid), (total, _) in scored.items():
            if total >= config.REVIEW_THRESHOLD and (aid not in best or total > best[aid][1]):
                best[aid] = (slug, total)

        for loc in self.locations:
            slug = loc["slug"]
            members = [(self.by_id[aid], scored[(slug, aid)]) for aid, (s, _) in best.items() if s == slug]
            cluster = [m for m in members if m[1][0] >= config.MATCH_THRESHOLD]
            grey = [m for m in members if m[1][0] < config.MATCH_THRESHOLD]
            if cluster:
                self.matched.update(a["account_id"] for a, _ in cluster)
                self.handle_cluster(loc, cluster)
            elif grey:
                self.matched.update(a["account_id"] for a, _ in grey)
                self.handle_grey(loc, grey)
            else:
                self.handle_new(loc, scored)

        if self.orphan_check:
            for a in self.pool:
                if (a.get("parent_id") == self.pid and a["account_id"] not in self.matched
                        and a.get("status") not in ("Inactive", "Needs Review")):
                    self.handle_orphan(a)

        report = {
            "parent_id": self.pid,
            "confirmed": self.confirmed,
            "counts": {k: sum(1 for p in self.proposals if p["kind"] == k) for k in KIND_LABELS},
            "orphan_check": self.orphan_check,
        }
        return self.proposals, report

    # -- survivor selection
    def choose_survivor(self, loc, cluster):
        def raw_addr_equal(a):
            return squash(a.get("billing_street")).lower() == squash(loc.get("street")).lower()

        def key(item):
            a, (total, br) = item
            signals = {s for s, _, _ in br}
            hits = int("phone" in signals) + int("administrator" in signals) + int(raw_addr_equal(a))
            return (a.get("parent_id") == self.pid, a.get("status") == "Active", has_billing(a),
                    hits, not a.get("parent_id"), len(self.contacts_by.get(a["account_id"], [])), total)

        ordered = sorted(sorted(cluster, key=lambda i: i[0]["account_id"]), key=key, reverse=True)
        surv = ordered[0]
        reasons = []
        k = key(surv)
        if k[0]:
            reasons.append("is already under the Bellhaven parent")
        if k[2]:
            reasons.append("carries the billing history")
        if k[3]:
            reasons.append(f"matches the website on {k[3]} of 3 exact signals (address text, phone, administrator)")
        if k[4] and not k[0]:
            reasons.append("has no other operator as parent, so no other company's record is taken over")
        if not reasons:
            reasons.append("wins the tie-break on lowest account id")
        return surv, ordered[1:], reasons

    # -- field-level differences between a matched account and its location
    def fixes(self, acc, loc):
        fx, why, warn = {}, {}, []
        if squash(acc.get("name")) != squash(loc.get("name")):
            fx["name"] = loc["name"]
            why["name"] = "Website uses this name"
        if loc.get("street") and (is_po_box(acc.get("billing_street"))
                                  or norm_street(acc.get("billing_street")) != norm_street(loc["street"])):
            fx["billing_street"] = loc["street"]
            why["billing_street"] = "Website lists a different street address"
            if is_po_box(acc.get("billing_street")):
                warn.append("The current billing street is a PO Box. It may be an intentional billing "
                            "mailing address; approving replaces it with the facility's street address.")
        if loc.get("city") and (acc.get("billing_city") or "").strip().lower() != loc["city"].lower():
            fx["billing_city"] = loc["city"]
            why["billing_city"] = "Website lists a different city"
        if loc.get("state") and (acc.get("billing_state") or "").upper() != loc["state"].upper():
            fx["billing_state"] = loc["state"]
            why["billing_state"] = "Website lists a different state"
        if loc.get("zip") and zip5(acc.get("billing_zip")) != zip5(loc["zip"]):
            fx["billing_zip"] = loc["zip"]
            why["billing_zip"] = "Website lists a different ZIP (street and city agree)"
        types = to_crm_care_types(loc.get("care_offerings"))
        if types and acc.get("care_type") not in types:
            fx["care_type"] = types[0]
            why["care_type"] = f"Website offers {', '.join(loc['care_offerings'])}"
        if acc.get("status") != "Active":
            fx["status"] = "Active"
            why["status"] = "Facility is listed on the website"
        return fx, why, warn

    def diff_rows(self, acc, fields, why=None, record=None):
        why = why or {}
        rows = []
        for f, v in fields.items():
            cur = acc.get(f, "") if acc else ""
            if f == "parent_id":
                cur = self.pname(cur) if acc else ""
                v = self.pname(v)
            rows.append({"record": record or (acc or {}).get("name", "New account"),
                         "account_id": (acc or {}).get("account_id"), "field": f,
                         "current": cur, "proposed": v, "why": why.get(f, "")})
        return rows

    # -- a website location with one or more matching accounts
    def handle_cluster(self, loc, cluster):
        (surv, (total, br)), losers, reasons = self.choose_survivor(loc, cluster)
        fx, why, warn = self.fixes(surv, loc)
        moving = surv.get("parent_id") != self.pid
        evidence = {
            "website": loc_evidence(loc),
            "crm": [self.ev(surv)],
            "score": {"total": total, "signals": br},
            "billing": billing_check(surv, moving),
        }
        if len(cluster) > 1:
            evidence["survivor_reason"] = reasons

        if moving and chow_required(surv):
            new_fields = self.new_account_fields(loc)
            new_fields["note"] = self.note(
                f"Created as change-of-ownership successor of {surv['account_id']} ({surv['name']}), "
                f"which stays under {self.pname(surv.get('parent_id'))} for billing "
                f"(lifetime revenue {usd(surv.get('lifetime_revenue'))}, open AR {usd(surv.get('outstanding_ar'))}).")
            ops = [
                {"op": "create_account", "ref": "new", "fields": new_fields},
                {"op": "patch_account", "account_id": surv["account_id"],
                 "fields": {"chow_current_account": "$ref:new"},
                 "expect": {"chow_current_account": surv.get("chow_current_account") or "",
                            "parent_id": surv.get("parent_id") or ""}},
            ]
            changes = self.diff_rows(None, {k: v for k, v in new_fields.items() if k != "note"},
                                     record="New account")
            changes += self.diff_rows(surv, {"chow_current_account": "(id of the new account)"},
                                      {"chow_current_account": "Points billing to the successor"})
            self.add("chow_new_account", surv["account_id"],
                     f"{loc['name']}: change of ownership",
                     f"{surv['name']} sits under {self.pname(surv.get('parent_id'))} but the website shows it is "
                     f"a Bellhaven community. It has revenue history and open AR, so the old account is kept "
                     f"untouched and a new account is created under Bellhaven.",
                     ops, changes, evidence, warn)
        elif moving:
            fields = {"parent_id": self.pid, **fx}
            ops = [{"op": "patch_account", "account_id": surv["account_id"], "fields": fields,
                    "expect": {k: surv.get(k) or "" for k in fields},
                    "append_note": f"Moved from {self.pname(surv.get('parent_id'))} to Bellhaven: listed on "
                                   f"the Bellhaven website as {loc['name']} ({loc['url']})."
                                   + (f" Also updated: {', '.join(fx)}." if fx else "")}]
            self.add("reparent", surv["account_id"],
                     f"{loc['name']}: move under Bellhaven",
                     f"{surv['name']} " + (f"is under {self.pname(surv.get('parent_id'))}" if surv.get("parent_id")
                                            else "has no parent account")
                     + f" but appears on the Bellhaven website. {billing_check(surv, True)['outcome']}",
                     ops, self.diff_rows(surv, fields, {"parent_id": "Listed on the Bellhaven website", **why}),
                     evidence, warn)
        elif fx:
            ops = [{"op": "patch_account", "account_id": surv["account_id"], "fields": fx,
                    "expect": {k: surv.get(k) or "" for k in fx},
                    "append_note": f"Updated {', '.join(fx)} to match the Bellhaven website ({loc['url']})."}]
            self.add("update", surv["account_id"], f"{loc['name']}: update {', '.join(FRIENDLY.get(f, f) for f in fx)}",
                     f"{surv['name']} is the right account but some details differ from the website.",
                     ops, self.diff_rows(surv, fx, why), evidence, warn)
        else:
            self.confirmed.append({"location": loc["name"], "account_id": surv["account_id"],
                                   "account": surv["name"], "score": total,
                                   "signals": [s for s, _, _ in br]})

        for loser, (l_total, l_br) in losers:
            self.handle_duplicate(loc, surv, loser, l_total, l_br, reasons)

    def handle_duplicate(self, loc, surv, loser, total, br, reasons):
        if chow_required(loser) and loser.get("parent_id") != surv.get("parent_id"):
            # Same facility, but this record carries revenue history AND open AR under another parent.
            # Billing needs it preserved, so the SOP applies: leave it exactly as it is and point
            # chow_current_account at the surviving record instead of inactivating it.
            fields = {"chow_current_account": surv["account_id"]}
            ops = [{"op": "patch_account", "account_id": loser["account_id"], "fields": fields,
                    "expect": {"chow_current_account": loser.get("chow_current_account") or "",
                               "parent_id": loser.get("parent_id") or ""}}]
            self.add("chow_link_existing", loser["account_id"], f"{loc['name']}: change of ownership",
                     f"{loser['name']} ({self.pname(loser.get('parent_id'))}) is the same facility as "
                     f"{surv['name']}, but it has revenue history and open AR. Instead of inactivating it as a "
                     f"duplicate, keep it exactly as it is and link it to the surviving record.",
                     ops, self.diff_rows(loser, fields, {"chow_current_account": "Surviving account for this facility"}),
                     {"website": loc_evidence(loc), "crm": [self.ev(loser), self.ev(surv)],
                      "score": {"total": total, "signals": br}, "survivor_reason": reasons,
                      "billing": billing_check(loser, True)})
            return
        ops, changes = [], []
        for c in self.contacts_by.get(loser["account_id"], []):
            ops.append({"op": "patch_contact", "contact_id": c["contact_id"],
                        "fields": {"account_id": surv["account_id"]},
                        "expect": {"account_id": loser["account_id"]}})
            changes.append({"record": f"Contact {c.get('name')} ({c.get('title')})",
                            "account_id": None, "field": "account",
                            "current": f"{loser['name']} ({loser['account_id']})",
                            "proposed": f"{surv['name']} ({surv['account_id']})",
                            "why": "Keep the contact on the surviving record"})
        fields = {"duplicate_of_account": surv["account_id"], "status": "Inactive"}
        ops.append({"op": "patch_account", "account_id": loser["account_id"], "fields": fields,
                    "expect": {"duplicate_of_account": loser.get("duplicate_of_account") or "",
                               "status": loser.get("status") or ""},
                    "append_note": f"Duplicate of {surv['account_id']} ({surv['name']}): same facility at "
                                   f"{loc['street']}, {loc['city']} {loc['state']}. Survivor chosen because it "
                                   f"{'; '.join(reasons)}."})
        changes += self.diff_rows(loser, fields, {"duplicate_of_account": "Same facility as the survivor",
                                                  "status": "Losing copy of a duplicate"})
        warnings = []
        if has_billing(loser):
            warnings.append(f"The losing record carries billing history (revenue {usd(loser.get('lifetime_revenue'))}, "
                            f"AR {usd(loser.get('outstanding_ar'))}). Confirm with billing before approving.")
        if surv.get("parent_id") != self.pid and chow_required(surv):
            warnings.append("The survivor is being replaced through a change of ownership; this duplicate "
                            "points at the retained billing record.")
        self.add("duplicate", loser["account_id"],
                 f"{loc['name']}: duplicate record {loser['name']}",
                 f"{loser['name']} ({self.pname(loser.get('parent_id'))}) is the same facility as "
                 f"{surv['name']}. Mark it as a duplicate and inactivate it.",
                 ops, changes,
                 {"website": loc_evidence(loc), "crm": [self.ev(loser), self.ev(surv)],
                  "score": {"total": total, "signals": br}, "survivor_reason": reasons},
                 warnings)

    # -- uncertain: write nothing structural, flag the candidate for a human
    def handle_grey(self, loc, grey):
        for a, (total, br) in grey:
            if a.get("status") == "Needs Review":
                continue
            fields = {"status": "Needs Review"}
            ops = [{"op": "patch_account", "account_id": a["account_id"], "fields": fields,
                    "expect": {"status": a.get("status") or ""},
                    "append_note": f"Possible match for website location {loc['name']} ({loc['street']}, "
                                   f"{loc['city']} {loc['state']}), score {total}. Confirm identity."}]
            self.add("needs_review", a["account_id"], f"{loc['name']}: possible match {a['name']}",
                     f"{a['name']} partly matches {loc['name']} (score {total}), not enough to act on. "
                     f"Flag it for a person to confirm.",
                     ops, self.diff_rows(a, fields, {"status": "Identity uncertain"}),
                     {"website": loc_evidence(loc), "crm": [self.ev(a)],
                      "score": {"total": total, "signals": br}})

    # -- website location with no CRM account
    def handle_new(self, loc, scored):
        def why_not(a, signals):
            got = {k for k, _, _ in signals}
            diffs = []
            if "address" not in got:
                diffs.append(f"different street ({a.get('billing_street')})")
            if "phone" not in got:
                diffs.append("different phone")
            if "administrator" not in got:
                diffs.append(f"administrator {loc.get('administrator')} is not a contact")
            shared = "Same city" if "city" in got else "Same ZIP" if "zip" in got else "Nearby"
            return f"{shared}, but {', '.join(diffs)}"

        near = sorted(((scored[(loc["slug"], a["account_id"])], a) for a in self.pool),
                      key=lambda x: -x[0][0])
        near = [(s, a) for s, a in near if {"city", "zip"} & {k for k, _, _ in s[1]}][:3]
        near_ids = {a["account_id"] for _, a in near}
        same_name = [a for a in self.pool if a["account_id"] not in near_ids
                     and name_similarity(loc["name"], a["name"]) >= 0.75]
        rejected = [{"account": self.ev(a), "score": s[0], "signals": s[1], "why_not": why_not(a, s[1])}
                    for s, a in near]
        rejected += [{"account": self.ev(a), "score": scored[(loc["slug"], a["account_id"])][0], "signals": [],
                      "why_not": f"Similar name, but located in {a.get('billing_city')}, {a.get('billing_state')}"}
                     for a in same_name]
        fields = self.new_account_fields(loc)
        fields["note"] = self.note(f"Created from the Bellhaven website listing {loc['url']}.")
        ops = [{"op": "create_account", "ref": "new", "fields": fields}]
        self.add("create", f"location:{loc['slug']}", f"{loc['name']}: new account",
                 f"{loc['name']} ({loc['city']}, {loc['state']}) is on the Bellhaven website but has no "
                 f"CRM account. Create one under Bellhaven.",
                 ops, self.diff_rows(None, {k: v for k, v in fields.items() if k != "note"}, record="New account"),
                 {"website": loc_evidence(loc), "crm": [], "nearest_candidates": rejected})

    # -- Bellhaven account that is not on the website
    def handle_orphan(self, a):
        succ = [b for b in self.pool
                if b["account_id"] != a["account_id"] and b.get("parent_id") and b.get("parent_id") != self.pid
                and norm_street(b.get("billing_street")) == norm_street(a.get("billing_street"))
                and zip5(b.get("billing_zip")) == zip5(a.get("billing_zip"))]
        evidence = {"crm": [self.ev(a)] + [self.ev(s) for s in succ],
                    "website_checked": len(self.locations),
                    "billing": billing_check(a, bool(succ))}
        if succ:
            s = succ[0]
            evidence["successor"] = (f"{s['name']} under {self.pname(s.get('parent_id'))} is at the same "
                                     f"address ({s.get('billing_street')}).")
            if chow_required(a):
                fields = {"chow_current_account": s["account_id"]}
                ops = [{"op": "patch_account", "account_id": a["account_id"], "fields": fields,
                        "expect": {"chow_current_account": a.get("chow_current_account") or "",
                                   "parent_id": a.get("parent_id") or ""}}]
                self.add("chow_link_existing", a["account_id"], f"{a['name']}: change of ownership",
                         f"{a['name']} is no longer on the Bellhaven website and {s['name']} "
                         f"({self.pname(s.get('parent_id'))}) operates at the same address. The old account has "
                         f"revenue history and open AR, so it stays exactly as it is and links to the existing "
                         f"successor account instead of creating a third record.",
                         ops, self.diff_rows(a, fields, {"chow_current_account": "Successor operator's account"}),
                         evidence)
            else:
                fields = {"duplicate_of_account": s["account_id"], "status": "Inactive"}
                ops = [{"op": "patch_account", "account_id": a["account_id"], "fields": fields,
                        "expect": {k: a.get(k) or "" for k in fields},
                        "append_note": f"Not on the Bellhaven website; {s['name']} "
                                       f"({self.pname(s.get('parent_id'))}) operates at this address."}]
                self.add("orphan_duplicate", a["account_id"], f"{a['name']}: taken over by another operator",
                         f"{a['name']} is no longer on the Bellhaven website and another operator's account "
                         f"exists at the same address. No open billing, so mark it a duplicate of that record.",
                         ops, self.diff_rows(a, fields), evidence)
        elif money(a.get("outstanding_ar")) > 0:
            fields = {"status": "Needs Review"}
            ops = [{"op": "patch_account", "account_id": a["account_id"], "fields": fields,
                    "expect": {"status": a.get("status") or ""},
                    "append_note": f"Not listed on the Bellhaven website; open AR {usd(a.get('outstanding_ar'))} "
                                   f"and no successor operator found. Confirm with billing."}]
            self.add("orphan_needs_review", a["account_id"], f"{a['name']}: not on website, open AR",
                     f"{a['name']} is no longer on the Bellhaven website, no new operator is in the CRM, and it "
                     f"still has open AR. Flag for billing instead of closing it.",
                     ops, self.diff_rows(a, fields), evidence)
        else:
            fields = {"status": "Inactive"}
            ops = [{"op": "patch_account", "account_id": a["account_id"], "fields": fields,
                    "expect": {"status": a.get("status") or ""},
                    "append_note": f"Inactivated: not listed on the Bellhaven website ({len(self.locations)} "
                                   f"communities checked); no billing history; no successor operator found in "
                                   f"the CRM. Reactivate if the facility reappears."}]
            self.add("orphan_inactive", a["account_id"], f"{a['name']}: no longer on website",
                     f"{a['name']} is under Bellhaven but not on the Bellhaven website. No billing history and "
                     f"no other operator at its address, so inactivate it with an explanatory note.",
                     ops, self.diff_rows(a, fields, {"status": "Not on the website"}), evidence)


def build_proposals(locations, accounts, contacts, today=None, orphan_check=True, parent_override=""):
    return Builder(locations, accounts, contacts, today, orphan_check, parent_override).build()
