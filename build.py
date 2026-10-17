#!/usr/bin/env python3
"""Build the static Trend Report for GitHub Pages.

Run:  python3 build.py                     (all regions, into ./site)
      python3 build.py --regions US,GB --out site

Writes site/index.html and one site/data/<REGION>.json per region. Images are
Google's own news thumbnails, so nothing is fetched from publishers' sites.
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import trends

REGIONS = ["US", "GB", "CA", "AU", "IN", "IE", "NZ", "DE", "FR", "ES", "IT", "BR", "MX", "JP", "ZA", "SG"]


def slim(items):
    """Trim each search to what the page shows, to keep the data files small."""
    for t in items:
        top = t["rank"] <= 10
        t["related"] = t["related"][:10 if top else 5]
        t["news"] = [{k: n[k] for k in ("title", "url", "source", "thumb")} for n in t.get("news", [])[:3 if top else 1]]


def build_region(geo):
    report = {"geo": geo, "fetched": int(time.time())}
    for name, hours in trends.WINDOWS.items():
        report[name] = trends.fetch_trending(geo, hours)
    got = trends.news_for(geo, [t["term"] for name in trends.WINDOWS for t in report[name]])
    for name in trends.WINDOWS:
        for t in report[name]:
            t["news"] = got.get(t["term"], [])
        slim(report[name])
    try:
        report["stories"] = trends.fetch_stories(geo)
    except Exception:
        report["stories"] = {}
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site")
    ap.add_argument("--regions", default=",".join(REGIONS))
    opts = ap.parse_args()
    out = Path(opts.out)
    (out / "data").mkdir(parents=True, exist_ok=True)

    page = trends.PAGE.read_text(encoding="utf-8")
    page = page.replace("<script>", '<script>window.STATIC_DATA = true; document.documentElement.classList.add("static");</script>\n<script>', 1)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / ".nojekyll").write_text("")

    def one(geo):
        start = time.time()
        try:
            report = build_region(geo)
        except Exception as e:
            print(f"  {geo}: FAILED ({e})", flush=True)
            return geo, False
        (out / "data" / f"{geo}.json").write_text(json.dumps(report, separators=(",", ":")), encoding="utf-8")
        print(f"  {geo}: {len(report['today'])} today, {len(report['week'])} this week ({time.time() - start:.0f}s)", flush=True)
        return geo, True

    regions = [r.strip().upper() for r in opts.regions.split(",") if r.strip()]
    print(f"Building {len(regions)} regions into {out}/")
    with ThreadPoolExecutor(4) as ex:
        results = dict(ex.map(one, regions))
    ok = [g for g, good in results.items() if good]
    if not ok:
        sys.exit("No regions could be fetched from Google Trends.")
    print(f"Done: {len(ok)}/{len(regions)} regions.")


if __name__ == "__main__":
    main()
