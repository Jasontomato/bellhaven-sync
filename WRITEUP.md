# Writeup: Bellhaven website → CRM reconciliation

## What I built

A daily pipeline that scrapes the Bellhaven website, matches each community to CRM accounts,
and turns every difference into a reviewable proposal; a FastAPI review app where each
proposal shows its evidence and can be approved (written to the CRM through the API) or
rejected; and a GitHub Actions schedule. Nothing is written to the CRM without an approval.

## Scraping

The directory says 34 communities, but the homepage says 35 and links to Bellhaven Meadows of
Findlay, which is not in the directory. The scraper therefore collects community links from
the homepage, the About page and every directory page, then parses each detail page. It records
where each location was linked from, so the reviewer can see that Findlay comes from the
homepage only.

## Matching

Facilities rarely move, so the street address is the strongest signal. Addresses are compared
after normalization ("1250 Northwest Franklin St" equals "1250 NW Franklin Street"; "Wilmington
Pk" equals "Wilmington Pike"). Each location is scored against every account on independent
signals: same address (60), same city (10), same ZIP (5), same phone (20), the website's
administrator appearing as a contact on the account (20), and name similarity (up to 20, only
within the same state). A score of 50 or more is a match; 30 to 49 would be flagged as Needs
Review rather than acted on (no current account falls in that band). Each account is assigned
only to its best-scoring location.

Name-based matching would have made three mistakes that address-first matching avoids:
linking Bellhaven of Carlisle (PA) to Bellhaven of New Carlisle (OH), linking Amberly Manor in
Hudson, OH to an Amberly Manor in Colorado Springs, and linking Bellhaven at Union Square to
Juniper Point's "Union Square Senior Living", which is in the same city but at a different
street, with a different phone and administrator. All three became new accounts, and the
review page shows each rejected candidate with the specific reason.

## Outcomes

| Finding | Count | Action |
|---|---|---|
| Matched, nothing to change | 16 | None (listed on the queue page as confirmed) |
| Name differs from the website | 7 | Rename to the website name |
| ZIP typo (Portsmouth 45626 → 45662) | 1 | Update |
| Billing street is a PO Box (Ashtabula) | 1 | Rejected pending a check, then reopened and approved (see below) |
| Under the wrong parent or no parent | 6 | Re-parent directly or via CHOW, per the SOP |
| Not in the CRM | 4 | Create under Bellhaven |
| Duplicate records | 7 | `duplicate_of_account` + Inactive |
| Under Bellhaven, not on the website | 3 | Inactivate, or link CHOW to the new owner |

## The change-of-ownership SOP

The rule is applied whenever a parent changes, and it needs both conditions: revenue history
and outstanding AR above zero.

Tiffin ($84,000 revenue, $12,400 AR) and Marietta ($51,250, $3,800) sit under Cedar Trail but are
Bellhaven communities, so the old accounts are left exactly as they are, a new account is created
under Bellhaven, and the old account's `chow_current_account` points to it. I read "exactly as
it is" strictly: the only field written on the old account is the pointer. No note, no status
change. The explanation goes on the new account's note instead.

Bellhaven Crossings of Lima has $47,000 revenue but $0 AR, so it is re-parented directly.
Bellhaven Meadows of Findlay (no parent, $22,000 revenue, $0 AR) and Zanesville and Kettering
($0) are also re-parented directly. Bellhaven Terrace of Akron has revenue and AR but is already
under Bellhaven, so the rule never triggers.

Bellhaven of Sandusky is the reverse direction. It is under Bellhaven with $130,000 revenue and
$5,200 AR, it is no longer on the website, and Millstone Care of Sandusky exists at the same
address under Millstone Health Partners. The SOP says to keep the old account and create a new
one under the correct parent, but that account already exists; creating another would add a
duplicate. So the old account is left untouched and `chow_current_account` points to the
existing Millstone account.

The applier enforces the SOP mechanically too: a CHOW is two steps (create, then link), progress
is saved after each step, and the create step first looks for an account it already created at
that address under that parent. A crash between the steps cannot produce a second new account;
there is a test for exactly that.

## Duplicates

When several accounts match one location, one survives and the rest get `duplicate_of_account`
set to the survivor and status Inactive. The survivor is chosen in this order: already under
Bellhaven, Active, carries billing history, matches the website on exact signals (address text,
phone, administrator), has no other operator as parent, has more contacts. The reason is shown
on the proposal and written in the loser's note.

In Owosso two identical accounts exist; the survivor matches the website's address, phone and
administrator. The losing copy had an active contact with a bellhavenliving.com email, so the
same proposal moves that contact to the survivor before inactivating the duplicate, rather than
stranding it on an Inactive record. In Kettering three accounts share one address and none is
under Bellhaven; the survivor is the one with no parent, so no other operator's record is taken
over. The Harborview and Cedar Trail copies at Monroe, Port Clinton and Erie share addresses with
the Bellhaven accounts and become duplicates. None of the seven losing records has billing history.

A losing record that does carry revenue history and open AR under another parent is never
inactivated: that is a change of ownership, so it is left exactly as it is and linked with
`chow_current_account` to the survivor. No account in this data needs that, but it closes a real
gap: if a CHOW write failed after creating the new account and the daily run happened before a
retry, the old billing account would otherwise have looked like a duplicate.

## Accounts no longer on the website

Alliance and Coldwater are under Bellhaven, not on the website, have no billing history and no
other operator at their address. They are set to Inactive with a note explaining why and saying
to reactivate them if they reappear. I chose Inactive over Needs Review because the evidence is
consistent and the reviewer's approval already is the human decision; Needs Review queues tend
to become permanent. The parent link is kept for history. Needs Review is reserved for an orphan
that still has open AR with no successor, where billing needs to weigh in.

## Judgment calls in review

Ashtabula's billing street is "PO Box 517" while the website lists 3156 W Prospect Rd. The
pipeline proposes the street address, with a warning that the field is a billing address and
a PO Box may be intentional. In review I first rejected it, because overwriting a deliberate
billing mailbox could misroute invoices; the pipeline then stopped proposing it. Checking further,
every other account in the CRM stores the facility's street address in `billing_street`, none
uses a mailbox, so the field works as the facility address here and the PO Box is the stale
outlier. I reopened the proposal (with that reason in the audit log) and approved it. Reopening is
deliberate and only possible for rejected proposals, which never wrote anything.

I did not propose phone updates. Phones differ between the website and CRM on most accounts, the
brief lists name, address and care offerings as the fields of interest, and a phone in the CRM may
be a billing or admissions line. Phones are used only as matching evidence.

## Running daily, safely

Each proposal has a fingerprint built from what it would write (kind, record, target values),
excluding notes, dates and evidence, so the same finding produces the same fingerprint every
day. Approved or rejected fingerprints are never proposed again; pending ones are kept, not
duplicated; and a pending proposal that a later run no longer produces is marked "no longer
applies". After everything is applied, the CRM itself matches the website, so a re-run produces
nothing new even if the state file were lost; the tests check both cases.

A rejection is permanent for the pipeline but not for people: a reviewer can reopen a rejected
proposal, which records the reason and puts it back in the queue. Approved changes are not
reopened; a later correction comes through a new proposal, which keeps the audit trail clean.

Before every write the applier re-reads the record and stops if a field it planned to change no
longer holds the value it saw ("CRM changed since proposal"), so it never overwrites someone
else's edit. A safety guard skips "no longer on the website" proposals when the scrape looks
incomplete (pages failed to parse, fewer communities than the directory claims, or a sharp drop
from the last run), so a website outage cannot cause mass inactivation. Writes are resumable: progress is saved after
each step, so a write interrupted by a crash or a closed app can be finished from the proposal
page without repeating completed steps.

The GitHub Actions workflow runs the tests and the pipeline daily and keeps its state in the Actions
cache, so the repository never holds CRM data or credentials. In production the state would live in a
shared database that the scheduled job and the review app both use.

## What I would do next

Match on geocoded coordinates as an extra signal; alert when a run produces an unusual number of
findings; let the reviewer edit a proposed value (for example a name) before approving; and move
state to Postgres so the scheduled job and the review app share it directly.
