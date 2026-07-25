# Connect this to Claude

This repo ships an **MCP server** — Model Context Protocol. Once installed, Claude Desktop
can talk to your competitor ad database directly: scan advertisers, label creative, spot
angle gaps, render the PDF, and post to Slack, all in natural language.

Two paths, both work:

- **Local (recommended)** — Claude Desktop runs the server on your machine. Ten minutes.
- **Remote** — the server runs on a URL, connects from any Claude client. More setup.

---

## Local install (Claude Desktop)

### 1. Get the code

```bash
git clone <your-repo> competitor-ad-intel
cd competitor-ad-intel
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

On macOS: `brew install pango gdk-pixbuf` (needed for the PDF renderer).
On Debian/Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0`.
On Windows: WeasyPrint installs its own DLLs via pip, no extra step.

### 2. Add your keys

```bash
cp .env.example .env
```

Open `.env` and paste in:
- `APIFY_TOKEN` — from apify.com → Settings → Integrations
- `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` — optional, only if you want the "post to Slack" tool

**You do not need an Anthropic API key.** That's the point of MCP: the Claude you're
chatting with is the one doing the labelling, so no separate SDK call is made.

### 3. Register the connector

```bash
python scripts_install_connector.py
```

This writes to Claude Desktop's config file (`claude_desktop_config.json`), keeping any
other connectors you already have. It also backs up the previous version to `.bak`.

**Quit Claude Desktop completely and reopen it** — on macOS that's ⌘Q, not just closing
the window. On restart, click the tools icon; you should see `competitor-ad-intel` listed,
with 8 tools and 2 prompts.

To remove: `python scripts_install_connector.py --remove`.

### 4. Try it

In a new Claude chat, type:

> Use the competitor-ad-intel connector. What advertisers am I tracking?

Claude will call `list_tracked_advertisers`. If that returns your competitor set, you're
connected.

Then try the full loop:

> Run the "Run the weekly ad intel digest" prompt over a 30-day window.

Claude picks up the prompt from the server, walks the pipeline, labels every ad in the
chat where you can see and correct it, and renders the PDF at the end.

### If it doesn't appear

Almost always one of three things:

- Claude Desktop wasn't fully quit before reopening. `⌘Q` on macOS.
- The `.venv` wasn't used. Re-run the install script from an activated venv, or from a
  shell where `python3` resolves to the interpreter you installed the deps into.
- Python version. FastMCP needs 3.10+. `python3 --version` to check.

Logs live at `~/Library/Logs/Claude/mcp-server-competitor-ad-intel.log` (macOS) or
`%APPDATA%\Claude\logs\` (Windows). If a tool call fails, the traceback lands there.

---

## What Claude can now do

Eight tools, exposed to any conversation on that Desktop install:

| Tool | Purpose |
|---|---|
| `list_tracked_advertisers` | Show the current tracked set and which platforms each is watched on. |
| `add_advertiser` | Add a competitor by LinkedIn slug or Meta page ID, writing it to config. |
| `scan_ads` | Read the public ad libraries for one, several, or all tracked advertisers. |
| `get_ads_to_classify` | Hand Claude the unlabelled ads plus the taxonomy. |
| `save_classifications` | Store Claude's labels for those ads. |
| `get_digest` | Aggregate the numbers: velocity, angle mix, longest-running ads. |
| `build_report` | Render the branded PDF (and optionally publish the site issue). |
| `post_digest_to_slack` | Send the digest into your Slack channel. |

Plus three **resources** (`adintel://taxonomy`, `adintel://config`, `adintel://digest/latest`)
and two **prompts** (`Run the weekly ad intel digest`, `Find the gap in the market's messaging`).

Because the server exposes primitives rather than one big "run everything" button, you can
have conversations like:

> Which of my competitors has never run a compliance-security angle? What are they leaning
> on instead?

> The Award Force ad about "judging season" — is that still live, and how does it compare
> to what SmartSimple ran the same week?

> Draft three test creatives for me that occupy the angle nobody in the tracked set is
> using right now.

---

## Remote install (any Claude client)

Local stdio is the shortest path, but only works on machines where the repo is checked
out. If you want the connector to work from claude.ai on the web, or from a phone, host
the server as **streamable HTTP**:

```bash
python -m src.mcp_server --http --port 8000
```

That gets you a working endpoint at `http://localhost:8000/mcp`. To make it usable from a
Claude client, you need three more things:

1. **A public URL.** Deploy the `Dockerfile` (already in the repo) to Railway, Fly, or
   Render. Railway command: `railway up`, then set env vars in the dashboard, then set the
   start command to `python -m src.mcp_server --http --port $PORT`.
2. **Auth.** Anonymous remote MCPs are a bad idea — anyone with the URL can drain your
   Apify credit. At minimum, put Cloudflare Access in front, or use the FastMCP OAuth
   provider (see [MCP docs](https://modelcontextprotocol.io) for the current shape).
3. **Register it in Claude.** claude.ai → Settings → Connectors → Add custom connector,
   paste the URL. On Desktop, the local block above supports `"url": "..."` instead of
   `"command"`.

The local install is what I'd send to someone the first time. Do the remote version only
if you specifically need cross-device access.

---

## For anyone sharing this repo

The line that makes this a portfolio-worthy artifact rather than "another scraper" is that
**the model in the conversation does the classification** — the server holds no key, pays
no per-token cost, and every label is visible and correctable in chat. That's an actual
MCP design decision, and it's worth pointing out when someone asks how it works.

If you post the repo, the elevator pitch is:

> A local MCP server that turns Claude into a competitive ad analyst. Install once,
> then ask Claude to scan competitors, label creative, spot angle gaps, and render a
> branded weekly digest — all from natural language. No custom UI, no scheduled job
> required. Runs on ~$0.30/week of scraping credit.
