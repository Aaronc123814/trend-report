#!/usr/bin/env python3
"""Trend Report: a local dashboard of Google Trends' top searches for today and this week.

Run:  python3 trends.py            (opens http://localhost:8765)
      python3 trends.py --port 9000 --no-browser

Data comes from Google Trends' public "Trending now" feeds. They are unofficial,
so if Google changes them the API endpoint below will report an error.
"""
import argparse
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PAGE = ROOT / "static" / "trends.html"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
CACHE_SECONDS = 600
WINDOWS = {"today": 24, "week": 168}
WITH_NEWS = 100  # how many top searches per window get news articles and images
BATCH_URL = "https://trends.google.com/_/TrendsUi/data/batchexecute"
TRENDING_URL = "https://trends.google.com/trending"
TRENDING_DATA = re.compile(r"AF_initDataCallback\(\{key: 'ds:0', hash: '\d+', data:")

CATEGORIES = {
    1: "Autos & vehicles", 2: "Beauty & fashion", 3: "Business & finance", 4: "Entertainment",
    5: "Food & drink", 6: "Games", 7: "Health", 8: "Hobbies & leisure", 9: "Jobs & education",
    10: "Law & government", 11: "Other", 13: "Pets & animals", 14: "Politics", 15: "Science",
    16: "Shopping", 17: "Sports", 18: "Technology", 19: "Travel & transportation", 20: "Climate",
}

_cache = {}
_lock = threading.Lock()
_images = {}            # article URL -> full-size image URL (or None)
_allowed = set()        # article URLs /api/img may fetch
_article_ids = {}       # (geo, term) -> Google's article ids for that trend
_news = {}              # (geo, term) -> resolved news articles


def _get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def _batch(calls):
    """Run several Trends RPCs in one request. calls: [(rpc_id, args)] -> {index: decoded payload}."""
    envelopes = [[rpc, json.dumps(args), None, str(i)] for i, (rpc, args) in enumerate(calls)]
    form = urllib.parse.urlencode({"f.req": json.dumps([envelopes])}).encode()
    raw = _get(BATCH_URL, data=form, headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    out = {}
    for line in raw.splitlines():
        if not line.startswith("[["):
            continue
        for env in json.loads(line):
            if env[0] == "wrb.fr" and env[2]:
                out[int(env[-1]) if env[-1].isdigit() else 0] = json.loads(env[2])
    return out


def fetch_trending(geo, hours, lang="en-US"):
    """All trending searches for a region over the last `hours` (24 or 168)."""
    # The "Trending now" page embeds the full list; the i0OFE RPC has refused anonymous calls since Nov 2026.
    query = urllib.parse.urlencode({"geo": geo, "hl": lang, "hours": hours})
    page = _get(f"{TRENDING_URL}?{query}")
    match = TRENDING_DATA.search(page)
    payload = json.JSONDecoder().raw_decode(page, match.end())[0] if match else None
    if not payload:
        raise RuntimeError("the Google Trends page had no trending data for this region")
    items = []
    for it in payload[1] or []:
        cats = it[10] or []
        items.append({
            "term": it[0],
            "started": (it[3] or [None])[0],
            "ended": (it[4] or [None])[0],
            "volume": it[6] or 0,
            "increase": it[8] or 0,
            "related": (it[9] or [])[:12],
            "categories": [CATEGORIES.get(c, "Other") for c in cats] or ["Other"],
        })
        _article_ids[(geo, it[0])] = (it[11] or [])[:3]
    items.sort(key=lambda x: (-x["volume"], -x["increase"], -(x["started"] or 0)))
    for rank, t in enumerate(items, 1):
        t["rank"] = rank
    return items


def news_for(geo, terms):
    """Up to 3 news articles (title, url, source, thumbnail) per term, looked up 50 per request
    the first time a term is asked for."""
    terms = list(dict.fromkeys(terms))
    todo = [t for t in terms if (geo, t) not in _news]
    for start in range(0, len(todo), 50):
        chunk = todo[start:start + 50]
        lookups = [t for t in chunk if _article_ids.get((geo, t))]
        try:
            res = _batch([("w4opAf", [_article_ids[(geo, t)]]) for t in lookups]) if lookups else {}
        except Exception:
            continue
        for t in chunk:
            _news[(geo, t)] = []
        for i, term in enumerate(lookups):
            for a in (res.get(i) or [[]])[0] or []:
                if a and a[1]:
                    _news[(geo, term)].append({"title": a[0], "url": a[1], "source": a[2], "thumb": a[4] if len(a) > 4 else None})
                    _allowed.add(a[1])
    return {t: _news[(geo, t)] for t in terms if (geo, t) in _news}


def full_image(article_url):
    """The article's own share image (og:image / twitter:image), usually 1200px wide."""
    if article_url in _images:
        return _images[article_url]
    img = None
    try:
        req = urllib.request.Request(article_url, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=8) as r:
            page = r.read(600_000).decode("utf-8", "ignore")
        for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
            if re.search(r"""(?:property|name)\s*=\s*["'](?:og:image(?::secure_url|:url)?|twitter:image(?::src)?)["']""", tag, re.I):
                m = re.search(r"""content\s*=\s*["']([^"']+)""", tag, re.I)
                if m:
                    img = urllib.parse.urljoin(article_url, html.unescape(m.group(1)).strip())
                    if img.startswith("http"):
                        break
                    img = None
    except Exception:
        pass
    _images[article_url] = img
    return img


def fetch_stories(geo):
    """Pictures and news headlines for the current top stories (RSS gives ~10)."""
    ns = {"ht": "https://trends.google.com/trending/rss"}
    root = ET.fromstring(_get(f"https://trends.google.com/trending/rss?geo={urllib.parse.quote(geo)}"))
    stories = {}
    for item in root.iter("item"):
        news = [{
            "title": n.findtext("ht:news_item_title", "", ns),
            "url": n.findtext("ht:news_item_url", "", ns),
            "source": n.findtext("ht:news_item_source", "", ns),
            "picture": n.findtext("ht:news_item_picture", "", ns),
        } for n in item.findall("ht:news_item", ns)]
        for n in news:
            _allowed.add(n["url"])
        stories[item.findtext("title", "").lower()] = {
            "title": item.findtext("title", ""),
            "traffic": item.findtext("ht:approx_traffic", "", ns),
            "published": item.findtext("pubDate", ""),
            "picture": item.findtext("ht:picture", "", ns),
            "picture_source": item.findtext("ht:picture_source", "", ns),
            "news": news,
        }
    return stories


def get_report(geo, fresh=False):
    key = geo
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    report = {"geo": geo, "fetched": int(time.time())}
    for name, hours in WINDOWS.items():
        report[name] = fetch_trending(geo, hours)
    # News for the leaders now; the rest of the ranking asks /api/news as rows scroll into view
    for k in [k for k in _news if k[0] == geo]:
        del _news[k]
    got = news_for(geo, [t["term"] for name in WINDOWS for t in report[name][:WITH_NEWS]])
    for name in WINDOWS:
        for t in report[name]:
            if t["term"] in got:
                t["news"] = got[t["term"]]
    # Warm the full-size image cache for the stories shown first
    leaders = [n["url"] for name in WINDOWS for t in report[name][:12] for n in t.get("news", [])]
    with ThreadPoolExecutor(12) as ex:
        list(ex.map(full_image, leaders))
    try:
        report["stories"] = fetch_stories(geo)
    except Exception:
        report["stories"] = {}
    with _lock:
        _cache[key] = (time.time(), report)
    return report


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._send(200, PAGE.read_text(encoding="utf-8"), "text/html; charset=utf-8")
        elif url.path == "/api/img":
            # Redirect to the first full-size image among the trend's articles, else the thumbnail
            qs = urllib.parse.parse_qs(url.query)
            fallback = (qs.get("fb") or [""])[0]
            target = next((img for u in qs.get("u", [])[:3] if u in _allowed for img in [full_image(u)] if img), None)
            target = target or (fallback if fallback.startswith("https://") else None)
            if target:
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
            else:
                self._send(404, "No image", "text/plain")
        elif url.path == "/api/news":
            qs = urllib.parse.parse_qs(url.query)
            geo = (qs.get("geo") or ["US"])[0].upper()[:6]
            try:
                self._send(200, json.dumps(news_for(geo, qs.get("t", [])[:100])), "application/json")
            except Exception as e:
                self._send(502, json.dumps({"error": str(e)}), "application/json")
        elif url.path == "/api/trends":
            qs = urllib.parse.parse_qs(url.query)
            geo = (qs.get("geo") or ["US"])[0].upper()[:6]
            try:
                self._send(200, json.dumps(get_report(geo, fresh="fresh" in qs)), "application/json")
            except Exception as e:
                self._send(502, json.dumps({"error": f"Could not reach Google Trends: {e}"}), "application/json")
        else:
            self._send(404, "Not found", "text/plain")

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    opts = ap.parse_args()
    ThreadingHTTPServer.request_queue_size = 64  # many images load at once
    server = ThreadingHTTPServer(("127.0.0.1", opts.port), Handler)
    url = f"http://localhost:{opts.port}"
    print(f"Trend Report running at {url}  (Ctrl+C to stop)")
    if not opts.no_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
