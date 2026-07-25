# Competitor Ad / LinkedIn Intel Agent

> **Want to connect this to Claude?** See [CONNECT.md](CONNECT.md). Ten-minute local
> install; Claude Desktop drives the whole pipeline in chat.


A scheduled agent that reads the Meta and LinkedIn ad libraries for a set of competitors,
classifies every creative against a fixed taxonomy with Claude, and delivers a branded PDF
plus a Slack digest. Full agentic loop: **trigger → scrape → enrich → analyse → deliver.**

Everything in this repo runs. `python -m src.demo` produces a real PDF and a publishable
static site from fixtures — no API keys, no spend — so you can tune the design, and deploy a
public demo, before touching a live competitor set. See **§9 Making it shareable**.

---

## 1. Tool list, and why each one

| Layer | Choice | Why this and not the alternative |
|---|---|---|
| Meta ads | Apify actor `curious_coder/facebook-ads-library-scraper` | $0.75/1k ads, ~99.7% success, 35k users. Meta's *official* Ad Library API only exposes political/social-issue ads — commercial SaaS ads are visible on the web library but absent from that API, so scraping is the only route. |
| LinkedIn ads | Apify actor `dz_omar/linkedin-ads-scraper` | $1.50/1k ads. For B2B software this is the richer source — most of your competitor set runs LinkedIn, not Meta. Alt: `s-r/linkedin-ads-library`. |
| Classification | Claude via `anthropic` SDK, tool-schema forced JSON | Tool schemas give you enum-constrained output. Prompting for "return JSON" gives you 200 unique angle labels and nothing you can trend. |
| State | SQLite (stdlib) | The single most valuable feature is knowing what is **new** since last run. That needs persistence, not a fancy DB. Swap for Postgres (Neon/Supabase) if you deploy to a container without a volume. |
| PDF | WeasyPrint (HTML/CSS → PDF) | You already write CSS. Reportlab means learning a drawing API; Playwright-print means shipping a browser. |
| Slack | `slack-sdk`, Block Kit + threaded file upload | Digest in-channel, PDF in the thread so it doesn't clutter. |
| Schedule | GitHub Actions cron (free) | No infra. Railway cron if you need a persistent volume or >6h jobs. |
| Secrets | `.env` locally, GitHub Actions secrets in CI | |

**Not needed:** Playwright, proxies, or anti-bot work. That's the whole reason to use Apify
actors — someone else maintains the breakage surface, and both ad libraries are public
transparency products, not gated data.

---

## 2. Build order

Do it in this sequence. Each step is independently testable, which is the difference
between a two-evening project and a two-week one.

### Step 0 — Accounts (20 min)
1. Apify account → **Settings → Integrations → API token**. Free tier gives $5/month credit, which covers roughly 5,000 ads.
2. Anthropic Console → API key.
3. Slack: **api.slack.com/apps → Create App → From scratch**. Add bot scopes `chat:write` and `files:write`. Install to workspace, copy the `xoxb-` token, then invite the bot to the channel (`/invite @yourbot`).

### Step 1 — Local setup (10 min)
```bash
git clone <your-repo> && cd competitor-ad-intel
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in the keys
```
WeasyPrint needs system libraries. macOS: `brew install pango gdk-pixbuf libffi`.
Debian/Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0`.

### Step 2 — Prove the report before you scrape anything
```bash
python -m src.demo     # → out/ad-intel-<date>.pdf and site/index.html
```
Iterate on `src/report/templates/digest.html` until you'd be happy sending it to a CMO.
This is the step people skip and then regret: a beautiful pipeline delivering an ugly PDF
reads as unfinished.

### Step 3 — Fill in your competitor set
Edit `config/competitors.yaml`.

- **LinkedIn slug**: the tail of `linkedin.com/company/<slug>`.
- **Meta page ID**: open `facebook.com/ads/library`, search the brand, pick it, and copy `view_all_page_id=` from the URL. Leave the list empty for competitors that don't run Meta — most B2B ones don't, and that absence is itself a finding.
- Keep your own brand in the `brand:` block. A digest that shows you against the set is far more useful than one that only shows them.

### Step 4 — First real fetch (no LLM spend)
```bash
python -m src.main fetch
```
Expect the first run to be noisy. Check `data/ads.db`:
```bash
sqlite3 data/ads.db "select competitor, platform, count(*) from ads group by 1,2;"
sqlite3 data/ads.db "select competitor, substr(body,1,90) from ads limit 10;"
```
If `body` is empty for a source, the actor's field names changed — fix the `normalise()`
function in `src/sources/*.py`. This is the only part of the system that rots, and it's
20 lines per source.

### Step 5 — Classify
```bash
python -m src.main classify
```
Then read ten labels by hand:
```bash
sqlite3 data/ads.db "select competitor, json_extract(classification,'$.angle'), json_extract(classification,'$.hook') from ads limit 10;"
```
If labels feel wrong, the fix is almost always the **taxonomy**, not the prompt. Angles that
overlap ("outcome/ROI" vs "cost-saving") produce inconsistent labelling. Merge them.

### Step 6 — Deliver
```bash
python -m src.main report --window 30            # PDF only, first run needs a wide window
python -m src.main report --window 7 --slack     # the weekly shape
```

### Step 7 — Schedule
Push to GitHub. Then **Settings → Secrets and variables → Actions**:
secrets `ANTHROPIC_API_KEY`, `APIFY_TOKEN`, `SLACK_BOT_TOKEN`; variables `SLACK_CHANNEL`, `BRAND_NAME`.
Run the workflow manually once via **Actions → weekly-ad-intel → Run workflow** before trusting the cron.

The workflow commits `data/ads.db` back to the repo after each run. That's deliberate:
GitHub runners are ephemeral, and without persisted state every week looks like week one
and every ad looks new. If that feels dirty, move to Postgres and drop the commit step.

### Step 8 (optional) — Railway instead
```bash
railway init && railway up
```
Set the same env vars in the Railway dashboard, add a cron schedule `0 7 * * 1`, and attach
a volume mounted at `/app/data`. Use this if you want a persistent DB without committing it,
or if you later add a web view. Vercel is the wrong tool here — 
serverless functions time out long before a multi-competitor scrape finishes.

---

## 3. Architecture

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
        └──► src/report/slack.py   Block Kit + threaded PDF
```

The shared ad schema every source must emit:
```
platform, ad_id, advertiser, headline, body, cta_text, landing_url,
first_seen, last_seen, is_active, media[], permalink, raw
```
Adding TikTok or Google Ads Transparency Center later means one new file that emits this
dict. Nothing downstream changes.

---

## 4. The analytical ideas that make it worth reading

A digest that just lists new ads is a worse version of the ad library. What earns the open:

- **Runtime as a performance proxy.** Nobody keeps paying for a creative that doesn't convert. An ad live 60+ days is the closest thing to a public performance signal, so `days_live` is surfaced as its own section, and encoded in the card's bar length.
- **Angle mix by advertiser.** Ten ads all selling compliance is a positioning statement. The gaps in the table are your openings.
- **Velocity.** A competitor going from 2 to 14 new creatives is a spend push, usually ahead of a launch or a season.
- **You in the set.** Your own row in the angle-mix table turns intel into a decision.
- **Conservative "notable" field.** The classifier is instructed to say "nothing notable" for most ads. Manufactured insight on every row destroys trust in the whole document.

---

## 5. Costs

| Item | Cost |
|---|---|
| Apify: 6 advertisers × ~40 ads × 2 platforms, weekly | ~$0.30/week |
| Claude classification: ~50 new ads/week, batched | ~$0.10/week |
| Claude synthesis pass | negligible |
| GitHub Actions | free |
| **Total** | **well under $5/month** |

The Apify free tier ($5 credit/month) covers this outright.

---

## 6. Known sharp edges

- **Actor field names drift.** Community actors change output keys without notice. `normalise()` is your shock absorber; keep `raw` in the DB so you can re-derive fields without re-scraping.
- **Truncated ad copy.** Meta's library truncates long bodies. The classifier is told to judge on what's present.
- **Fonts in PDF.** WeasyPrint won't always fetch Google Fonts over the network. For deterministic output, download the TTFs into `src/report/templates/fonts/` and swap the `@import` for local `@font-face` rules.
- **LLM classification is not ground truth.** The PDF footer says so. Spot-check before anything goes in a board deck.
- **Terms of use.** Both ad libraries are public transparency tools, and the actors read them at low volume with no authentication. Keep it that way: don't add logged-in scraping, don't hammer, and don't republish competitors' creative assets externally.

---

## 7. Sensible next builds

1. **Vision on creatives.** Pass `media[]` image URLs to Claude and classify the *visual* — screenshot vs person vs abstract, text density, whether the hook appears in the image. Design pattern intel is where most of this category's value sits and almost nobody does it.
2. **Landing page diffing.** You already store `landing_url`. Fetch it, hash the copy, and alert on changes. Ad + LP together is the whole offer.
3. **Message-market drift chart.** Angle mix over 12 weeks as a small-multiples line chart in the PDF. Once you have history, this is the slide that gets forwarded.
4. **Change-only alerts.** A same-day Slack ping when any competitor launches an ad with a new angle, separate from the weekly digest.
5. **Multi-tenant.** One YAML per client, one channel per client. That's the productised version.

---

## 8. Files

```
config/competitors.yaml         who to track
src/config.py                   env + YAML loading
src/sources/meta.py             Meta Ad Library via Apify
src/sources/linkedin.py         LinkedIn Ad Library via Apify
src/store.py                    SQLite schema, upsert, new-since queries
src/classify.py                 Claude taxonomy classification + synthesis pass
src/analyze.py                  aggregation: mix, velocity, days-live, proven ads
src/report/templates/digest.html  the branded document
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

## 9. Making it shareable

`out/` is a scratch directory. `site/` is a deployable artifact: a stable index plus every
past issue at a permanent URL, generated by `src/publish.py`. It's plain static HTML, so it
needs no runtime and costs nothing to host.

```bash
python -m src.demo                              # fixtures → PDF + site/, no keys needed
python -m src.main report --window 7 --publish  # real data → PDF + site/
```

Open `site/index.html`. Three sharing shapes, in increasing effort:

### A. Portfolio — a link a hiring manager can click

The obstacle is that a live agent needs your keys and your real competitor set. Solve it by
publishing the **demo build**: `python -m src.demo` runs the full pipeline off
`demo/fixtures.json`, so the deployed site is real code, real document, invented data. The
index shows a "Demo data" banner, because pretending otherwise is the one thing that would
actually cost you the job.

1. Make the repo public. Confirm `.env` is gitignored and no real keys are in the history: `git log -p | grep -i "sk-ant\|apify_api"`.
2. Enable **Settings → Pages → Source: GitHub Actions**, then set repository variable `DEMO_SITE=true`.
3. Run **Actions → publish-site → Run workflow**. You get `https://<user>.github.io/<repo>/`.
4. Vercel is the alternative if you want a custom domain: `vercel --prod` picks up `vercel.json`, which runs the same demo build and serves `site/`.

Put the live URL and the sample PDF at the top of the README. Recruiters read a README for
about fifteen seconds; a screenshot of the document beats any description of the pipeline.

### B. Team — a link Neil and Patrick can bookmark

Same publish step, real data, **private** hosting. Options in order of effort:

- **Private repo + Pages**: needs GitHub Enterprise for private Pages. Usually not worth it.
- **Vercel with password protection** (Pro) or **Cloudflare Pages + Access**: one click, real auth, five minutes.
- **Netlify with basic auth** via `_headers`.
- **Simplest of all**: skip hosting. The Slack digest already threads the PDF, and `actions/upload-artifact` keeps 90 days of PDFs in the run history. Add the archive site only when someone asks "what did they run in April?"

Set repository variable `DEMO_SITE=false` so the workflow publishes real output, and change
`--window` to match your cadence.

### C. Product — other people run it on their own competitor set

No code changes needed. One YAML and one database per tenant, driven by env vars:

```bash
for t in config/tenants/*.yaml; do
  name=$(basename "$t" .yaml)
  DB_PATH="data/$name.db" \
  SLACK_CHANNEL="#intel-$name" \
  BRAND_NAME="$name" \
  python -m src.main run --window 7 --slack --publish --site "site/$name"
done
```

Separate DB files mean no chance of one client's ads appearing in another's digest, which is
the only failure mode here that would actually end a client relationship. For a "deploy your
own" button, add a Railway template pointing at the repo — the `Dockerfile` and `railway.json`
are already the whole configuration.

### Redaction

If the repo is public but the tracked set is a client's, add a map under `defaults:` in
`config/competitors.yaml`:

```yaml
defaults:
  redact:
    "Real Client Ltd": "Competitor A"
```

Every occurrence is replaced before anything is written to `site/`, so the published HTML
never contains the real name. It does **not** touch `data/ads.db` — which is exactly why
that file should never be committed to a public repo.

### Before you make the repo public — checklist

- [ ] `.env` in `.gitignore`, and not present in git history
- [ ] `data/ads.db` **not** committed. The weekly workflow commits it by default; on a public repo, either move state to Postgres (Neon free tier) or push the DB to a private branch instead
- [ ] Fixtures reviewed — `demo/fixtures.json` is invented copy, not scraped competitor text
- [ ] Repo secrets set as *secrets*, never as *variables* (variables are visible in logs)
- [ ] README opens with the live link, a screenshot, and the cost line
