# 📡 Gig Notifier

> Interactive Telegram bot for discovering and monitoring freelance project listings in real-time from multiple sources.

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/Built%20for-Telegram-26A5E4.svg)](https://telegram.org)
[![Status](https://img.shields.io/badge/Status-Active-green.svg)](#)

</div>

## ✨ Features

- **🌐 Dual Source Monitoring** — Projects.co.id + Fastwork.id in one bot
- **📂 Category Browsing** — Navigate projects via interactive inline keyboards (per platform)
- **📄 Smart Pagination** — Configurable projects per page with Prev/Next navigation
- **🔔 Real-time Monitoring** — Background polling with instant Telegram notifications for new listings
- **⚙️ Per-Category Toggles** — Enable/disable monitoring for specific categories independently (per platform)
- **🧠 Competitive Intel** — Budget comparison vs category average (💎 Above avg, ⚠️ Below avg)
- **👤 Client Reputation** — Track client history: Veteran (10+ projects), Regular (5+), Known (1-4)
- **📊 Daily Digest** — Automated 9 PM summary of all new projects + cron job
- **🏆 Top Clients** — Top 10 clients by project volume with avg budget
- **🛡️ Cloudflare Resilience** — Built-in bypass via Scrapling's StealthyFetcher
- **💾 Persistent State** — Seen projects and monitor config survive restarts
- **🎨 Rich UI** — Emoji-formatted cards, inline buttons, structured layout

## 🖥️ Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.11+ |
| **Projects.co.id** | [Scrapling](https://github.com/D4Vinci/Scrapling) — adaptive parser + StealthyFetcher |
| **Fastwork.id** | Direct REST API (`jobboard-api.fastwork.id`) — no browser needed |
| **HTTP Client** | stdlib `urllib` + `httpx` for Telegram API |
| **Bot API** | Telegram Bot API (long polling, no external framework) |
| **Persistence** | JSON file storage (zero dependencies) |

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or higher
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Your Telegram Chat ID (from [@userinfobot](https://t.me/userinfobot))

### Installation

```bash
# Clone the repository
git clone https://github.com/afg2002/gig-notifier.git
cd gig-notifier

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Chromium for StealthyFetcher (Projects.co.id scraping)
patchright install chromium
```

### Configuration

```bash
# Copy and edit the environment file
cp .env.example .env
```

Edit `.env` with your credentials:

```env
TELEGRAM_BOT_TOKEN=123456...ew11
TELEGRAM_CHAT_ID=123456789
POLL_INTERVAL=300
PROJECTS_PER_PAGE=10
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | — | Your Telegram user/chat ID |
| `POLL_INTERVAL` | No | `300` | Seconds between monitoring polls |
| `PROJECTS_PER_PAGE` | No | `10` | Projects displayed per page in browse mode |

### Run

```bash
python bot.py
```

Send `/start` to your bot on Telegram to begin.

## 📱 Bot Commands

| Command | Description |
|---|---|
| `/start` | Main menu — choose platform (Projects.co.id or Fastwork.id) |
| `/browse` | Browse Projects.co.id projects by category |
| `/monitor` | Configure Projects.co.id per-category monitoring |
| `/refresh` | Manually check for new Projects.co.id projects |
| `/status` | View current Projects.co.id monitoring config |
| `/digest` | View today's project digest (Projects.co.id) |
| `/topclients` | Top 10 clients by project count |
| `/fw` | Browse Fastwork.id jobs by category |
| `/help` | Show help and usage information |

## 🗂️ Project Structure

```
gig-notifier/
├── bot.py                  # Telegram bot — commands, callbacks, polling, monitoring
├── scraper.py              # Projects.co.id scraping engine — adaptive parsing
├── fastwork_scraper.py     # Fastwork.id API integration — REST client
├── requirements.txt        # Python package dependencies
├── .env.example            # Environment variable template
├── .gitignore              # Git ignore rules
├── data/                   # Runtime data (auto-generated, gitignored)
│   ├── seen_projects.json        # Projects.co.id seen IDs (deduplication)
│   ├── fastwork_seen.json        # Fastwork.id seen job IDs
│   ├── monitor_config.json      # Projects.co.id per-category monitoring state
│   ├── fastwork_monitor.json     # Fastwork.id per-category monitoring state
│   ├── client_stats.json        # Client reputation tracking
│   ├── category_budget_stats.json # Category avg budget for competitive intel
│   └── daily_digest.json        # Today's project tracking for digest
└── README.md               # This file
```

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Telegram User                          │
└────────────────┬──────────────────────────┬─────────────────┘
                 │ /commands                 │ Inline callbacks
                 │                           │
┌────────────────▼──────────────────────────▼─────────────────┐
│                         bot.py                                │
│  ┌─────────────────────────────────────────────────────┐     │
│  │              Dual-Source Platform Menu               │     │
│  │    🌐 Projects.co.id    ⚡ Fastwork.id              │     │
│  └─────────────────────────────────────────────────────┘     │
│         │                                    │               │
│  ┌──────▼──────┐                   ┌───────▼───────┐       │
│  │  Projects.co │                   │  Fastwork.id   │       │
│  │  .id Module │                   │  API Module    │       │
│  └──────┬──────┘                   └───────┬───────┘       │
│         │                                  │                │
│  ┌──────▼──────────────────────────────────▼───────┐         │
│  │         Background Monitoring Loop (async)      │         │
│  │  Polls both platforms every POLL_INTERVAL sec    │         │
│  │  Tracks seen IDs, groups by category            │         │
│  │  Sends competitive intel + client reputation     │         │
│  └─────────────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

## 📂 Available Categories

### Projects.co.id

| ID | Name | Emoji |
|---|---|---|
| `web_dev` | Website Development | 💻 |
| `mobile_prog` | Mobile Programming | 📲 |
| `desktop_prog` | Desktop Programming | 🖥️ |
| `game_prog` | Game Programming | 🎮 |
| `data_entry` | Data Entry & Data Mining | 📊 |
| `electronics` | Electronics & Robotics | 🤖 |
| `seo` | SEO & Website Maintenance | 🔍 |

### Fastwork.id

| ID | Name |
|---|---|
| `eb7276d1-...` | Pengembangan Website |
| `3327d5e5-...` | Pengembangan Aplikasi |
| `28956f70-...` | Desain Grafis |
| `c9bfb440-...` | Desain UX/UI |
| `81f7bcc2-...` | Bisnis & Keuangan |
| `a880a9d4-...` | Pemasaran |
| `fc275f48-...` | Penulisan dan Artikel |
| `65cf001b-...` | Fotografi & Videografi |
| `a1fc9903-...` | IT/Technical Support |
| `d2339d10-...` | Pengisi Suara |
| `eac52fa3-...` | Penata Rias |
| `9ea921d0-...` | Pengembangan Diri |
| `b1a4abc1-...` | Teknik Audio |
| `f257cc79-...` | Lainnya |

## 🧠 Competitive Intel

Every notification includes budget comparison and client reputation:

| Emoji | Meaning |
|---|---|
| 💎 | Budget ≥1.5x above category average |
| ✅ | Budget above average (1.2-1.5x) |
| 📊 | Budget at average |
| ⚠️ | Budget below average |
| 🏆 | Veteran client (10+ projects posted) |
| ⭐ | Regular client (5-9 projects) |
| 👤 | Known client (1-4 projects) |
| ❓ | New/unknown client |

## 📊 Daily Digest

Automatic 9 PM summary via cron job:
- All new projects grouped by category
- Budget comparison per project
- Client reputation badges
- Triggered by `/digest` command anytime

## 🔧 Extending

### Adding a New Source

1. Create a scraper module (e.g., `newsource_scraper.py`)
2. Add source entry to `build_main_menu_keyboard()` in `bot.py`
3. Add source selection handler (`_cb_source_select`)
4. Add source-specific handlers (browse, monitor, refresh)
5. Add polling block in `start_polling()`
6. Add `/command` dispatch in `_handle_message()`

### Adding a New Command

1. Add handler in `ProjectsBot._handle_message()`
2. Implement the handler method (e.g., `_cmd_mycommand()`)
3. Update the `/help` text and relevant menu keyboard

### Switching to Webhook Mode

Replace the long-polling loop in `main()` with an async web server (e.g., `aiohttp` + `Telegram.setWebhook()`).

## 📝 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Scrapling](https://github.com/D4Vinci/Scrapling) — Adaptive web scraping framework
- [Patchright](https://github.com/D4Vinci/patchright) — Stealth browser automation
- [Telegram Bot API](https://core.telegram.org/bots/api) — Bot platform
- [Fastwork](https://www.fastwork.id) — Freelance platform
