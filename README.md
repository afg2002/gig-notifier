# Gig Notifier

> A Telegram bot for real-time monitoring and discovery of freelance project listings across multiple platforms.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Built for](https://img.shields.io/badge/Built%20for-Telegram-26A5E4.svg)](https://telegram.org/)
[![Status](https://img.shields.io/badge/Status-Active-green.svg)](#)

---

## Overview

Gig Notifier is a self-hosted Telegram bot that monitors freelance platforms — Projects.co.id, Fastwork.id, and Sribu.com — and delivers instant notifications when new project listings match your configured categories. It includes budget intelligence, client reputation tracking, and a daily digest to help prioritize outreach.

---

## Features

| Feature | Description |
|---|---|
| **Multi-Platform Monitoring** | Monitors Projects.co.id, Fastwork.id, and Sribu.com from a single bot |
| **Real-time Notifications** | Background polling with instant Telegram alerts for new listings |
| **Category Browsing** | Navigate projects by category via interactive inline keyboards |
| **Smart Pagination** | Configurable projects per page with Prev/Next navigation |
| **Per-Category Toggles** | Enable or disable monitoring per category independently, per platform |
| **Competitive Intel** | Compares project budget against category average (above/below avg indicators) |
| **Client Reputation** | Tracks client history: Veteran (10+ projects), Regular (5+), Known (1-4) |
| **Daily Digest** | Automated 9 PM summary of all new projects across platforms |
| **Top Clients** | Lists top 10 clients by project volume with average budget |
| **Cloudflare Resilience** | Fallback chain: cloudscraper -> curl_cffi -> Obscura -> StealthyFetcher |
| **Persistent State** | Seen projects and monitor config survive bot restarts |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Projects.co.id | cloudscraper -> curl_cffi -> Obscura (StealthyFetcher) fallback chain |
| Fastwork.id | Direct REST API (`jobboard-api.fastwork.id`) |
| Sribu.com | GraphQL API (`app.api.v2.sribu.com/graphql`) |
| HTTP Client | stdlib `urllib` + `httpx` |
| Bot API | Telegram Bot API (long polling, no external framework) |
| Persistence | JSON file storage |

---

## Quick Start

### Prerequisites

- Python 3.11 or higher
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Your Telegram Chat ID from [@userinfobot](https://t.me/userinfobot)

### Installation

```bash
# Clone the repository
git clone https://github.com/afg2002/gig-notifier.git
cd gig-notifier

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Chromium for StealthyFetcher fallback (Projects.co.id only)
patchright install chromium
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=123456789
POLL_INTERVAL=300
PROJECTS_PER_PAGE=10
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | - | Your Telegram user or chat ID |
| `POLL_INTERVAL` | No | `300` | Seconds between monitoring polls |
| `PROJECTS_PER_PAGE` | No | `10` | Projects displayed per page in browse mode |

### Run

```bash
python bot.py
```

Send `/start` to your bot on Telegram to begin.

---

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Main menu — select platform |
| `/browse` | Browse Projects.co.id projects by category |
| `/monitor` | Configure per-category monitoring for Projects.co.id |
| `/refresh` | Manually check for new Projects.co.id listings |
| `/status` | View current monitoring configuration (all platforms) |
| `/digest` | View today's project digest (all platforms) |
| `/topclients` | Top 10 clients by project count (Projects.co.id) |
| `/fw` | Browse Fastwork.id jobs by category |
| `/sribu` | Browse Sribu.com contests by category |
| `/help` | Show help and usage information |

---

## Project Structure

```
gig-notifier/
├── bot.py                    # Main bot: commands, callbacks, polling, monitoring
├── scraper.py                # Projects.co.id scraping engine
├── fastwork_scraper.py       # Fastwork.id REST API client
├── sribu_scraper.py          # Sribu.com GraphQL client
├── obscurascrape.py          # Obscura headless browser (StealthyFetcher fallback)
├── scrape_sribu_budgets.py   # Budget detail scraper for Sribu.com
├── requirements.txt
├── .env.example
├── .gitignore
└── data/                     # Runtime data (auto-generated, gitignored)
    ├── seen_projects.json
    ├── fastwork_seen.json
    ├── sribu_seen.json
    ├── monitor_config.json
    ├── fastwork_monitor.json
    ├── sribu_monitor.json
    ├── client_stats.json
    ├── category_budget_stats.json
    └── daily_digest.json
```

---

## Available Categories

### Projects.co.id

| ID | Name |
|---|---|
| `web_dev` | Website Development |
| `mobile_prog` | Mobile Programming |
| `desktop_prog` | Desktop Programming |
| `game_prog` | Game Programming |
| `data_entry` | Data Entry & Data Mining |
| `electronics` | Electronics & Robotics |
| `seo` | SEO & Website Maintenance |

### Fastwork.id

| Name |
|---|
| Pengembangan Website |
| Pengembangan Aplikasi |
| Desain Grafis |
| Desain UX/UI |
| Bisnis & Keuangan |
| Pemasaran |
| Penulisan dan Artikel |
| Fotografi & Videografi |
| IT/Technical Support |
| Pengisi Suara |
| Pengembangan Diri |
| Teknik Audio |
| Lainnya |

### Sribu.com

| Name |
|---|
| Website & Programming |
| Logo & Branding |
| Desain Logo |
| Kemasan |
| Video & Audio |
| Writing & Translation |
| Digital Marketing |

---

## Budget Intelligence

| Indicator | Meaning |
|---|---|
| `[PREMIUM]` | Budget >= 1.5x above category average |
| `[ABOVE AVG]` | Budget 1.2-1.5x above average |
| `[AVG]` | Budget at category average |
| `[BELOW AVG]` | Budget below category average |
| `[VETERAN]` | Client with 10+ posted projects |
| `[REGULAR]` | Client with 5-9 posted projects |
| `[KNOWN]` | Client with 1-4 posted projects |
| `[NEW]` | Client with no tracked history |

---

## Extending

### Adding a New Source

1. Create a scraper module (e.g., `newsource_scraper.py`)
2. Add the source entry to `build_main_menu_keyboard()` in `bot.py`
3. Add a source selection handler in `_cb_source_select`
4. Implement browse, monitor, and refresh handlers for the new source
5. Add a polling block in `start_polling()`
6. Register the command in `_handle_message()`

### Adding a New Command

1. Add a handler in `ProjectsBot._handle_message()`
2. Implement the method (e.g., `_cmd_mycommand()`)
3. Update `/help` text and relevant menu keyboards

### Switching to Webhook Mode

Replace the long-polling loop in `main()` with an async web server (e.g., `aiohttp` + `Telegram.setWebhook()`).

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Acknowledgments

- [Scrapling](https://github.com/D4Vinci/Scrapling) — Adaptive web scraping framework
- [Patchright](https://github.com/D4Vinci/patchright) — Stealth browser automation
- [cloudscraper](https://github.com/viaforensics/cloudscraper) — Cloudflare bypass
- [curl_cffi](https://github.com/FFEFFF/curl_cffi) — TLS fingerprint impersonation
- [Telegram Bot API](https://core.telegram.org/bots/api) — Bot platform
- [Fastwork](https://www.fastwork.id/) — Freelance platform
- [Sribu](https://www.sribu.com/) — Design & creative contest platform
