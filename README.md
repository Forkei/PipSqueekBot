# 🐭 PipSqueek Bot

A self-hosted Discord music bot for friends. Plays YouTube audio, supports YouTube Music playlists & albums, lets you build shareable playlists right inside Discord, and can convert Spotify playlists to YouTube ones.

---

## Features

- **YouTube playback** — search by name or paste a URL
- **Full queue controls** — play, pause, resume, skip, stop, shuffle, loop, autoplay
- **Albums & playlists** — queue entire YouTube playlists/albums in one command
- **Discord playlists** — create, share, and manage playlists tied to your username, with public/private control
- **Spotify → YouTube** — paste a Spotify playlist or album link and it converts it automatically
- **Local audio cache** — downloaded tracks are cached on disk so replays are instant
- **Cookie support** — drop a `cookies.txt` to access age-restricted or premium content

---

## Setup

### 1. Prerequisites

- Python 3.11+
- [FFmpeg](https://ffmpeg.org/download.html) installed and on your PATH

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure `.env`

Copy the example and fill in your values:

```bash
cp .env.example .env
```

```env
# Required
DISCORD_TOKEN=your_discord_bot_token

# Optional — for Spotify import
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret

# Optional — for age-restricted YouTube content
YOUTUBE_COOKIES_FILE=cookies.txt

# Cache limits
CACHE_MAX_MB=2048
CACHE_MAX_SONGS=500
```

**Getting a Discord bot token:**
1. Go to https://discord.com/developers/applications
2. Create a new application → Bot → Reset Token
3. Enable: `Message Content Intent`, `Voice States`
4. Invite your bot with scopes: `bot` + permissions: `Connect`, `Speak`, `Send Messages`, `Embed Links`, `Read Message History`

**Spotify** works out of the box for public playlists — no credentials needed.

**Getting YouTube cookies (optional):**
- Install the browser extension **"Get cookies.txt LOCALLY"**
- Visit youtube.com while logged in, export cookies → save as `cookies.txt` in the project root

### 4. Run the bot

```bash
python bot.py
```

---

## Commands

### Music

| Command | Description |
|---|---|
| `!play <query or URL>` | Play a song (search or direct URL) |
| `!pause` / `!resume` | Pause or resume |
| `!skip` | Skip current song |
| `!stop` | Stop and disconnect |
| `!nowplaying` | Show current song |
| `!queue [page]` | Show queue |
| `!volume <0-100>` | Set volume |
| `!loop [off/one/all]` | Toggle loop mode |
| `!autoplay` | Toggle autoplay of related songs |
| `!shuffle` | Shuffle the queue |
| `!remove <#>` | Remove a song from queue |
| `!move <from> <to>` | Reorder queue |
| `!clear` | Clear the queue |
| `!search <query>` | Pick from top 5 search results |
| `!album <URL> [shuffle]` | Queue a YouTube playlist/album |

### Playlists

| Command | Description |
|---|---|
| `!playlist create <name>` | Create a new playlist |
| `!playlist list` | Your playlists |
| `!playlist show <id>` | View tracks in a playlist |
| `!playlist play <id> [shuffle]` | Play a playlist |
| `!playlist add <id> <song>` | Add a song to a playlist |
| `!playlist addcurrent <id>` | Add now-playing to a playlist |
| `!playlist removesong <id> <#>` | Remove a track |
| `!playlist delete <id>` | Delete a playlist |
| `!playlist rename <id> <name>` | Rename a playlist |
| `!playlist privacy <id> public/private` | Set privacy |
| `!playlist share <id>` | Get shareable ID |
| `!playlist public` | Browse public playlists |

### Spotify

| Command | Description |
|---|---|
| `!spotify <url>` | Import Spotify playlist/album → save as Discord playlist |
| `!spotify <url> play` | Import and queue immediately (no save) |
| `!spotify <url> save <name>` | Import with a custom name |

---

## Project Structure

```
PipSqueekBot/
├── bot.py                  # Entry point
├── cogs/
│   ├── music.py            # Playback, queue, loop, autoplay
│   ├── playlist.py         # Discord playlist management
│   ├── spotify_import.py   # Spotify → YouTube conversion
│   └── help.py             # Help command
├── utils/
│   ├── ytdl.py             # yt-dlp wrapper + caching
│   ├── database.py         # SQLite playlist storage
│   ├── spotify.py          # Spotify API client
│   └── embeds.py           # Discord embed helpers
├── data/
│   ├── cache/              # Downloaded audio files
│   └── db/                 # SQLite database
├── .env                    # Your secrets (not committed)
├── .env.example
└── requirements.txt
```

---

## Notes

- Audio files are cached in `data/cache/` using yt-dlp + FFmpeg (opus format). Cache is automatically pruned by size and count limits set in `.env`.
- Playlists are stored in a local SQLite database at `data/db/pipsqueek.db`.
- Playlist IDs are 8-character uppercase codes (e.g. `A1B2C3D4`) that anyone can use to play or view public playlists.
