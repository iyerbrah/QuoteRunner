# Deploying QuoteRunner

The app runs as a systemd service on `127.0.0.1:8000`. The nginx that already
runs on the VPS proxies `quoterunner.ramiyer.tech` to it, and certbot adds
HTTPS. No Docker.

```
browser ──HTTPS──> nginx (:443) ──HTTP──> uvicorn (127.0.0.1:8000, 2 workers) ──> SQLite (/opt/quoterunner/data)
                                                                       └──────> OpenAI API
```

## First deploy (run these yourself on the VPS)

**1. DNS.** Add an A record `quoterunner` → the VPS's IP at your DNS provider, then check:

```bash
dig +short quoterunner.ramiyer.tech      # should print the VPS IP
```

**2. Check what's installed.**

```bash
python3 --version        # needs 3.12 (Ubuntu 24.04 has it)
nginx -v
certbot --version        # if missing: sudo apt install certbot python3-certbot-nginx
ss -ltnp | grep ':8000'  # should print nothing; if another app uses 8000, pick another
                         # port and change it in quoterunner.service AND nginx-quoterunner.conf
```

**3. Get the code.**

```bash
sudo mkdir -p /opt/quoterunner && sudo chown ram:ram /opt/quoterunner
git clone https://github.com/iyerbrah/QuoteRunner.git /opt/quoterunner
cd /opt/quoterunner
```

**4. Install.** (If `venv` fails, `sudo apt install python3.12-venv` first.)

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

**5. Secrets.** Copy the template, fill in the key, lock it down. Never commit it.

```bash
cp .env.example .env
nano .env                # OPENAI_API_KEY=..., and a lower DAILY_COST_CAP_USD (e.g. 0.50) for the public site
chmod 600 .env
```

Also set a monthly spend limit on the OpenAI dashboard.

**6. Start the app.**

```bash
sudo cp deploy/quoterunner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quoterunner
curl http://127.0.0.1:8000/healthz       # {"status":"ok"}
```

**7. Add the nginx site.** Always run `nginx -t` before a reload, so a typo can't
take down the other sites on this server.

```bash
sudo cp deploy/nginx-quoterunner.conf /etc/nginx/sites-available/quoterunner
sudo ln -s /etc/nginx/sites-available/quoterunner /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

(If your nginx uses `/etc/nginx/conf.d/` instead, copy the file there as `quoterunner.conf`.)

**8. HTTPS.**

```bash
sudo certbot --nginx -d quoterunner.ramiyer.tech
```

Then open https://quoterunner.ramiyer.tech.

## Every redeploy

```bash
cd /opt/quoterunner && ./deploy/deploy.sh
```

It pulls, installs requirements, restarts the service and checks `/healthz`,
stopping at the first failure.

## Day to day

| What | Command |
|---|---|
| Live logs | `journalctl -u quoterunner -f` |
| Status | `systemctl status quoterunner` |
| Restart | `sudo systemctl restart quoterunner` |
| Back up the data | `cp /opt/quoterunner/data/quoterunner.db ~/quoterunner-$(date +%F).db` |

## Before you share the link

- [ ] Finish one full conversation on your phone.
- [ ] Spend caps work: set `DAILY_COST_CAP_USD=0` in `.env`, `sudo systemctl restart quoterunner`,
      send a message, and see "This demo has reached its spending limit". (The check blocks once
      today's spend is at or above the cap, so 0 blocks the first message.) Then set it back.
- [ ] `.env` is not in git: `git log --all -- .env` prints nothing.
- [ ] Add a free uptime monitor (e.g. UptimeRobot) on https://quoterunner.ramiyer.tech/healthz.

## Notes

- **Rate limit and real IPs.** uvicorn runs with `--proxy-headers --forwarded-allow-ips 127.0.0.1`:
  it trusts the `X-Forwarded-For` header only from nginx on this machine, so the per-IP limit
  sees each visitor's address. The limit is kept in memory, so each of the 2 workers counts
  separately (the effective limit is about 2x) and a restart resets it.
- **The /evals page** reads the committed `evals/reports/*.json`, so it shows the same runs as
  GitHub after each `git pull`. No need to run evals on the server.
- **Carrier portals.** The three fictional carrier portals live in this same app (`/portals/...`),
  and the submission agent reaches them over HTTP at `PORTAL_BASE_URL`, which defaults to
  `http://127.0.0.1:8000`, the app's own address behind nginx. If you change the port in
  `quoterunner.service`, set `PORTAL_BASE_URL` in `.env` to match. The first quote request after a
  deploy learns each portal's field mapping (about 10 s, a few LLM calls); it's cached in SQLite
  after that.
