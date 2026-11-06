"""Server-rendered HTML for the review app. Pure functions: data in, HTML out."""
import json
from html import escape

from . import config
from .matcher import KIND_LABELS

STATUS_LABELS = {
    "pending": "Waiting for review",
    "approved": "Approved, writing",
    "applied": "Written to CRM",
    "rejected": "Rejected",
    "failed": "Write failed",
    "stale": "CRM changed since proposal",
    "obsolete": "No longer applies",
}
TABS = [("pending", "To review", ("pending",)),
        ("applied", "Written to CRM", ("applied",)),
        ("rejected", "Rejected", ("rejected",)),
        ("other", "Needs attention", ("failed", "stale", "approved")),
        ("obsolete", "No longer applies", ("obsolete",))]

FIELD_LABELS = {
    "name": "Name", "parent_id": "Parent account", "billing_street": "Street", "billing_city": "City",
    "billing_state": "State", "billing_zip": "ZIP", "care_type": "Care type", "phone": "Phone",
    "status": "Status", "duplicate_of_account": "Duplicate of", "chow_current_account": "CHOW current account",
    "account": "Account",
}

CSS = """
:root{--ink:#18233B;--muted:#5B6578;--paper:#fff;--mist:#F3F5F8;--rule:#D9DEE7;--link:#2453A6;
--ok:#17704A;--ok-bg:#E6F4EC;--no:#A42A22;--no-bg:#FBEAE8;--gate:#7A4A00;--gate-bg:#FFF4DC;--focus:#2453A6}
*{box-sizing:border-box}
body{margin:0;background:var(--mist);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,
"Helvetica Neue",Arial,sans-serif;font-variant-numeric:tabular-nums}
a{color:var(--link)}
a:focus-visible,button:focus-visible,textarea:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.bar{background:var(--paper);border-bottom:1px solid var(--rule)}
.bar-in{max-width:1200px;margin:0 auto;padding:12px 24px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
.brand{font-weight:650;font-size:16px;text-decoration:none;color:var(--ink)}
.bar nav{display:flex;gap:18px;flex:1}
.bar nav a{text-decoration:none;color:var(--muted)} .bar nav a.on{color:var(--ink);font-weight:600}
main{max-width:1200px;margin:0 auto;padding:24px}
h1{font-size:24px;line-height:1.25;margin:4px 0 6px;font-weight:650}
h2{font-size:16px;margin:0 0 10px;font-weight:650}
.muted{color:var(--muted)} .small{font-size:13px}
.panel{background:var(--paper);border:1px solid var(--rule);border-radius:6px;padding:18px 20px;margin-bottom:16px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--rule);margin:8px 0 0;flex-wrap:wrap}
.tabs a{padding:8px 14px;text-decoration:none;color:var(--muted);border-bottom:3px solid transparent;margin-bottom:-1px}
.tabs a.on{color:var(--ink);border-bottom-color:var(--ink);font-weight:600}
.count{display:inline-block;min-width:22px;padding:0 6px;margin-left:6px;border-radius:10px;background:var(--mist);
font-size:12px;text-align:center}
.queue{list-style:none;margin:0;padding:0;background:var(--paper);border:1px solid var(--rule);border-top:0;
border-radius:0 0 6px 6px}
.queue li{border-top:1px solid var(--rule)} .queue li:first-child{border-top:0}
.queue a{display:grid;grid-template-columns:230px 1fr auto;gap:16px;padding:12px 18px;text-decoration:none;color:inherit}
.queue a:hover{background:#F8F9FB}
.kind{font-size:13px;color:var(--muted)} .qtitle{font-weight:600}
.pill{font-size:12px;padding:2px 9px;border-radius:10px;background:var(--mist);white-space:nowrap;align-self:start}
.pill.applied{background:var(--ok-bg);color:var(--ok)} .pill.rejected{background:var(--no-bg);color:var(--no)}
.pill.failed,.pill.stale{background:var(--gate-bg);color:var(--gate)}
.grid{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:20px;align-items:start}
.side{position:sticky;top:16px}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:8px 10px;border-top:1px solid var(--rule);vertical-align:top}
th{font-weight:600;font-size:13px;color:var(--muted);border-top:0}
.diff td.was{color:#7A2A24} .diff td.was span{background:var(--no-bg);text-decoration:line-through;padding:1px 4px;border-radius:3px}
.diff td.now span{background:var(--ok-bg);color:#0F4D33;padding:1px 4px;border-radius:3px;font-weight:600}
.diff td.empty{color:var(--muted)}
.gate{border-left:4px solid var(--gate);background:var(--gate-bg);padding:14px 16px;border-radius:0 6px 6px 0;margin-bottom:16px}
.gate h2{color:var(--gate)} .gate dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:6px 0 8px}
.gate dt{color:var(--muted)} .gate dd{margin:0;font-weight:600}
.warn{border-left:4px solid var(--no);background:var(--no-bg);padding:12px 16px;border-radius:0 6px 6px 0;margin-bottom:16px}
.ev{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.rec{border:1px solid var(--rule);border-radius:6px;padding:12px 14px}
.rec h3{font-size:14px;margin:0 0 6px} .rec dl{display:grid;grid-template-columns:96px 1fr;gap:2px 10px;margin:0;font-size:14px}
.rec dt{color:var(--muted)} .rec dd{margin:0;overflow-wrap:anywhere}
.signals td:nth-child(2){text-align:right;width:60px}
textarea{width:100%;min-height:70px;font:inherit;padding:8px;border:1px solid var(--rule);border-radius:4px;margin:6px 0 12px}
.btn{display:block;width:100%;padding:10px 14px;font:inherit;font-weight:600;border-radius:5px;cursor:pointer;margin-bottom:8px}
.btn.approve{background:var(--ok);color:#fff;border:1px solid var(--ok)}
.btn.reject{background:var(--paper);color:var(--no);border:1px solid var(--no)}
.btn.plain{background:var(--paper);color:var(--ink);border:1px solid var(--rule)}
.flash{padding:10px 14px;border-radius:5px;margin-bottom:16px;background:var(--ok-bg);color:#0F4D33}
.flash.bad{background:var(--no-bg);color:var(--no)}
.log{font-size:13px} .log td{padding:6px 8px} code{font-size:12.5px;overflow-wrap:anywhere}
details summary{cursor:pointer;color:var(--link)}
.empty-state{padding:28px 18px;text-align:center}
@media (max-width:860px){.grid{grid-template-columns:minmax(0,1fr)}.side{position:static}
.queue a{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def e(v):
    return escape("" if v is None else str(v))


def money(v):
    try:
        return f"${float(v or 0):,.0f}"
    except (TypeError, ValueError):
        return e(v)


def layout(title, body, active="queue"):
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in (("queue", "/", "Review queue"), ("runs", "/runs", "Pipeline runs"),
                                 ("audit", "/audit", "Audit log")))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)}</title>
<style>{CSS}</style></head><body>
<div class="bar"><div class="bar-in"><a class="brand" href="/">Bellhaven CRM review</a><nav>{nav}</nav>
<form method="post" action="/run"><button class="btn plain" style="margin:0;width:auto">Run pipeline now</button></form>
</div></div><main>{body}</main></body></html>"""


def flash_html(flash):
    if not flash:
        return ""
    bad = any(w in flash.lower() for w in ("fail", "stale", "error", "cannot"))
    return f'<div class="flash{" bad" if bad else ""}" role="status">{e(flash)}</div>'


# ------------------------------------------------------------------ queue
def queue_page(store, tab="pending", flash=None):
    counts = store.counts()
    tabs_html, statuses = [], ("pending",)
    for key, label, sts in TABS:
        n = sum(counts.get(s, 0) for s in sts)
        if key == tab:
            statuses = sts
        tabs_html.append(f'<a href="/?tab={key}" class="{"on" if key == tab else ""}">{label}'
                         f'<span class="count">{n}</span></a>')
    order = ["chow_new_account", "chow_link_existing", "reparent", "create", "duplicate", "update",
             "needs_review", "orphan_needs_review", "orphan_duplicate", "orphan_inactive"]
    items = sorted(store.list(statuses), key=lambda p: (order.index(p["kind"]) if p["kind"] in order else 99, p["title"]))
    if items:
        rows = "".join(
            f'<li><a href="/p/{p["id"]}"><span class="kind">{e(KIND_LABELS.get(p["kind"], p["kind"]))}</span>'
            f'<span><span class="qtitle">{e(p["title"])}</span><br><span class="muted small">{e(p["summary"])}</span></span>'
            f'<span class="pill {e(p["status"])}">{e(STATUS_LABELS.get(p["status"], p["status"]))}</span></a></li>'
            for p in items)
    else:
        msg = ("Nothing waiting. Run the pipeline to check the website against the CRM."
               if tab == "pending" else "Nothing here yet.")
        rows = f'<li class="empty-state muted">{msg}</li>'

    runs = store.runs(1)
    last = ""
    if runs:
        r = runs[0]
        s = r["stats"]
        conf = r["report"].get("confirmed", [])
        conf_rows = "".join(f"<tr><td>{e(c['location'])}</td><td>{e(c['account'])}</td>"
                            f"<td>{e(', '.join(c['signals']))}</td></tr>" for c in conf)
        guard = ("" if s.get("orphan_check", True) else
                 f'<p class="warn small">Safety guard skipped “no longer on website” checks: '
                 f'{e("; ".join(s.get("guard_reasons", [])))}</p>')
        last = f"""<div class="panel"><h2>Last pipeline run</h2>
<p class="small">Run {r['id']} at {e(r['started_at'])}: {e(s.get('locations'))} website locations and
{e(s.get('accounts'))} CRM accounts checked. {e(s.get('new', 0))} new findings, {e(s.get('suppressed_already_decided', 0))}
already decided, {e(s.get('confirmed_no_change', 0))} locations confirmed with no change needed.</p>{guard}
<details><summary>Show the {len(conf)} confirmed matches</summary><table class="small"><tr><th>Website location</th>
<th>CRM account</th><th>Matched on</th></tr>{conf_rows}</table></details></div>"""

    body = f"""{flash_html(flash)}{last}<div class="tabs" role="tablist">{''.join(tabs_html)}</div>
<ul class="queue">{rows}</ul>"""
    return layout("Review queue", body)


# ------------------------------------------------------------------ detail
def _diff(changes):
    rows = []
    for c in changes:
        cur, new = c.get("current"), c.get("proposed")
        cur_html = f'<td class="was"><span>{e(cur)}</span></td>' if cur not in (None, "") else '<td class="empty">empty</td>'
        rows.append(f"<tr><td>{e(c['record'])}</td><td>{e(FIELD_LABELS.get(c['field'], c['field']))}</td>"
                    f"{cur_html}<td class=\"now\"><span>{e(new)}</span></td>"
                    f"<td class=\"muted small\">{e(c.get('why'))}</td></tr>")
    return (f'<div class="panel"><h2>What changes</h2><div style="overflow-x:auto"><table class="diff">'
            f"<tr><th>Record</th><th>Field</th><th>Now</th><th>After</th><th>Why</th></tr>{''.join(rows)}"
            f"</table></div></div>")


def _billing(b, kind=""):
    # Only where the billing figures drive the decision: parent changes, CHOW, accounts leaving.
    if not b or not (b.get("parent_change") or kind.startswith(("chow", "orphan"))):
        return ""
    return f"""<div class="gate"><h2>Billing check (change-of-ownership rule)</h2><dl>
<dt>Lifetime revenue</dt><dd>{money(b['lifetime_revenue'])}</dd>
<dt>Outstanding AR</dt><dd>{money(b['outstanding_ar'])}</dd>
<dt>Parent changes</dt><dd>{'Yes' if b['parent_change'] else 'No'}</dd></dl>
<p style="margin:0">{e(b['outcome'])}</p></div>"""


def _website_card(w):
    if not w:
        return ""
    where = ("Directory" if w.get("in_directory") else "Not in the directory; linked from "
             + ", ".join(w.get("linked_from") or []))
    return f"""<div class="rec"><h3>Website: {e(w.get('name'))}</h3><dl>
<dt>Address</dt><dd>{e(w.get('street'))}<br>{e(w.get('city'))}, {e(w.get('state'))} {e(w.get('zip'))}</dd>
<dt>Care</dt><dd>{e(', '.join(w.get('care_offerings') or []))}</dd>
<dt>Administrator</dt><dd>{e(w.get('administrator'))}</dd><dt>Phone</dt><dd>{e(w.get('phone'))}</dd>
<dt>Listed in</dt><dd>{e(where)}</dd></dl>
<p class="small" style="margin:8px 0 0"><a href="{e(w.get('url'))}" target="_blank" rel="noopener">Open the website page</a></p></div>"""


def _crm_card(a, heading="CRM"):
    contacts = "".join(f"<br>{e(c['name'])}, {e(c['title'])}{(' (' + e(c['email']) + ')') if c.get('email') else ''}"
                       for c in a.get("contacts", [])) or "<br><span class='muted'>none</span>"
    url = config.crm_browser_url(a.get("account_id"))   # built at display time; never stored
    link = (f'<p class="small" style="margin:8px 0 0"><a href="{e(url)}" target="_blank" rel="noopener">'
            f'Open in CRM</a></p>' if url else "")
    return f"""<div class="rec"><h3>{e(heading)}: {e(a.get('name'))}</h3><dl>
<dt>Account id</dt><dd><code>{e(a.get('account_id'))}</code></dd><dt>Parent</dt><dd>{e(a.get('parent'))}</dd>
<dt>Address</dt><dd>{e(a.get('street'))}<br>{e(a.get('city'))}, {e(a.get('state'))} {e(a.get('zip'))}</dd>
<dt>Care type</dt><dd>{e(a.get('care_type'))}</dd><dt>Status</dt><dd>{e(a.get('status'))}</dd>
<dt>Phone</dt><dd>{e(a.get('phone'))}</dd>
<dt>Billing</dt><dd>{money(a.get('lifetime_revenue'))} revenue, {money(a.get('outstanding_ar'))} AR</dd>
<dt>Contacts</dt><dd>{contacts[4:]}</dd></dl>{link}</div>"""


def _evidence(ev, kind=""):
    cards = [_website_card(ev.get("website"))]
    crm = ev.get("crm") or []
    for i, a in enumerate(crm):
        heading = "CRM"
        if len(crm) > 1 and kind == "duplicate":
            heading = "CRM, losing copy" if i == 0 else "CRM, survivor"
        elif len(crm) > 1:
            heading = "CRM, this record" if i == 0 else "CRM, successor operator"
        cards.append(_crm_card(a, heading))
    out = f'<div class="panel"><h2>Evidence</h2><div class="ev">{"".join(c for c in cards if c)}</div>'

    sc = ev.get("score")
    if sc:
        rows = "".join(f"<tr><td>{e(d)}</td><td>+{p}</td></tr>" for _, p, d in sc["signals"])
        out += (f'<h2 style="margin-top:18px">Why this is the same facility (score {sc["total"]}, '
                f'match at {config.MATCH_THRESHOLD})</h2><table class="signals">{rows}</table>')
    if ev.get("survivor_reason"):
        out += (f'<h2 style="margin-top:18px">Which duplicate survives</h2>'
                f'<p>The surviving record {e("; ".join(ev["survivor_reason"]))}.</p>')
    if ev.get("successor"):
        out += f'<h2 style="margin-top:18px">Successor operator</h2><p>{e(ev["successor"])}</p>'
    if "website_checked" in ev:
        out += (f'<p class="muted small">Checked against all {ev["website_checked"]} communities on the '
                f'Bellhaven website; this account matched none of them.</p>')
    if "nearest_candidates" in ev:
        near = ev["nearest_candidates"]
        if near:
            rows = "".join(f"<tr><td>{e(n['account']['name'])} <span class='muted small'>"
                           f"({e(n['account']['city'])}, {e(n['account']['state'])}; {e(n['account']['parent'])})"
                           f"</span></td><td>{n['score']}</td><td>{e(n['why_not'])}</td></tr>" for n in near)
            out += (f'<h2 style="margin-top:18px">Closest CRM accounts, and why they are not this location</h2>'
                    f'<table><tr><th>Account</th><th>Score</th><th>Reason</th></tr>{rows}</table>')
        else:
            out += '<p class="muted">No CRM account shares this address, city or name.</p>'
    return out + "</div>"


def _decision(store, p):
    st = p["status"]
    if st == "pending":
        return f"""<div class="panel"><h2>Your decision</h2>
<form method="post"><label for="c" class="small muted">Comment (optional, saved with the decision)</label>
<textarea id="c" name="comment"></textarea>
<button class="btn approve" formaction="/p/{p['id']}/approve">Approve and write to CRM</button>
<button class="btn reject" formaction="/p/{p['id']}/reject">Reject</button></form>
<p class="small muted" style="margin:4px 0 0">Approving writes the changes listed here through the CRM API.
Rejecting records the decision; the pipeline will not propose this again.</p></div>"""
    lines = [f'<span class="pill {e(st)}">{e(STATUS_LABELS.get(st, st))}</span>']
    if p.get("decided_by"):
        lines.append(f'<p class="small">Decided by {e(p["decided_by"])} at {e(p["decided_at"])}'
                     + (f': “{e(p["reviewer_comment"])}”' if p.get("reviewer_comment") else "") + "</p>")
    if p.get("error"):
        lines.append(f'<p class="small">{e(p["error"])}</p>')
    if st == "rejected":
        lines.append(f'''<form method="post" action="/p/{p["id"]}/reopen" style="margin-top:12px">
<label for="rc" class="small muted">Reason for reopening (saved in the audit log)</label>
<textarea id="rc" name="comment"></textarea>
<button class="btn plain">Reopen for review</button></form>
<p class="small muted" style="margin:4px 0 0">Nothing was written when this was rejected, so reopening is safe.
It goes back to the review queue.</p>''')
    if st == "failed":
        lines.append(f'<form method="post" action="/p/{p["id"]}/retry"><button class="btn plain">Retry the write</button></form>')
    if st == "approved":
        lines.append('<p class="small">Approved, but the write did not finish (for example the app was closed '
                     'mid-write). Finishing is safe: completed steps are not repeated.</p>'
                     f'<form method="post" action="/p/{p["id"]}/retry"><button class="btn plain">Finish the write</button></form>')
    if st == "stale":
        lines.append('<p class="small">Someone changed this record after the proposal was made, so nothing was '
                     'overwritten. The next pipeline run re-evaluates it.</p>')
    links = []
    ids = [op.get("account_id") for op in p["ops"] if op.get("account_id")]
    ids += [v for v in p["context"].values() if isinstance(v, str)]
    for aid in dict.fromkeys(ids):
        if config.crm_browser_url(aid):
            links.append(f'<a href="{e(config.crm_browser_url(aid))}" target="_blank" rel="noopener">'
                         f'Open {e(aid)} in CRM</a>')
    if st == "applied" and links:
        lines.append("<p class='small'>" + "<br>".join(links) + "</p>")
    log = store.audit_log(p["id"])
    if log:
        def comment(r):
            try:
                c = (json.loads(r["request"] or "{}") or {}).get("comment") if r["action"] in ("approve", "reject", "reopen") else ""
            except (ValueError, AttributeError):
                c = ""
            return f"<br><span class='muted'>“{e(c)}”</span>" if c else ""
        rows = "".join(f"<tr><td>{e(r['ts'][11:19])}</td><td>{e(r['action'])}{comment(r)}</td>"
                       f"<td><code>{e(r['target'])}</code></td></tr>" for r in log)
        lines.append(f'<h2 style="margin-top:14px">Activity</h2><table class="log">{rows}</table>')
    return f'<div class="panel">{"".join(lines)}</div>'


def detail_page(store, p, flash=None):
    warns = "".join(f'<div class="warn" role="note"><strong>Check before approving.</strong> {e(w)}</div>'
                    for w in p.get("warnings", []))
    nxt = store.next_pending(p["id"])
    nav = f'<a href="/">Back to the queue</a>' + (f' &nbsp; <a href="/p/{nxt}">Next to review</a>' if nxt and nxt != p["id"] else "")
    body = f"""{flash_html(flash)}<p class="small">{nav}</p>
<p class="kind" style="margin:0">{e(KIND_LABELS.get(p['kind'], p['kind']))}</p>
<h1>{e(p['title'])}</h1><p style="max-width:75ch">{e(p['summary'])}</p>
<div class="grid"><div>{warns}{_billing(p['evidence'].get('billing'), p['kind'])}{_diff(p['changes'])}{_evidence(p['evidence'], p['kind'])}</div>
<aside class="side">{_decision(store, p)}</aside></div>"""
    return layout(p["title"], body)


# ------------------------------------------------------------------ runs and audit
def runs_page(store):
    rows = "".join(
        f"<tr><td>{r['id']}</td><td>{e(r['started_at'])}</td><td>{'ok' if r['ok'] else 'failed'}</td>"
        f"<td>{e(r['stats'].get('locations', ''))}</td><td>{e(r['stats'].get('generated', ''))}</td>"
        f"<td>{e(r['stats'].get('new', ''))}</td><td>{e(r['stats'].get('suppressed_already_decided', ''))}</td>"
        f"<td>{e(r['stats'].get('obsoleted', ''))}</td><td>{e(r['stats'].get('error', ''))}</td></tr>"
        for r in store.runs(50))
    body = f"""<h1>Pipeline runs</h1><p class="muted">Each run re-checks the website against the CRM. Findings that
were already approved or rejected are not proposed again.</p><div class="panel" style="overflow-x:auto"><table>
<tr><th>Run</th><th>Started</th><th>Result</th><th>Locations</th><th>Findings</th><th>New</th>
<th>Already decided</th><th>No longer apply</th><th>Error</th></tr>{rows}</table></div>"""
    return layout("Pipeline runs", body, "runs")


def audit_page(store):
    rows = "".join(
        f"<tr><td>{e(r['ts'])}</td><td>{e(r['actor'])}</td><td>{e(r['action'])}</td>"
        f"<td><a href='/p/{r['proposal_id']}'>{r['proposal_id']}</a></td><td><code>{e(r['target'])}</code></td>"
        f"<td><code>{e((r['request'] or '')[:300])}</code></td></tr>"
        for r in store.audit_log())
    body = f"""<h1>Audit log</h1><p class="muted">Every decision and every CRM write, newest first.</p>
<div class="panel" style="overflow-x:auto"><table class="log"><tr><th>When</th><th>Who</th><th>Action</th>
<th>Proposal</th><th>Record</th><th>Request</th></tr>{rows}</table></div>"""
    return layout("Audit log", body, "audit")
