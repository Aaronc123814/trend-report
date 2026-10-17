# The Trend Report

A local dashboard of Google's top trending searches for today and this week, styled like a print newspaper.

## Live site

Published with GitHub Pages and rebuilt every hour by `.github/workflows/pages.yml`. To update it straight away, open the repository's **Actions** tab, choose **Update and publish**, then click **Run workflow**. The hosted version uses Google's news thumbnails as its images.

## Run locally

```
python3 trends.py
```

This opens http://localhost:8765 with live data and full-size images. It needs only the Python that ships with macOS, with nothing to install. Press Ctrl+C to stop it.

Options: `--port 9000` to use another port, `--no-browser` to skip opening the browser.

## What's on the page

- **Today / This week**: trending searches from the past 24 hours or 7 days, for 16 regions.
- **Top of the day / week**: the #1 search with news coverage, plus #2–#5.
- **Breaking now**: the newest searches to start trending.
- **The rest of the ranking**: photo cards from #6 to the end of the list.
- **Top 10, By category, Full ranking**: bars, a category filter, and a searchable, sortable table.

## Where the data comes from

| Part | Source |
|---|---|
| Rankings, volumes, categories, related searches | The data behind Google's "Trending now" page (`trends.google.com/_/TrendsUi/data/batchexecute`, rpc `i0OFE`) |
| News headlines for each search | Same endpoint, rpc `w4opAf` |
| Breaking now | Google Trends RSS feed (`trends.google.com/trending/rss?geo=XX`) |
| Photos | Hosted site: Google's news thumbnails. Local app: each article's own share image (`og:image`), falling back to the thumbnail |

The app calculates the ranking itself: highest search volume first, ties broken by % increase and then by recency. Google's endpoints are unofficial and may change without notice.

## Files

- `trends.py`: local server, data fetching and caching (10 minutes)
- `static/trends.html`: the dashboard page (used both locally and on the hosted site)
- `build.py`: builds the hosted site into `site/`, with an `index.html` and one `data/<REGION>.json` per region. Run `python3 build.py --regions US` to try it locally.
