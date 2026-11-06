"""End-to-end tests against the real exported data. Run: python -m unittest -v"""
import unittest

from bellhaven_sync import applier, pipeline
from bellhaven_sync.scraper import parse_detail
from bellhaven_sync.store import Store
from tests.fake_crm import FakeCRM, FIX, load

BH = "0015QAPLGS3FVYEEEM"
TODAY = "2026-11-05"


def quiet(*_):
    pass


class Base(unittest.TestCase):
    def setUp(self):
        self.crm = FakeCRM()
        self.store = Store(":memory:")
        self.locations = load("locations.json")

    def run_pipeline(self, locations=None, site_stats=None):
        return pipeline.run(self.store, self.crm, locations or self.locations, site_stats,
                            today=TODAY, log=quiet)

    def by_subject(self, subject, kind=None):
        hits = [p for p in self.store.list() if p["subject"] == subject and (kind is None or p["kind"] == kind)]
        self.assertEqual(len(hits), 1, f"expected one proposal for {subject}, got {len(hits)}")
        return hits[0]


class Classification(Base):
    def test_counts(self):
        stats = self.run_pipeline()
        self.assertEqual(stats["generated"], 29)
        self.assertEqual(stats["confirmed_no_change"], 16)
        kinds = {}
        for p in self.store.list():
            kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
        self.assertEqual(kinds, {"update": 9, "reparent": 4, "chow_new_account": 2, "chow_link_existing": 1,
                                 "create": 4, "duplicate": 7, "orphan_inactive": 2})

    def test_sop_branches(self):
        self.run_pipeline()
        self.by_subject("001U6RW32TY0WSXZZB", "chow_new_account")   # Tiffin: revenue + AR
        self.by_subject("001A34WFSUYHCRBLFT", "chow_new_account")   # Marietta: revenue + AR
        self.by_subject("001LGFPBJY4N9MB6KL", "reparent")           # Lima: revenue, AR = 0
        self.by_subject("001SXSF4ELF0Z2LGDM", "chow_link_existing")  # Sandusky -> Millstone

    def test_traps(self):
        self.run_pipeline()
        subjects = {p["subject"] for p in self.store.list()}
        self.assertIn("location:bellhaven-at-union-square", subjects)   # not Juniper's account
        self.assertIn("location:amberly-manor", subjects)                # not the Colorado one
        self.assertIn("location:bellhaven-of-carlisle", subjects)        # not New Carlisle, OH
        self.assertNotIn("001GNU41AVXZRLLJ9P", subjects)
        self.assertNotIn("0015D74ZLRY810RGY5", subjects)
        self.assertNotIn("0019Y4J61ZBG4R00CD", subjects)                 # Akron: right parent, no CHOW

    def test_survivors(self):
        self.run_pipeline()
        self.by_subject("0016KTS1UAWBRXS09J", "reparent")                # Kettering survivor (no parent)
        dup = self.by_subject("001QU150PM4Z15UA71", "duplicate")         # Owosso loser
        self.assertEqual(dup["ops"][0]["op"], "patch_contact")          # contact moves first


class ApplyAndRerun(Base):
    def approve_all_except(self, rejected_subjects=()):
        for p in self.store.list(["pending"]):
            if p["subject"] in rejected_subjects:
                applier.reject(self.store, p["id"], "tester", "billing address is intentional")
            else:
                self.assertEqual(applier.approve(self.store, self.crm, p["id"], "tester"), "applied",
                                 f"{p['title']}: {self.store.get(p['id'])['error']}")

    def test_full_cycle_end_state(self):
        self.run_pipeline()
        before_tiffin = self.crm.get_account("001U6RW32TY0WSXZZB")
        self.approve_all_except({"001NXP9X46CWEPSLSV"})                 # reject Ashtabula PO Box change

        # CHOW: old account untouched except the pointer; new one under Bellhaven
        after_tiffin = self.crm.get_account("001U6RW32TY0WSXZZB")
        changed = {k for k in before_tiffin if before_tiffin[k] != after_tiffin[k]}
        self.assertEqual(changed, {"chow_current_account"})
        new = self.crm.get_account(after_tiffin["chow_current_account"])
        self.assertEqual(new["parent_id"], BH)
        self.assertEqual(new["name"], "Bellhaven of Tiffin")

        sandusky = self.crm.get_account("001SXSF4ELF0Z2LGDM")
        self.assertEqual(sandusky["chow_current_account"], "0017JP8Z1UQ763BVK3")
        self.assertEqual(sandusky["parent_id"], BH)                      # left exactly as it was
        self.assertEqual(sandusky["note"], "")

        lima = self.crm.get_account("001LGFPBJY4N9MB6KL")
        self.assertEqual(lima["parent_id"], BH)
        self.assertIn("[bellhaven-sync]", lima["note"])

        owosso_loser = self.crm.get_account("001QU150PM4Z15UA71")
        self.assertEqual((owosso_loser["status"], owosso_loser["duplicate_of_account"]),
                         ("Inactive", "001EGU7BMJ942ZTRE6"))
        moved = [c for c in self.crm.contacts.values() if c["name"] == "Tricia Lindqvist"][0]
        self.assertEqual(moved["account_id"], "001EGU7BMJ942ZTRE6")

        self.assertEqual(self.crm.get_account("0016PVXH4B25HWR7QE")["status"], "Inactive")   # Coldwater
        self.assertEqual(self.crm.get_account("001NXP9X46CWEPSLSV")["billing_street"], "PO Box 517")
        self.assertEqual(len([w for w in self.crm.writes if w[0] == "create"]), 6)

    def test_rerun_proposes_nothing_new(self):
        self.run_pipeline()
        self.approve_all_except({"001NXP9X46CWEPSLSV"})
        writes = len(self.crm.writes)
        stats = self.run_pipeline()
        self.assertEqual(stats["new"], 0)
        self.assertEqual(stats["reopened"], 0)
        self.assertEqual(stats["suppressed_already_decided"], 1)        # the rejected Ashtabula change
        self.assertEqual(self.store.list(["pending"]), [])
        self.assertEqual(stats["confirmed_no_change"], 34)              # 35 locations minus rejected one
        self.assertEqual(len(self.crm.writes), writes)                  # pipeline never writes
        stats = self.run_pipeline()                                     # and a third time
        self.assertEqual(stats["new"], 0)

    def test_reject_reopen_approve(self):
        self.run_pipeline()
        p = self.by_subject("001NXP9X46CWEPSLSV", "update")             # Ashtabula PO Box
        applier.reject(self.store, p["id"], "tester", "check with billing first")
        self.assertEqual(self.run_pipeline()["new"], 0)                 # stays rejected on re-run
        self.assertEqual(self.store.get(p["id"])["status"], "rejected")
        applier.reopen(self.store, p["id"], "tester", "every other account stores the street here")
        self.assertEqual(self.store.get(p["id"])["status"], "pending")
        stats = self.run_pipeline()                                     # still one proposal, not two
        self.assertEqual(stats["new"], 0)
        self.by_subject("001NXP9X46CWEPSLSV", "update")
        self.assertEqual(applier.approve(self.store, self.crm, p["id"], "tester"), "applied")
        self.assertEqual(self.crm.get_account("001NXP9X46CWEPSLSV")["billing_street"], "3156 W Prospect Rd")
        with self.assertRaises(ValueError):                             # applied changes cannot be reopened
            applier.reopen(self.store, p["id"], "tester")
        self.assertEqual(self.run_pipeline()["new"], 0)

    def test_rerun_with_pending_keeps_one_copy(self):
        self.run_pipeline()
        stats = self.run_pipeline()
        self.assertEqual(stats["new"], 0)
        self.assertEqual(stats["kept_pending"], 29)
        self.assertEqual(len(self.store.list()), 29)


class Safety(Base):
    def test_nothing_written_without_approval(self):
        self.run_pipeline()
        self.assertEqual(self.crm.writes, [])
        p = self.store.list(["pending"])[0]
        with self.assertRaises(ValueError):
            applier.Applier(self.store, self.crm).apply(p["id"])
        self.assertEqual(self.crm.writes, [])

    def test_failed_chow_then_rerun_never_inactivates_billing_account(self):
        self.run_pipeline()
        p = self.by_subject("001U6RW32TY0WSXZZB", "chow_new_account")
        self.crm.fail_next_patch = True                                 # new account created, link fails
        self.assertEqual(applier.approve(self.store, self.crm, p["id"], "tester"), "failed")
        self.run_pipeline()                                             # daily run before anyone retries
        old_tiffin = [x for x in self.store.list(["pending"]) if x["subject"] == "001U6RW32TY0WSXZZB"]
        self.assertNotIn("duplicate", [x["kind"] for x in old_tiffin])  # must not inactivate it
        self.assertEqual(applier.retry(self.store, self.crm, p["id"], "tester"), "applied")
        tiffin = self.crm.get_account("001U6RW32TY0WSXZZB")
        self.assertEqual((tiffin["status"], tiffin["duplicate_of_account"]), ("Active", ""))
        self.assertEqual(len([w for w in self.crm.writes if w[0] == "create"]), 1)
        stats = self.run_pipeline()
        self.assertEqual(stats["new"], 0)

    def test_app_closed_mid_write_can_be_finished(self):
        self.run_pipeline()
        p = self.by_subject("001LGFPBJY4N9MB6KL", "reparent")
        self.store.set_status(p["id"], "approved")                      # crash after approval
        self.assertEqual(applier.retry(self.store, self.crm, p["id"], "tester"), "applied")
        self.assertEqual(self.crm.get_account("001LGFPBJY4N9MB6KL")["parent_id"], BH)

    def test_reopened_failed_proposal_resumes(self):
        self.run_pipeline()
        p = self.by_subject("001A34WFSUYHCRBLFT", "chow_new_account")   # Marietta
        self.crm.fail_next_patch = True
        applier.approve(self.store, self.crm, p["id"], "tester")
        self.store.set_status(p["id"], "failed")
        # same finding regenerated -> reopened, but progress and the created id are kept
        self.store.upsert_proposal(99, {**p, "fingerprint": p["fingerprint"]})
        q = self.store.get(p["id"])
        self.assertEqual((q["status"], q["progress"]), ("pending", 1))
        self.assertIn("new", q["context"])

    def test_drift_marks_stale(self):
        self.run_pipeline()
        p = self.by_subject("001LGFPBJY4N9MB6KL", "reparent")
        self.crm.accounts["001LGFPBJY4N9MB6KL"]["parent_id"] = "00139TNDS8HNLUZ5A6"   # someone else edits
        self.assertEqual(applier.approve(self.store, self.crm, p["id"], "tester"), "stale")
        self.assertEqual(self.crm.writes, [])

    def test_crash_mid_chow_does_not_duplicate(self):
        self.run_pipeline()
        p = self.by_subject("001U6RW32TY0WSXZZB", "chow_new_account")
        self.crm.fail_next_patch = True                                 # create works, link fails
        self.assertEqual(applier.approve(self.store, self.crm, p["id"], "tester"), "failed")
        self.assertEqual(applier.retry(self.store, self.crm, p["id"], "tester"), "applied")
        creates = [w for w in self.crm.writes if w[0] == "create"]
        self.assertEqual(len(creates), 1)
        tiffin = self.crm.get_account("001U6RW32TY0WSXZZB")
        self.assertEqual(tiffin["chow_current_account"], creates[0][1])

    def test_lost_state_reuses_created_account(self):
        self.run_pipeline()
        p = self.by_subject("location:bellhaven-of-batavia", "create")
        applier.approve(self.store, self.crm, p["id"], "tester")
        # Simulate losing the database and re-running from scratch.
        self.store = Store(":memory:")
        stats = self.run_pipeline()
        subjects = {x["subject"] for x in self.store.list()}
        self.assertNotIn("location:bellhaven-of-batavia", subjects)
        self.assertEqual(stats["generated"], 28)

    def test_website_outage_guard(self):
        self.run_pipeline()
        partial = self.locations[:10]
        stats = self.run_pipeline(partial, {"locations": 10, "parse_failures": [], "directory_claim": 34,
                                            "not_in_directory": []})
        self.assertFalse(stats["orphan_check"])
        kinds = {p["kind"] for p in self.store.list(["pending"])}
        self.assertNotIn("orphan_inactive", kinds)


class Scraper(unittest.TestCase):
    def test_parse_detail(self):
        with open(f"{FIX}/sample_detail.html") as fh:
            html = fh.read()
        loc = parse_detail(html, "https://example.test/communities/amberly-manor")
        self.assertEqual((loc["name"], loc["street"], loc["city"], loc["state"], loc["zip"]),
                         ("Amberly Manor", "4390 Darrow Rd", "Hudson", "OH", "44236"))
        self.assertEqual(loc["care_offerings"], ["Assisted Living"])
        self.assertEqual(loc["administrator"], "Tom Trent")


if __name__ == "__main__":
    unittest.main()
