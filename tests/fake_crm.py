"""In-memory stand-in for the CRM API, loaded from real exported data."""
import copy
import itertools
import json
import os

from bellhaven_sync.crm import CRMError

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


class FakeCRM:
    def __init__(self):
        self.accounts = {a["account_id"]: copy.deepcopy(a) for a in load("accounts.json")}
        self.contacts = {c["contact_id"]: copy.deepcopy(c) for c in load("contacts.json")}
        self._ids = itertools.count(1)
        self.writes = []
        self.fail_next_patch = False

    def _parent_name(self, pid):
        return self.accounts[pid]["name"] if pid in self.accounts else ""

    def list_accounts(self, **f):
        out = list(self.accounts.values())
        for k, field in (("zip", "billing_zip"), ("street", "billing_street"), ("parent_id", "parent_id")):
            if f.get(k):
                out = [a for a in out if a.get(field) == f[k]]
        return copy.deepcopy(out)

    def get_account(self, aid):
        if aid not in self.accounts:
            raise CRMError(f"GET /accounts/{aid} -> HTTP 404")
        return copy.deepcopy(self.accounts[aid])

    def patch_account(self, aid, fields):
        if self.fail_next_patch:
            self.fail_next_patch = False
            raise CRMError(f"PATCH /accounts/{aid} -> HTTP 503: simulated outage")
        a = self.accounts[aid]
        if "status" in fields and fields["status"] not in ("Active", "Inactive", "Needs Review"):
            raise CRMError("422 invalid status")
        a.update(fields)
        if "parent_id" in fields:
            a["parent_name"] = self._parent_name(fields["parent_id"])
        self.writes.append(("patch", aid, fields))
        return copy.deepcopy(a)

    def create_account(self, fields):
        aid = f"001NEW{next(self._ids):012d}"
        a = {"account_id": aid, "name": "", "parent_id": "", "parent_name": "", "billing_street": "",
             "billing_city": "", "billing_state": "", "billing_zip": "", "care_type": "", "status": "Active",
             "phone": "", "lifetime_revenue": 0, "outstanding_ar": 0, "chow_current_account": "",
             "duplicate_of_account": "", "note": "", "created_by_candidate": True, "updated_at": ""}
        a.update(fields)
        a["parent_name"] = self._parent_name(a["parent_id"])
        self.accounts[aid] = a
        self.writes.append(("create", aid, fields))
        return {"data": copy.deepcopy(a)}

    def list_contacts(self, **f):
        out = list(self.contacts.values())
        if f.get("account_id"):
            out = [c for c in out if c.get("account_id") == f["account_id"]]
        return copy.deepcopy(out)

    def patch_contact(self, cid, fields):
        self.contacts[cid].update(fields)
        self.writes.append(("patch_contact", cid, fields))
        return copy.deepcopy(self.contacts[cid])
