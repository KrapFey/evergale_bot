# Evergale BOT

A Discord guild management bot for gaming guilds. Integrates with the **Raid-Helper** bot to automate roster classification and attendance tracking.

---

## Commands

### `/boss` — Boss database

Maintains a shared, persistent list of game bosses.

| Command | Access | Description |
|---|---|---|
| `/boss list` | Everyone | Display the numbered boss list |
| `/boss add <name>` | Admin | Append a boss by name |
| `/boss remove <identifier>` | Admin | Remove by exact name or list number |
| `/boss random` | Everyone | Pick a random boss publicly |

---

### `/roster` — Raid event management

Works with Raid-Helper signup messages.

| Command | Access | Description |
|---|---|---|
| `/roster generate <raid_msg> <destination>` | Admin | Parse a Raid-Helper signup and open the roster classification UI |
| `/roster attendance <tag> [start_date] [end_date]` | Admin | Generate an attendance leaderboard from saved records |

**`/roster generate` flow:**
1. Pass a Raid-Helper message ID or URL and a target channel.
2. An ephemeral dropdown appears listing all accepted and maybe players.
3. Select the players assigned to **Attack** — everyone else is placed in **Defense**.
4. Click **Confirm & Generate** to post the color-coded embed to the target channel.

**`/roster attendance` tags:** `<gvg_all>`, `<gvg_sat>`, `<gvg_sun>`, `<hero_realm>`, `<group_pvp>`, `<united_resolve>`, `<speedrun>`

---

### `/utility` — Channel maintenance

| Command | Access | Description |
|---|---|---|
| `/utility clean [target] [limit] [user]` | Admin | Delete messages from the current channel |
| `/utility archive <source> <destination> [tag] [start_date] [end_date]` | Admin | Forward Raid-Helper messages to an archive channel and save attendance data |

**`/utility clean` targets:** `all` (default), `bots`, `users`

**`/utility archive`** scans the source channel for Raid-Helper messages, forwards them to the destination, deletes the originals, and writes attendance records to `reports/<tag>.json`. These records are what power `/roster attendance`.

---

## Setup

### Requirements

- Python 3.11+
- A Discord bot token with the following intents enabled: **Guilds**, **Members**, **Messages**, **Message Content**

### Installation

```powershell
git clone https://github.com/KrapFey/evergale_bot
cd evergale_bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[all]
```

### Configuration

Copy `.env.example` to `.env` and fill in the values:

```
GUILD_ID=your_guild_id_here
MAGIC=your_bot_token_here
```

### Running

```powershell
# After installing:
evergale_bot

# Or directly:
python evergale_bot/evergale_bot.py
```

---

## Data

| Path | Content |
|---|---|
| `bosses.txt` | One boss name per line |
| `reports/<tag>.json` | Attendance records keyed by Unix timestamp |
| `app.log` | Runtime log with timestamps |
