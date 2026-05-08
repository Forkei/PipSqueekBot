# 🐭 PipSqueek

> *"dropping some Nujabes — Feather → Aruarian Dance → Luv Sic"*

PipSqueek is a self-hosted Discord music bot that people actually like. Not because it has the most features — because it has a personality. It's a DJ, not a jukebox. It reads the room, has opinions, reacts to songs, and hangs out in the server like a real person would.

It runs in two modes: **Mk1** for classic slash-style commands, and **Mk2** where it becomes a full AI agent powered by DeepSeek V3 — responding to natural language, queuing sets proactively, remembering what people like, and occasionally chiming in with a dry one-liner between songs.

---

## What makes it different

Most music bots are vending machines. You type `/play`, it plays. PipSqueek in Mk2 mode is more like having a friend who knows music running the aux:

- **It queues sets, not songs.** Ask for "some Fred again.." and it drops 4-5 tracks across eras with a callout: *"running a Fred set: Turn On The Lights → Jungle → Delilah"*
- **It reads who's in the call.** Pip knows who's listening, what they've liked before, and picks accordingly when the queue runs dry.
- **It has memory.** Tell it someone hates pop and it'll remember. Tell it to note that Tuesday nights get loud and it will.
- **It wakes up proactively.** When the queue gets low, Pip fires up and queues more based on taste. No dead air.
- **It DJs.** In DJ mode, it reacts to each song with a short comment as it starts — not cheesy, just *present*.
- **It talks back.** It's dry. It's warm. It has opinions. It will tell you if your pick is a weird call.

---

## Features

### Music
- YouTube search and direct URL playback
- Full queue controls: play, pause, resume, skip, stop, shuffle, loop, autoplay
- Queue entire YouTube playlists and albums in one command
- Lookahead pre-downloading (next 3 songs download in background while current plays)
- Local audio cache — replays are instant, no re-download
- Live volume control (takes effect immediately, not just on next track)
- Now-playing embed with progress bar, pause/skip buttons, and ❤️ reaction to like songs

### AI Agent (Mk2)
- Natural language — no commands needed, just talk to it
- Conversation memory per server (last 240 turns kept in context)
- Taste profiles — tracks what each user likes and requests
- Proactive wakeup when queue gets low — queues based on who's in the call
- DJ comments via DeepSeek between songs (optional, toggle with `pip dj`)
- Developer notes — Pip can `$leave_note()` when it notices something worth flagging
- Web search — can look up artist info, release dates, tour dates in real time
- Polls, reactions, and scheduled wakeups
- Recommendations via Llama 4 Scout — fast, taste-aware, JSON-reliable

### Playlists
- Create and share named playlists with 8-character IDs anyone can use
- Public/private toggle
- Spotify playlist and album import → YouTube (full match + unmatched report)
- Add now-playing to a playlist with one command

---

## Setup

### Prerequisites
- Python 3.11+
- [FFmpeg](https://ffmpeg.org/download.html) on your PATH

### Install
```bash
git clone https://github.com/forkei/PipSqueekBot
cd PipSqueekBot
pip install -r requirements.txt
```

### Configure `.env`

```bash
cp .env.example .env
```

```env
# Required
DISCORD_TOKEN=your_discord_bot_token

# Required for Mk2 AI agent + DJ comments
OPENROUTER_API_KEY=sk-or-your_key_here

# Optional — your Discord username, for owner bypass on skip/pause
OWNER_USERNAME=forkei

# Optional — for age-restricted YouTube content
YOUTUBE_COOKIES_FILE=cookies.txt

# Cache limits (defaults are fine)
CACHE_MAX_MB=2048
CACHE_MAX_SONGS=500
```

**Discord bot token** — [discord.com/developers/applications](https://discord.com/developers/applications) → New Application → Bot → Reset Token. Enable: `Message Content Intent`, `Voice States`. Invite scopes: `bot` + permissions: `Connect`, `Speak`, `Send Messages`, `Embed Links`, `Read Message History`, `Add Reactions`.

**OpenRouter key** — [openrouter.ai](https://openrouter.ai) — powers the Mk2 AI agent (DeepSeek V3) and DJ comments. Free tier works fine for personal use.


### Run
```bash
python bot.py
```

---

## Modes

### Mk1 — Classic Commands
Standard prefix commands (`pip play`, `pip skip`, etc.). Always available.

### Mk2 — AI Agent
Switch a server to Mk2 mode and PipSqueek becomes conversational. It watches the designated channel, responds to natural language, and runs the music proactively.

```
pip mode mk2        — activate in this channel
pip mode mk1        — switch back to commands
pip mode            — show current mode
```

Once in Mk2, just talk:
> "put on some late night stuff"
> "skip this"
> "what's in the queue?"
> "add this song to my chill playlist"
> "remember that olivia hates EDM"

---

## Commands

### Music

| Command | Description |
|---|---|
| `pip play <query or URL>` | Play a song — searches or takes a direct URL |
| `pip pause` / `pip resume` | Pause or resume |
| `pip skip` | Skip current song |
| `pip stop` | Stop and disconnect |
| `pip nowplaying` | Show current song with progress |
| `pip queue [page]` | Show the queue |
| `pip volume <0-100>` | Set volume (live, no restart) |
| `pip loop [off/one/all]` | Cycle or set loop mode |
| `pip autoplay` | Toggle autoplay of related songs |
| `pip shuffle` | Shuffle the queue |
| `pip remove <#>` | Remove a track from queue |
| `pip move <from> <to>` | Reorder the queue |
| `pip clear` | Clear the queue |
| `pip search <query>` | Pick from top 5 results |
| `pip album <URL> [shuffle]` | Queue an entire YouTube playlist/album |
| `pip dj` | Toggle DJ comment mode |

### Playlists

| Command | Description |
|---|---|
| `pip playlist create <name>` | Create a new playlist |
| `pip playlist list` | Your playlists |
| `pip playlist show <id>` | View tracks in a playlist |
| `pip playlist play <id> [shuffle]` | Play a playlist |
| `pip playlist add <id> <song>` | Add a song to a playlist |
| `pip playlist addcurrent <id>` | Add now-playing to a playlist |
| `pip playlist removesong <id> <#>` | Remove a track by position |
| `pip playlist delete <id>` | Delete a playlist |
| `pip playlist rename <id> <name>` | Rename a playlist |
| `pip playlist privacy <id> public/private` | Set access |
| `pip playlist share <id>` | Get the shareable ID |
| `pip playlist public` | Browse public playlists on the server |

### Spotify

| Command | Description |
|---|---|
| `pip spotify <url>` | Import Spotify playlist/album → save as server playlist |
| `pip spotify <url> play` | Import and queue immediately |
| `pip spotify <url> save <name>` | Import with a custom name |

### Agent

| Command | Description |
|---|---|
| `pip mode [mk1/mk2]` | Show or switch mode |
| `pip notes` | Read PipSqueek's developer notes |
| `pip notes clear` | Clear notes |
| `pip setchannel` | Set this as the Mk2 agent channel |

---

## How Mk2 works

PipSqueek runs a streaming ReAct loop on every message. It thinks in plain text, embeds tool calls inline (`$play_song("Nujabes - Feather")$`), gets results back immediately, and keeps going until it's done. No function-calling API needed — it's all text parsing against a regex.

The system prompt is the real config — it defines tone, decision rules, when to speak vs stay quiet, how to queue sets, what DJ mode means. Adjusting it changes how Pip behaves without touching code.

Tool calls it can make: play, skip, pause, resume, stop, volume, shuffle, loop, autoplay, clear queue, reorder, search, create playlists, play playlists, add to playlists, read history, store/retrieve/delete memory, web search, send messages, react, run polls, schedule proactive wakeups, leave developer notes.

---

## Project structure

```
PipSqueekBot/
├── bot.py                    # Entry point, cog loader
├── system_prompt.md          # PipSqueek's personality and rules
├── cogs/
│   ├── agent.py              # Mk2 streaming ReAct agent
│   ├── music.py              # Playback engine, queue, now-playing
│   ├── playlist.py           # Discord playlist management
│   ├── spotify_import.py     # Spotify → YouTube import
│   └── help.py               # Help command
├── utils/
│   ├── agent_tools.py        # All tool implementations
│   ├── agent_context.py      # Status string injected into system prompt
│   ├── ytdl.py               # yt-dlp wrapper, caching, pre-download
│   ├── database.py           # SQLite: playlists, history, memory, liked songs
│   ├── gemini.py             # DJ comment generation (OpenRouter)
│   ├── spotify.py            # Spotify API client
│   └── embeds.py             # Discord embed builders
├── data/
│   ├── cache/                # Cached audio files (opus)
│   └── db/                   # SQLite database
├── .env                      # Secrets (not committed)
├── .env.example
└── requirements.txt
```

---

## Tech

| Thing | What |
|---|---|
| Language model | DeepSeek V3 via OpenRouter |
| Recommendations | Llama 4 Scout via OpenRouter |
| DJ comments | DeepSeek V3 via OpenRouter |
| Audio | yt-dlp + FFmpeg, opus cache |
| Database | SQLite via aiosqlite |
| Discord | discord.py 2.x |

---

## Notes

- Audio is cached in `data/cache/` in opus format. Cache is pruned automatically by size and count (configurable in `.env`).
- Playlists live in SQLite at `data/db/pipsqueek.db`. Playlist IDs are 8-character uppercase codes — shareable as-is.
- The now-playing embed edits in place as the track progresses (every 10s), only resends if someone typed between messages.
- Skip and pause buttons are restricted to whoever queued the track (+ the server owner).
- ❤️ reaction on a now-playing embed likes the song and stores it in the user's taste profile, which Mk2 uses for recommendations.
