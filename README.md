# Bellhaven website → CRM sync

Keeps the CRM's Bellhaven accounts in line with the communities on the Bellhaven website.
A daily pipeline finds differences and turns each one into a proposal with its evidence;
a person approves or rejects each proposal in a small review app; only approved changes
are written to the CRM.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export BH_TOKEN=bh_your_token                          # PowerShell: $env:BH_TOKEN="bh_your_token"
export BH_REVIEWER="Your Name"
```

## Run

```bash
python -m unittest -v                                  # offline tests against the real exported data
python -m bellhaven_sync.pipeline run --dry-run        # print what it would propose, store nothing
python -m bellhaven_sync.pipeline run                  # store proposals in state/bellhaven.db
uvicorn bellhaven_sync.app:app --port 8000             # open http://localhost:8000
```

The pipeline never writes to the CRM. Running it again is safe: anything already approved or
rejected is not proposed again (see `store.py` for the exact rules).

## One-click start (Windows + WSL)

```bash
bash run.sh --install-shortcut
```

puts **Bellhaven Review** on your Windows desktop. Double-click it to start the review app (the
browser opens by itself); close its window to stop the app.

## Review

The queue lists every proposal grouped by type. Each proposal page shows what will change,
the billing check when a parent change is involved, the website record next to the CRM
record(s), and the signals behind the match. **Approve and write to CRM** applies it
immediately through the API; **Reject** records the decision. A rejected proposal can be
reopened with a reason. Every action is in the audit log.

## Schedule

`.github/workflows/daily-sync.yml` runs the tests and the pipeline at 11:00 UTC daily and keeps its
state in the Actions cache so decisions persist between runs (add a `BH_TOKEN` repository secret to
enable the CRM steps). `deploy/crontab` is the equivalent cron entry.

## Layout

| Path | What it does |
|---|---|
| `bellhaven_sync/scraper.py` | Crawls homepage, About page and directory; parses each community page |
| `bellhaven_sync/normalize.py` | Address, name, phone and care-type normalization |
| `bellhaven_sync/matcher.py` | Scoring, clustering, survivor choice, CHOW rule, proposal building |
| `bellhaven_sync/store.py` | SQLite state: runs, proposals, decisions, audit log, idempotency |
| `bellhaven_sync/applier.py` | Executes approved proposals with drift checks and resumable steps |
| `bellhaven_sync/pipeline.py` | Daily entry point and safety guard |
| `bellhaven_sync/app.py`, `views.py` | FastAPI review app |
| `tests/` | End-to-end tests with an in-memory CRM loaded from real exported data |

## Demo script (about 2 minutes)

1. Queue: “Last run checked 35 locations and 121 accounts; 16 need nothing, 29 need a decision.”
2. Open **Bellhaven of Tiffin: change of ownership**. Point at the billing check ($84,000
   revenue, $12,400 AR), explain the SOP, approve. Show the activity: create, then link.
3. Open the CRM browser: the old Tiffin account is unchanged except CHOW Current Account;
   the new account sits under Bellhaven.
4. Open **Bellhaven at Union Square: new account**. Show why the Juniper account in the same
   city is not a match (different street, phone, administrator).
5. Open **Bellhaven of Ashtabula: update street**. The current value is a PO Box, which may be
   an intentional billing address. Reject with a comment.
6. Click **Run pipeline now**: 0 new findings, the rejected item stays rejected.

After recording: reopen Ashtabula (reason: every other account stores the street address in
billing_street) and approve it, then approve everything else.
