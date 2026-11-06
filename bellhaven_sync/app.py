"""Review app. Run locally:

    export BH_TOKEN=bh_xxx
    uvicorn bellhaven_sync.app:app --port 8000
    open http://localhost:8000

Approving a proposal writes it to the CRM immediately; nothing is written otherwise.
"""
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import applier, config, pipeline, views
from .crm import CRMClient
from .store import Store

app = FastAPI(title="Bellhaven CRM review")


def _store():
    return Store(config.DB_PATH)


async def _form(request: Request):
    raw = (await request.body()).decode()
    return {k: v[0] for k, v in parse_qs(raw).items()}


def _go(path, msg=None):
    url = path + (("&" if "?" in path else "?") + "msg=" + quote(msg) if msg else "")
    return RedirectResponse(url, status_code=303)


@app.get("/", response_class=HTMLResponse)
def queue(tab: str = "pending", msg: str = None):
    return views.queue_page(_store(), tab, msg)


@app.get("/p/{pid}", response_class=HTMLResponse)
def detail(pid: int, msg: str = None):
    store = _store()
    p = store.get(pid)
    if p is None:
        return HTMLResponse(views.layout("Not found", "<h1>No such proposal</h1><p><a href='/'>Back to the queue</a></p>"),
                            status_code=404)
    return views.detail_page(store, p, msg)


@app.post("/p/{pid}/approve")
async def approve(pid: int, request: Request):
    form = await _form(request)
    store = _store()
    try:
        result = applier.approve(store, CRMClient(), pid, config.REVIEWER, form.get("comment", ""))
    except ValueError as e:
        return _go(f"/p/{pid}", str(e))
    msg = {"applied": "Approved and written to the CRM.",
           "failed": "Approved, but the CRM write failed. See the error and retry.",
           "stale": "Not written: the CRM record changed since this proposal was made."}[result]
    return _go(f"/p/{pid}", msg)


@app.post("/p/{pid}/reject")
async def reject(pid: int, request: Request):
    form = await _form(request)
    store = _store()
    try:
        applier.reject(store, pid, config.REVIEWER, form.get("comment", ""))
    except ValueError as e:
        return _go(f"/p/{pid}", str(e))
    nxt = store.next_pending(pid)
    return _go(f"/p/{nxt}" if nxt else "/", "Rejected. The pipeline will not propose it again.")


@app.post("/p/{pid}/reopen")
async def reopen(pid: int, request: Request):
    form = await _form(request)
    try:
        applier.reopen(_store(), pid, config.REVIEWER, form.get("comment", ""))
    except ValueError as e:
        return _go(f"/p/{pid}", str(e))
    return _go(f"/p/{pid}", "Reopened. It is back in the review queue.")


@app.post("/p/{pid}/retry")
def retry(pid: int):
    try:
        result = applier.retry(_store(), CRMClient(), pid, config.REVIEWER)
    except ValueError as e:
        return _go(f"/p/{pid}", str(e))
    return _go(f"/p/{pid}", "Written to the CRM." if result == "applied" else f"Retry result: {result}.")


@app.post("/run")
def run_now():
    try:
        stats = pipeline.run(_store(), CRMClient(), log=lambda *_: None)
    except Exception as e:  # surfaced to the reviewer, not swallowed
        return _go("/", f"Pipeline run failed: {e}")
    return _go("/", f"Pipeline run finished: {stats['new']} new findings, "
                    f"{stats['suppressed_already_decided']} already decided, "
                    f"{stats['confirmed_no_change']} confirmed with no change.")


@app.get("/runs", response_class=HTMLResponse)
def runs():
    return views.runs_page(_store())


@app.get("/audit", response_class=HTMLResponse)
def audit():
    return views.audit_page(_store())
