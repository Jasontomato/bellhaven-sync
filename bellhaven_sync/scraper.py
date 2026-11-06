"""Scrape every Bellhaven community from the public website.

Crawls the homepage, the About page and every directory page, because a
location can be linked from the homepage without appearing in the directory
(Bellhaven Meadows of Findlay is the live example). Then parses each
community detail page.
"""
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import BASE_URL

UA = "bellhaven-sync/1.0 (+data quality pipeline)"
LABELS = ("address", "care offerings", "administrator", "phone")


def _get(session, url):
    last = None
    for attempt in range(4):
        try:
            r = session.get(url, timeout=30, headers={"User-Agent": UA})
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            last = str(e)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


def _community_links(soup, page_url, base=BASE_URL):
    pat = re.compile(rf"^{re.escape(base)}/communities/[^/?#]+$")
    links = set()
    for a in soup.select("a[href]"):
        href = urljoin(page_url, a["href"]).split("#")[0].split("?")[0].rstrip("/")
        if pat.match(href):
            links.add(href)
    return links


def _next_page(soup, page_url):
    for a in soup.select("a[href]"):
        if re.search(r"\bnext\b", a.get_text(" ", strip=True), re.I):
            return urljoin(page_url, a["href"])
    return None


def _lines(el):
    return [t.strip() for t in el.get_text("\n").split("\n") if t.strip()]


def parse_detail(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    sections = {}

    dl = soup.select_one("dl.detail") or soup.find("dl")
    if dl:
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd is not None:
                sections[dt.get_text(" ", strip=True).rstrip(":").lower()] = dd

    def text_lines(key):
        el = sections.get(key)
        return _lines(el) if el is not None else []

    addr = text_lines("address")
    offerings = []
    if "care offerings" in sections:
        offerings = [b.get_text(" ", strip=True) for b in sections["care offerings"].select(".badge")]
        offerings = offerings or text_lines("care offerings")

    # Fallback: label/value text layout, in case the markup changes.
    if not addr:
        root = soup.find("main") or soup.body or soup
        cur, fb = None, {}
        for ln in _lines(root):
            key = ln.rstrip(":").lower()
            if key in LABELS:
                cur = key
                fb[cur] = []
            elif ln.startswith(("←", "©")):
                cur = None
            elif cur:
                fb[cur].append(ln)
        addr = fb.get("address", [])
        offerings = offerings or fb.get("care offerings", [])

    street = city = state = zip_ = None
    if addr:
        m = re.match(r"^(.+?),\s*([A-Z]{2})\s+(\d{5})(?:-\d{4})?$", addr[-1])
        if m:
            city, state, zip_ = m.groups()
            street = ", ".join(addr[:-1]) or None

    return {
        "slug": url.rstrip("/").rsplit("/", 1)[-1],
        "url": url,
        "name": h1.get_text(" ", strip=True) if h1 else None,
        "street": street, "city": city, "state": state, "zip": zip_,
        "care_offerings": offerings,
        "administrator": " ".join(text_lines("administrator")) or None,
        "phone": " ".join(text_lines("phone")) or None,
    }


def scrape(session=None, base=BASE_URL, delay=0.05):
    """Return (locations, stats). stats feeds the pipeline's safety guard."""
    session = session or requests.Session()
    found = {}

    for start in (f"{base}/", f"{base}/about"):
        try:
            soup = BeautifulSoup(_get(session, start), "html.parser")
        except RuntimeError:
            continue
        for link in _community_links(soup, start, base):
            found.setdefault(link, set()).add(start[len(base):] or "/")

    directory_claim, page_url, visited, n = None, f"{base}/communities", set(), 0
    while page_url and page_url not in visited and n < 50:
        visited.add(page_url)
        n += 1
        soup = BeautifulSoup(_get(session, page_url), "html.parser")
        m = re.search(r"(\d+)\s+communities listed", soup.get_text(" "))
        if m:
            directory_claim = int(m.group(1))
        for link in _community_links(soup, page_url, base):
            found.setdefault(link, set()).add(f"directory page {n}")
        page_url = _next_page(soup, page_url)

    locations, failures = [], []
    for url in sorted(found):
        loc = parse_detail(_get(session, url), url)
        loc["linked_from"] = sorted(found[url])
        loc["in_directory"] = any(s.startswith("directory") for s in found[url])
        if not (loc["name"] and loc["street"] and loc["zip"]):
            failures.append(url)
        else:
            locations.append(loc)
        time.sleep(delay)

    stats = {
        "locations": len(locations),
        "directory_claim": directory_claim,
        "directory_pages": n,
        "parse_failures": failures,
        "not_in_directory": [l["name"] for l in locations if not l["in_directory"]],
    }
    return locations, stats
