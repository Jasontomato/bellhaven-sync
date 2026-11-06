"""Execute an approved proposal against the CRM.

Safety properties
  * Nothing runs unless the proposal status is 'approved' (or 'failed', for a retry).
  * Drift check: before every write the current record is re-read; if a field
    we expected has changed since the proposal was generated, stop and mark
    the proposal 'stale'. A person, or another process, got there first.
  * Resumable: progress is saved after each op, so a retry after a crash
    continues where it stopped (a CHOW never creates two new accounts).
  * Idempotent create: before POSTing, look for an account we already created
    at the same address under the same parent and reuse it.
  * Every request and response is written to the audit log.
"""
from datetime import date

from . import config
from .crm import CRMError
from .normalize import norm_street, squash, zip5


class Drift(Exception):
    pass


def _resolve(value, ctx):
    if isinstance(value, str) and value.startswith("$ref:"):
        ref = value[5:]
        if ref not in ctx:
            raise RuntimeError(f"Unresolved reference {value}")
        return ctx[ref]
    return value


def _same(a, b):
    return squash(str(a if a is not None else "")) == squash(str(b if b is not None else ""))


def _account_id(resp):
    if isinstance(resp, dict) and isinstance(resp.get("data"), dict):
        resp = resp["data"]
    for k in ("account_id", "id"):
        if isinstance(resp, dict) and resp.get(k):
            return resp[k]
    raise RuntimeError(f"Create response has no account id: {resp!r}")


class Applier:
    def __init__(self, store, crm, actor=None, today=None):
        self.store, self.crm = store, crm
        self.actor = actor or config.REVIEWER
        self.today = today or date.today().isoformat()

    def apply(self, pid):
        p = self.store.get(pid)
        if p is None:
            raise ValueError(f"No proposal {pid}")
        if p["status"] not in ("approved", "failed"):  # 'approved' = approved but not yet fully written
            raise ValueError(f"Proposal {pid} is {p['status']}; only approved proposals are written")
        ctx = dict(p["context"])
        for i, op in enumerate(p["ops"]):
            if i < p["progress"]:
                continue
            try:
                self._run(pid, op, ctx)
            except Drift as e:
                self.store.audit(pid, self.actor, "drift", op.get("account_id") or op.get("contact_id"),
                                 request=op, response=str(e), ok=False)
                self.store.set_status(pid, "stale", error=str(e))
                return "stale"
            except (CRMError, RuntimeError, KeyError, ValueError) as e:
                self.store.audit(pid, self.actor, "error", op.get("account_id") or op.get("contact_id"),
                                 request=op, response=str(e), ok=False)
                self.store.set_status(pid, "failed", error=str(e))
                self.store.save_progress(pid, i, ctx)
                return "failed"
            self.store.save_progress(pid, i + 1, ctx)
        from .store import now
        self.store.set_status(pid, "applied", applied_at=now(), error=None)
        return "applied"

    # ------------------------------------------------------------- op runners
    def _run(self, pid, op, ctx):
        kind = op["op"]
        if kind == "patch_account":
            self._patch_account(pid, op, ctx)
        elif kind == "create_account":
            self._create_account(pid, op, ctx)
        elif kind == "patch_contact":
            self._patch_contact(pid, op, ctx)
        else:
            raise ValueError(f"Unknown op {kind}")

    def _patch_account(self, pid, op, ctx):
        aid = op["account_id"]
        current = self.crm.get_account(aid)
        fields = {k: _resolve(v, ctx) for k, v in op["fields"].items()}
        todo = {k: v for k, v in fields.items() if not _same(current.get(k), v)}
        # Drift: a field we planned to change no longer holds the value we saw.
        # (If it already holds the target value, that op was done; skip quietly.)
        for k, expected in (op.get("expect") or {}).items():
            if k in fields and _same(current.get(k), fields[k]):
                continue
            if not _same(current.get(k), expected):
                raise Drift(f"{aid}.{k} is now {current.get(k)!r}; proposal expected {expected!r}")
        if not todo:
            self.store.audit(pid, self.actor, "skip", aid, request=fields, response="already in target state")
            return
        if op.get("append_note"):
            line = f"{config.NOTE_TAG} {self.today}: {op['append_note']}"
            existing = current.get("note") or ""
            if line not in existing:
                todo["note"] = f"{existing}\n{line}".strip()
        resp = self.crm.patch_account(aid, todo)
        self.store.audit(pid, self.actor, "patch_account", aid, request=todo, response=resp)

    def _create_account(self, pid, op, ctx):
        ref = op.get("ref", "new")
        if ref in ctx:  # already created in an earlier attempt
            return
        fields = dict(op["fields"])
        existing = self._find_existing(fields)
        if existing:
            ctx[ref] = existing["account_id"]
            self.store.audit(pid, self.actor, "reuse_account", existing["account_id"],
                             request=fields, response="account already exists at this address under this parent")
            return
        resp = self.crm.create_account(fields)
        ctx[ref] = _account_id(resp)
        self.store.audit(pid, self.actor, "create_account", ctx[ref], request=fields, response=resp)

    def _find_existing(self, fields):
        try:
            cands = self.crm.list_accounts(zip=fields.get("billing_zip", ""))
        except CRMError:
            return None
        for a in cands:
            if (a.get("parent_id") == fields.get("parent_id")
                    and norm_street(a.get("billing_street")) == norm_street(fields.get("billing_street"))
                    and zip5(a.get("billing_zip")) == zip5(fields.get("billing_zip"))
                    and not a.get("duplicate_of_account") and not a.get("chow_current_account")):
                return a
        return None

    def _patch_contact(self, pid, op, ctx):
        cid = op["contact_id"]
        fields = {k: _resolve(v, ctx) for k, v in op["fields"].items()}
        current = next((c for c in self.crm.list_contacts(account_id=op["expect"]["account_id"])
                        if c.get("contact_id") == cid), None)
        if current is None:
            current = next((c for c in self.crm.list_contacts(account_id=fields.get("account_id", ""))
                            if c.get("contact_id") == cid), None)
            if current is not None and _same(current.get("account_id"), fields.get("account_id")):
                self.store.audit(pid, self.actor, "skip", cid, request=fields, response="already moved")
                return
            raise Drift(f"Contact {cid} is no longer on account {op['expect']['account_id']}")
        resp = self.crm.patch_contact(cid, fields)
        self.store.audit(pid, self.actor, "patch_contact", cid, request=fields, response=resp)


def approve(store, crm, pid, reviewer=None, comment=""):
    from .store import now
    p = store.get(pid)
    if p is None:
        raise ValueError(f"No proposal {pid}")
    if p["status"] != "pending":
        raise ValueError(f"Proposal {pid} is {p['status']}, not pending")
    reviewer = reviewer or config.REVIEWER
    store.set_status(pid, "approved", decided_at=now(), decided_by=reviewer, reviewer_comment=comment or None)
    store.audit(pid, reviewer, "approve", p["subject"], request={"comment": comment})
    return Applier(store, crm, actor=reviewer).apply(pid)


def reject(store, pid, reviewer=None, comment=""):
    from .store import now
    p = store.get(pid)
    if p is None:
        raise ValueError(f"No proposal {pid}")
    if p["status"] != "pending":
        raise ValueError(f"Proposal {pid} is {p['status']}, not pending")
    reviewer = reviewer or config.REVIEWER
    store.set_status(pid, "rejected", decided_at=now(), decided_by=reviewer, reviewer_comment=comment or None)
    store.audit(pid, reviewer, "reject", p["subject"], request={"comment": comment})
    return "rejected"


def reopen(store, pid, reviewer=None, comment=""):
    """Send a rejected proposal back to the queue. Safe: a rejection never wrote anything.

    The proposal keeps its fingerprint, so it is still one record; the drift check at approval time
    protects against anything that changed in the CRM since it was generated.
    """
    p = store.get(pid)
    if p is None:
        raise ValueError(f"No proposal {pid}")
    if p["status"] != "rejected":
        raise ValueError(f"Only rejected proposals can be reopened; proposal {pid} is {p['status']}")
    reviewer = reviewer or config.REVIEWER
    store.set_status(pid, "pending", decided_at=None, decided_by=None, reviewer_comment=None, error=None)
    store.audit(pid, reviewer, "reopen", p["subject"], request={"comment": comment})
    return "pending"


def retry(store, crm, pid, reviewer=None):
    p = store.get(pid)
    if p is None or p["status"] not in ("failed", "approved"):
        raise ValueError(f"Proposal {pid} has no unfinished write to retry")
    return Applier(store, crm, actor=reviewer).apply(pid)
