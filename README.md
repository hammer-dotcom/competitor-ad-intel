# Competitor Ad Intelligence Agent

> **Live demo:** _add your GitHub Pages URL here after Step 2_
> **60-sec video:** _add your Loom link here_
> **Connect it to Claude:** see [CONNECT.md](CONNECT.md)

A scheduled agent that reads the Meta and LinkedIn ad libraries for a set of
competitors, classifies every creative with Claude against a fixed taxonomy, and
delivers a branded weekly PDF plus a Slack digest. Full agentic loop:
**trigger → scrape → enrich → analyse → deliver.**

Built for paid media, brand, and demand-gen teams tired of manually checking
competitor ads every Monday.

Everything in this repo runs. `python -m src.demo` produces a real PDF and a
publishable static site from fixtures — no API keys, no spend — so you can tune
the design and deploy a public demo before touching a live competitor set.

---

## What it does

Every Monday morning:

1. Reads the public Meta and LinkedIn ad libraries for every advertiser you're tracking
2. Detects which ads are new since last week and stores them
3. Classifies each new creative — hook, angle, offer, funnel stage, audience, proof used — using Claude
4. Aggregates: velocity, angle mix by advertiser, longest-running ads (a proxy for what's working)
5. Renders a branded PDF and posts a Slack digest with the key changes

The output is a document that opens with "here's what changed this week and why
you should care" rather than a data dump.

## Why it's worth reading

A digest that just lists new ads is a worse version of the ad library. This one
earns the open through four analytical choices:

- **Runtime as a performance proxy.** Nobody keeps paying for a creative that doesn't convert. An ad live 60+ days is the closest thing to a public performance signal, so `days_live` gets its own section and is encoded in each card's bar length.
- **Angle mix by advertiser.** Ten ads all selling compliance is a positioning statement. The gaps in the table are your openings.
- **Velocity.** A competitor going from 2 to 14 new creatives is a spend push, usually ahead of a launch or a season.
- **You in the set.** Your own brand row in the angle-mix table turns intel into a decision.

---

## Tech stack, and why

| Layer | Choice | Why |
|---|---|---|
| Meta ads | Apify actor `curious_coder/facebook-ads-library-scraper` | Meta's official Ad Library API only exposes political/social-issue ads; commercial SaaS ads live on the web library only. |
| LinkedIn ads | Apify actor `dz_omar/linkedin-ads-scraper` | For B2B software this is the richer source — most B2B advertisers run LinkedIn, not Meta. |
| Classification | Claude via `anthropic` SDK, tool-schema forced JSON | Enum-constrained output. Prompting for "return JSON" gives you 200 unique angle labels and nothing you can trend. |
| State | SQLite | The most valuable feature is knowing what is *new* since last run. Needs persistence, not a fancy DB. |
| PDF | WeasyPrint (HTML/CSS → PDF) | You already write CSS. Reportlab means learning a drawing API; Playwright-print means shipping a browser. |
| Slack | `slack-sdk`, Block Kit + threaded file upload | Digest in-channel, PDF in the thread so it doesn't clutter. |
| Schedule | GitHub Actions cron | Free, zero infra. |

Not needed: Playwright, proxies, or anti-bot work. Both ad libraries are public
transparency products.

## Costs

| Item | Cost |
|---|---|
| Apify: ~5 advertisers × 40 ads × 2 platforms, weekly | ~$0.30/week |
| Claude classification: ~50 new ads/week, batched | ~$0.10/week |
| GitHub Actions | free |
| **Total** | **under $5/month** |

Apify's free tier ($5 credit/month) covers the whole thing.

---

## Try it locally in 15 minutes

```bash
git clone <this-repo> && cd competitor-ad-intel
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.demo
```

macOS: install WeasyPrint's system libraries first with
`brew install pango gdk-pixbuf libffi`.
Debian/Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0`.

Open `out/ad-intel-<date>.pdf`. That's the document. Open `site/index.html` for
the archive view. Both are produced from `demo/fixtures.json` — no API keys,
no spend, no live scraping.

## Configure it for your own tracked set

Edit `config/competitors.yaml`. The default example is a hypothetical project
management tool tracking Notion, Asana, ClickUp, Monday, and Linear. Swap these
for whichever advertisers matter to you — the pipeline is competitor-agnostic.

- **LinkedIn slug**: the tail of `linkedin.com/company/<slug>`.
- **Meta page ID**: open `facebook.com/ads/library`, search the brand, pick it, copy `view_all_page_id=` from the URL. Leave the list empty for advertisers that don't run Meta — that absence is itself a finding.
- Keep your own brand in the `brand:` block. A digest showing you against the set beats one that only shows them.

## Run it on real data

Once your competitor set is configured and your `.env` has an `APIFY_TOKEN` and
`ANTHROPIC_API_KEY`:

```bash
python -m src.main fetch           # scrape + upsert, no LLM spend
python -m src.main classify        # label anything unclassified
python -m src.main report --window 7 --slack --publish
```

Or the whole pipeline in one command:

```bash
python -m src.main run --window 7 --slack --publish
```

## Ship it

Push to GitHub. The workflow in `.github/workflows/weekly.yml` runs Mondays at
07:00 UTC. Set these in repo settings:

- **Secrets:** `ANTHROPIC_API_KEY`, `APIFY_TOKEN`, `SLACK_BOT_TOKEN`
- **Variables:** `SLACK_CHANNEL`, `BRAND_NAME`

To publish the archive site to GitHub Pages, enable Pages under
Settings → Pages → Source: GitHub Actions, and set variable `DEMO_SITE=true`
(demo mode, safe for public) or `false` (real data, use with private hosting).

---

## Architecture

```
config/competitors.yaml      who to watch
        │
        ▼
src/sources/meta.py          Apify actor → normalise() → shared ad schema
src/sources/linkedin.py
        │
        ▼
src/store.py                 SQLite upsert. Returns True only for genuinely new ads.
        │
        ▼
src/classify.py              Claude, batched 8 ads/call, enum-constrained tool schema
        │
        ▼
src/analyze.py               days-live, angle mix, velocity, "proven" ads
        │
        ├──► src/report/render.py  Jinja → HTML → WeasyPrint PDF
        ├──► src/report/slack.py   Block Kit + threaded PDF
        └──► src/publish.py        static archive site for GitHub Pages / Vercel
```

Adding TikTok or Google Ads Transparency Center later means one new file that
emits the shared ad schema. Nothing downstream changes.

## Connect it to Claude Desktop

The repo ships an MCP server. Install once and Claude Desktop can drive the
whole pipeline in conversation — scan advertisers, spot angle gaps, render the
PDF, post to Slack, all in natural language. See [CONNECT.md](CONNECT.md).

## Making it shareable

Three sharing shapes, in increasing effort:

### Public demo

`python -m src.demo` runs the full pipeline off `demo/fixtures.json`, so a public
deployment is real code, real document, invented data. The site shows a "Demo data" banner so nothing about the deployment is misleading.

1. Make the repo public. Check history for leaked keys: `git log -p | grep -i "sk-ant\|apify_api"`.
2. Settings → Pages → Source: **GitHub Actions**. Add repo variable `DEMO_SITE=true`.
3. Run the `publish-site` workflow. You get `https://<user>.github.io/<repo>/`.

### Private deployment

Same publish step, real data, private hosting. Vercel with password protection
or Cloudflare Pages + Access are the quickest routes to real auth. Or skip
hosting entirely — the Slack digest already threads the PDF and Actions retains
90 days of PDFs in run history.

### Multi-tenant

One YAML and one database per tenant, driven by env vars:

```bash
for t in config/tenants/*.yaml; do
  name=$(basename "$t" .yaml)
  DB_PATH="data/$name.db" \
  SLACK_CHANNEL="#intel-$name" \
  BRAND_NAME="$name" \
  python -m src.main run --window 7 --slack --publish --site "site/$name"
done
```

### Redaction

If the repo is public but the tracked set is proprietary, add a map under
`defaults:` in `config/competitors.yaml`:

```yaml
defaults:
  redact:
    "Real Competitor Ltd": "Competitor A"
```

Every occurrence is replaced before anything is written to `site/`, so the
published HTML never contains the real name.

---

## Known sharp edges

- **Actor field names drift.** Community actors change output keys without notice. `normalise()` is your shock absorber; keep `raw` in the DB so you can re-derive fields without re-scraping.
- **Truncated ad copy.** Meta's library truncates long bodies. The classifier is told to judge on what's present.
- **LLM classification is not ground truth.** The PDF footer says so. Spot-check before quoting in a board deck.
- **Terms of use.** Both ad libraries are public transparency tools. Read them at low volume, don't add logged-in scraping, don't republish creative assets externally.

## Roadmap

1. **Vision on creatives.** Pass image URLs to Claude and classify the *visual* — screenshot vs person vs abstract, whether the hook appears in the image. Design pattern intel is where most of this category's value sits and almost nobody does it.
2. **Landing page diffing.** Fetch the `landing_url`, hash the copy, alert on changes. Ad + LP together is the whole offer.
3. **Message-market drift chart.** Angle mix over 12 weeks as small-multiples in the PDF. Once you have history, this is the slide that gets forwarded.
4. **Same-day change alerts.** A Slack ping when any competitor launches an ad with a new angle, separate from the weekly digest.

## File map

```
config/competitors.yaml         who to track
src/config.py                   env + YAML loading
src/sources/meta.py             Meta Ad Library via Apify
src/sources/linkedin.py         LinkedIn Ad Library via Apify
src/store.py                    SQLite schema, upsert, new-since queries
src/classify.py                 Claude taxonomy classification + synthesis pass
src/analyze.py                  aggregation: mix, velocity, days-live, proven ads
src/report/templates/digest.html   the branded document
src/report/render.py            Jinja → HTML → PDF
src/report/slack.py             Block Kit digest + threaded PDF
src/publish.py                  builds the static archive site, with redaction
src/report/templates/index.html   the public archive index
src/main.py                     CLI: fetch / classify / report / run
src/demo.py                     offline demo from fixtures, no keys required
demo/fixtures.json              invented ads for the public demo build
.github/workflows/weekly.yml    Monday 07:00 UTC cron
.github/workflows/pages.yml     publishes site/ to GitHub Pages
vercel.json                     alternative static host
Dockerfile                      for Railway / any container host
```

---

Inspired by the "scheduled agent for competitive creative intel" pattern that
several agencies have written about publicly. This is an independent
implementation with a Claude-native classification architecture,
runtime-as-signal analysis, and a design system meant for a document you'd
actually forward.
