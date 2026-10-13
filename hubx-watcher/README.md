# HUBX deal watcher

Checks the HUBX buyer portal every few hours and emails computers that meet a
profit target and are new on the site or got cheaper since the last check.

For every laptop and desktop it computes the landed cost in Israel:

```
cost (₪) = (price $ + shipping $) × USD/ILS rate × (1 + VAT)
```

The rate comes from the Bank of Israel API on every run. An item is reported when
`cost ≤ sale_price_ils − min_profit_ils` for one of the rules in `config.json` and
it meets that rule's spec filters. Chromebooks, Celeron/Pentium/N-series/MediaTek
CPUs and i3/Ryzen 3 are skipped unless a rule allows them.

The first run emails a summary of up to 40 matches. Later runs email only:

- **Price drops**: matching items that are at least `price_drop_alert_pct` (5%) cheaper than last time.
- **New listings**: matching items that weren't on the site at the last check.

The same item at the same price isn't re-sent for `realert_after_days` (7) days.

## Rules (`config.json`)

| field | meaning |
|---|---|
| `name` | label shown in the email |
| `keywords` | optional; description must contain one (e.g. `["ThinkPad"]`) |
| `category` | optional; `laptops` or `desktops` |
| `sale_price_ils` / `min_profit_ils` | your selling price incl. VAT and the minimum profit |
| `min_cpu_gen` | Intel generation, or the rough equivalent for Ryzen |
| `min_ram_gb` | minimum RAM |
| `require_touch` | touchscreen only |
| `allow_i3`, `allow_chromebook`, `allow_us_only` | off by default |
| `allow_graded` | Grade B/C, scratch & dent (on by default) |
| `min_stock` | minimum units available |

An item is checked against the rules in order and reported under the first one it matches.

## Setup

Copy `.env.example` to `.env` and fill it in. For Gmail, `SMTP_PASS` must be an
[App Password](https://myaccount.google.com/apppasswords), not your normal password.

Try it without sending anything (writes `data/last_email.html`):

```sh
set -a; . ./.env; set +a
python3 watcher.py --dry-run
```

### Hetzner (cron)

Python 3.9+ only, no packages to install.

```sh
git clone … && cd filter1/hubx-watcher
cp .env.example .env && nano .env
crontab -e
# every 3 hours:
0 */3 * * * cd /root/filter1/hubx-watcher && set -a && . ./.env && set +a && python3 watcher.py >> data/watcher.log 2>&1
```

### fly.io

```sh
cd hubx-watcher
fly launch --no-deploy --copy-config --name hubx-watcher
fly volumes create hubx_watcher_data --size 1 --region fra
fly secrets set HUBX_USER=… HUBX_PASS=… SMTP_USER=… SMTP_PASS=… MAIL_TO=…
fly deploy
fly logs
```

The machine runs `watcher.py --loop` and sleeps `INTERVAL_HOURS` between checks.
State (last seen prices) is kept on the volume.
