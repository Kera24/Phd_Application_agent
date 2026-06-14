# Deploying ScholarReach online

ScholarReach is two processes that must **both** be online:

- **Backend** — FastAPI (`api/main.py`). Connects to Supabase, runs the agent. Deployed on **Render** (Docker).
- **Dashboard** — Streamlit (`app.py`). Pure frontend; talks only to the backend over HTTPS. Deployed on **Streamlit Community Cloud**.

The dashboard finds the backend via the `SCHOLARREACH_API` env var. If it's unset it defaults to `http://localhost:8001`, which is why a freshly-deployed dashboard shows `Connection refused` until you point it at the live backend.

```
Streamlit Cloud (app.py)  --HTTPS-->  Render (FastAPI)  -->  Supabase (Postgres)
        SCHOLARREACH_API secret              DATABASE_URL secret
```

---

## 1. Deploy the backend on Render

1. Go to the Render dashboard → **New → Blueprint**, and select this GitHub repo. Render reads `render.yaml` and creates the `scholarreach-backend` Docker web service.
2. When prompted (or under the service's **Environment** tab), set the secret env vars:
   - **`DATABASE_URL`** *(required)* — your Supabase **connection pooler** URI in **Session mode** (port `5432`), e.g.
     `postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`
     > Use the pooler, not the direct `db.<ref>.supabase.co` host — the direct host is often IPv6-only and Render's outbound is IPv4, which causes connection failures. Get this string from **Supabase → Project Settings → Database → Connection string → URI** and switch the mode dropdown to the pooler.
   - **`ANTHROPIC_API_KEY`** *(optional)* — leave blank to use deterministic fallbacks.
   - **`TAVILY_API_KEY`** *(optional)* — enables discovery.
3. Deploy. On first boot the lifespan startup runs `init_engine`, which connects to Supabase and **creates the full schema**.
4. Verify health: open `https://<your-service>.onrender.com/healthz` — it should return
   `{"ok": true, "postgres": true}`.
   `"postgres": true` confirms it's on Supabase (not the SQLite fallback).

> **Free-tier note:** Render free web services sleep after ~15 min idle and cold-start in ~30–60s. The dashboard uses a 120s request timeout, so it tolerates cold starts, but the first request after idle will be slow.

## 2. Point the dashboard at the backend (Streamlit Community Cloud)

1. In your Streamlit Cloud app → **Settings → Secrets**, add:
   ```toml
   SCHOLARREACH_API = "https://<your-service>.onrender.com"
   ```
   (No trailing slash.)
2. Reboot the app. The `Connection refused` errors disappear and `/healthz` + `/pipeline` resolve against the live backend.

## 3. Durable scheduling (cron tick) — required for sends/follow-ups to fire

The Render free tier **sleeps when idle**, so in-process timers don't run. Time-
sensitive work (delivering due scheduled sends, reply detection, follow-up
drafting) is driven by an external scheduler that POSTs `/cron/tick`. The
endpoint is idempotent, so duplicate ticks are harmless.

1. In **Render** → Environment, add `CRON_TOKEN` = a long random string. The
   endpoint requires a matching `X-Cron-Token` header (if `CRON_TOKEN` is unset
   the endpoint is unprotected — set it).
2. Pick a scheduler:
   - **GitHub Actions (recommended, in this repo):** `.github/workflows/cron-tick.yml`
     runs every 30 min. Add two repo secrets (Settings → Secrets and variables →
     Actions): `SCHOLARREACH_BACKEND_URL` (your Render URL) and
     `SCHOLARREACH_CRON_TOKEN` (same value as `CRON_TOKEN`).
   - **cron-job.org / EasyCron:** create a job that POSTs
     `https://<service>.onrender.com/cron/tick` with header `X-Cron-Token: <token>`.
   - **Supabase pg_cron + pg_net:** schedule an `net.http_post` to the same URL/header.
3. Verify: `curl -X POST https://<service>.onrender.com/cron/tick -H "X-Cron-Token: <token>"`
   returns `{"sends": {...}, "scan": {...}}`.

> Sends only actually go out when `approved_send_mode` is ON **and** an email has
> been approved (reaching `scheduled`). In draft-only mode the tick reports
> `sends.skipped` and never sends.

## 4. Connect Gmail (web OAuth)

The hosted app uses a redirect-based OAuth flow (the desktop flow can't work on a
headless server). One-time setup in Google Cloud, then connect from the dashboard.

1. **Google Cloud Console** (console.cloud.google.com) → create/select a project.
2. **APIs & Services → Library → Gmail API → Enable.**
3. **OAuth consent screen** → User type **External** → fill app name + your email →
   add yourself under **Test users** → save. (Testing mode is fine for personal
   use; the send/compose scopes work for test users without Google verification.)
4. **Credentials → Create credentials → OAuth client ID** → Application type
   **Web application** → under **Authorized redirect URIs** add exactly:
   `https://scholarreach-backend.onrender.com/gmail/callback` → Create. Copy the
   **Client ID** and **Client secret**.
5. **Render → Environment**, add three vars:
   - `GOOGLE_OAUTH_CLIENT_ID` = the client ID
   - `GOOGLE_OAUTH_CLIENT_SECRET` = the client secret
   - `PUBLIC_BASE_URL` = `https://scholarreach-backend.onrender.com` (must match the
     redirect URI's host)
   Save (redeploys).
6. In the dashboard: **Settings → Connect Gmail → Open Google consent screen** →
   approve in the new tab → return and refresh. The token is stored in your DB
   (`gmail_tokens`, RLS on) and survives redeploys.

> The redirect URI in Google Cloud and `PUBLIC_BASE_URL` must match your backend
> exactly. If you use a custom domain, update both.

## 5. Rotate / manage secrets

- Set every secret in the **Render** and **Streamlit Cloud** dashboards — never commit them. `.mcp.json`, `gmail_credentials.json`, and `token.json` are git-ignored and excluded from the Docker image via `.dockerignore`.
- After rotating the Supabase DB password, update `DATABASE_URL` in Render only.

---

## Local development (unchanged)

```bash
# Backend (SQLite fallback, keyless) on 8001
env -u DATABASE_URL -u ANTHROPIC_API_KEY python -m uvicorn api.main:app --port 8001 --host 127.0.0.1

# Dashboard on 8501 pointed at the local backend
env -u DATABASE_URL -u ANTHROPIC_API_KEY SCHOLARREACH_API=http://localhost:8001 \
  python -m streamlit run app.py --server.port 8501 --server.address localhost --server.headless true
```
