# Deploying QuantDeck

QuantDeck is a **stateful, always-on** web app (FastAPI + WebSockets + a
background market-feed loop), not a static site — so it needs a persistent
server process, not serverless/Pages. It ships as a **single Docker image**: the
frontend is built and served by the backend on one origin (the frontend uses
relative `/api` and `ws://<host>/ws` URLs, so there's no CORS or URL config).

## Deploy to Render (recommended)

1. Make sure this repo is pushed to GitHub (it is: `Shubhampandit12/Quantdeck`).
2. Go to https://render.com → sign in with GitHub.
3. **New + → Blueprint** → select the **Quantdeck** repo. Render reads
   `render.yaml` and creates the web service automatically.
4. Click **Apply**. First build takes a few minutes (it builds the frontend,
   then the Python image). When it's live you get a URL like
   `https://quantdeck.onrender.com` — open it and the app loads.

Health check: Render pings `/api/health`. WebSockets work out of the box.

### Plans / gotchas
- **Free plan** sleeps after ~15 min idle (first request afterwards is slow to
  wake) and has an **ephemeral disk** — paper portfolios reset on restart. Fine
  for a demo/portfolio piece.
- **Starter ($7/mo)** = always-on. To also persist portfolios across restarts,
  uncomment the `disk:` block in `render.yaml` (needs a paid plan) so the SQLite
  file lives on a mounted volume.
- **AI is intentionally OFF.** There's no login, so setting `ANTHROPIC_API_KEY`
  on a public URL would let anyone spend your AI credits. Leave it unset (AI
  features degrade gracefully). If you want it on, add the key as a **secret in
  the Render dashboard** (never in git) and ideally add auth first.

## Run the image anywhere (Fly.io, Railway, a VPS, local Docker)

The Dockerfile is host-agnostic and binds to `$PORT` (defaults to 8000):

```bash
docker build -t quantdeck .
docker run -p 8000:8000 quantdeck        # open http://localhost:8000
```

- **Fly.io:** `fly launch` (it detects the Dockerfile), then `fly deploy`.
- **Railway:** New Project → Deploy from GitHub repo → it uses the Dockerfile.
- **VPS:** install Docker, `docker build` + `docker run` behind a reverse proxy
  (Caddy/Nginx) with TLS.

## Optional real-time US-stock data
Crypto is real-time and keyless (Binance). US equities fall back to Yahoo
(15-min delayed) unless you set a free `QUANTDECK_FINNHUB_API_KEY` (or Alpaca
keys) as a platform env var / secret.

## Security reminder
- Never commit `.env` or API keys — they're gitignored and injected as platform
  secrets instead.
- This is **demo money only**; there is no real-brokerage code path.
