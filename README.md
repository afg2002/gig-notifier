
# 📡 Gig Notifier

> Interactive Telegram bot for discovering and monitoring freelance project listings in real-time.

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Telegram](https://img.shields.io/badge/Built%20for-Telegram-26A5E4.svg)](https://telegram.org)
[![Scrapling](https://img.shields.io/badge/Powered%20by-Scrapling-orange.svg)](https://github.com/D4Vinci/Scrapling)
|[![Status](https://img.shields.io/badge/Status-Active-green.svg)](#)

</div>



## ✨ Features

- **📂 Category Browsing** — Navigate 16 freelance categories via interactive inline keyboards
- **📄 Smart Pagination** — Configurable projects per page with Prev/Next navigation
- **🔔 Real-time Monitoring** — Background polling with instant Telegram notifications for new listings
- **⚙️ Per-Category Toggles** — Enable/disable monitoring for specific categories independently
- **🛡️ Cloudflare Resilience** — Built-in bypass via Scrapling's StealthyFetcher (fingerprint spoofing, TLS impersonation)
- **🧠 Adaptive Parsing** — Element tracking survives website redesigns without code changes
- **💾 Persistent State** — Seen projects and monitor config survive restarts via JSON storage
- **🎨 Rich UI** — Emoji-formatted cards, inline buttons, and structured message layout

## 🖥️ Tech Stack

| Layer | Technology |
|---|---|
| **Language** | Python 3.11+ |
| **Scraping** | [Scrapling](https://github.com/D4Vinci/Scrapling) — adaptive parser + StealthyFetcher |
| **Browser Engine** | [Patchright](https://github.com/D4Vinci/patchright) — stealth Playwright fork |
| **HTTP Client** | [httpx](https://github.com/encode/httpx) — async HTTP for Telegram API |
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
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Chromium for StealthyFetcher
patchright install chromium
```

### Configuration

```bash
# Copy and edit the environment file
cp .env.example .env
```

Edit `.env` with your credentials:

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
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
| `/start` | Display main menu with interactive buttons |
| `/browse` | Browse projects by category with pagination |
| `/monitor` | Configure per-category monitoring (toggle ON/OFF) |
| `/refresh` | Manually check for new projects now |
| `/status` | View current monitoring configuration |
| `/help` | Show help and usage information |

## 🗂️ Project Structure

```
gig-notifier/
├── bot.py                  # Telegram bot — commands, callbacks, polling, monitoring
├── scraper.py              # Scraping engine — category support, adaptive parsing
├── requirements.txt        # Python package dependencies
├── .env.example            # Environment variable template
├── .gitignore              # Git ignore rules
├── data/                   # Runtime data (auto-generated, gitignored)
│   ├── seen_projects.json      # Tracks notified project IDs (deduplication)
│   └── monitor_config.json     # Per-category monitoring toggle state
└── README.md               # This file
```

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│                  Telegram User                   │
└──────────────┬──────────────────┬───────────────┘
               │                  │
        /commands           Inline callbacks
               │                  │
┌──────────────▼──────────────────▼───────────────┐
│                   bot.py                         │
│  ┌─────────────┐  ┌──────────────────────────┐  │
│  │ Command      │  │ Callback Query           │  │
│  │ Handler      │  │ Handler                  │  │
│  └──────┬──────┘  └──────────┬───────────────┘  │
│         │                    │                   │
│  ┌──────▼────────────────────▼───────────────┐  │
│  │           Inline Keyboard Builder          │  │
│  └──────────────────────┬────────────────────┘  │
│                         │                       │
│  ┌──────────────────────▼────────────────────┐  │
│  │        Message Formatter (HTML/Emoji)      │  │
│  └──────────────────────┬────────────────────┘  │
│                         │                       │
│  ┌──────────────────────▼────────────────────┐  │
│  │     Background Monitoring Loop (async)     │  │
│  └──────────────────────┬────────────────────┘  │
└─────────────────────────┼───────────────────────┘
                          │
               scrape_listing()
                          │
┌─────────────────────────▼───────────────────────┐
│                 scraper.py                       │
│  ┌───────────────────────────────────────────┐  │
│  │  StealthyFetcher (Cloudflare bypass)       │  │
│  │  ↓                                         │  │
│  │  CSS Selectors → Adaptive Parsing          │  │
│  │  ↓                                         │  │
│  │  Project Dataclass (structured output)     │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## 📂 Available Categories

| ID | Name | Emoji | ID | Name | Emoji |
|---|---|---|---|---|---|
| `all` | All Projects | 📋 | `graphic_design` | Layout, Logo & Graphic Design | 🎨 |
| `3d_modeling` | 3D Modeling & Animation | 🎬 | `mobile_prog` | Mobile Programming | 📲 |
| `accounting` | Accounting & Consultancy | 💼 | `network_admin` | Network & System Admin | 🌐 |
| `audio_video` | Audio, Video & Photography | 📸 | `seo` | SEO & Website Maintenance | 🔍 |
| `data_entry` | Data Entry & Data Mining | 📊 | `web_dev` | Website Development | 💻 |
| `desktop_prog` | Desktop Programming | 🖥️ | `writing` | Writing & Translation | ✍️ |
| `electronics` | Electronics & Robotics | 🤖 | `others` | Others | 📦 |
| `game_prog` | Game Programming | 🎮 | | | |
| `internet_marketing` | Internet Marketing & Social Media | 📱 | | | |

## 🔧 Extending

### Adding a New Category

Edit `CATEGORIES` in `scraper.py`:

```python
{"id": "my_category", "name": "My Category", "slug": "99_my-category-slug", "emoji": "🆕"},
```

### Adding a New Command

1. Add handler in `ProjectsBot._handle_message()`
2. Implement the handler method (e.g., `_cmd_mycommand()`)
3. Update the `/help` text and main menu keyboard

### Switching to Webhook Mode

Replace the long-polling loop in `main()` with an async web server (e.g., `aiohttp` + `Telegram.setWebhook()`).

## 📝 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Scrapling](https://github.com/D4Vinci/Scrapling) — Adaptive web scraping framework
- [Patchright](https://github.com/D4Vinci/patchright) — Stealth browser automation
- [Telegram Bot API](https://core.telegram.org/bots/api) — Bot platform


