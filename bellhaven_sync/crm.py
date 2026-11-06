"""Thin client for the CRM sandbox API.

Contract (from the OpenAPI spec and observed responses):
  GET    /accounts?page=&page_size=&zip=&street=&parent_id=&q=  -> {data, page, page_size, total}
  GET    /accounts/{id}
  PATCH  /accounts/{id}
  POST   /accounts
  GET    /contacts?account_id=&page=&page_size=
  PATCH  /contacts/{id}
"""
import time

import requests

from .config import API_URL, TOKEN


class CRMError(Exception):
    pass


class CRMClient:
    def __init__(self, token: str = None, api_url: str = API_URL, session=None):
        self.api_url = api_url.rstrip("/")
        self.s = session or requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token or TOKEN}",
            "Accept": "application/json",
            "User-Agent": "bellhaven-sync/1.0",
        })

    # ----------------------------------------------------------- transport
    def _req(self, method, path, **kw):
        url = f"{self.api_url}{path}"
        r = None
        for attempt in range(4):
            r = self.s.request(method, url, timeout=30, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            break
        if not r.ok:
            raise CRMError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}

    @staticmethod
    def _unwrap(body):
        if isinstance(body, dict) and isinstance(body.get("data"), dict):
            return body["data"]
        return body

    def _paged(self, path, **filters):
        out, page = [], 1
        while page <= 200:
            body = self._req("GET", path, params={**filters, "page": page, "page_size": 50})
            items = body.get("data", []) if isinstance(body, dict) else body
            out.extend(items)
            total = int(body.get("total") or 0) if isinstance(body, dict) else len(items)
            if not items or len(out) >= total:
                break
            page += 1
        return out

    # ----------------------------------------------------------- accounts
    def list_accounts(self, **filters):
        return self._paged("/accounts", **filters)

    def get_account(self, account_id):
        return self._unwrap(self._req("GET", f"/accounts/{account_id}"))

    def patch_account(self, account_id, fields):
        return self._unwrap(self._req("PATCH", f"/accounts/{account_id}", json=fields))

    def create_account(self, fields):
        return self._unwrap(self._req("POST", "/accounts", json=fields))

    # ----------------------------------------------------------- contacts
    def list_contacts(self, **filters):
        return self._paged("/contacts", **filters)

    def patch_contact(self, contact_id, fields):
        return self._unwrap(self._req("PATCH", f"/contacts/{contact_id}", json=fields))
