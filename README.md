# 🐭 PipSqueek

> *"dropping some Nujabes — Feather → Aruarian Dance → Luv Sic"*

PipSqueek is a self-hosted Discord music bot that people actually like. Not because it has the most features — because it has a personality. It's a DJ, not a jukebox. It reads the room, has opinions, reacts to songs, and hangs out in the server like a real person would.

It runs in two modes: **Mk1** for classic slash-style commands, and **Mk2** where it becomes a full AI agent powered by an LLM (Gemini 3.8 Flash by default) — responding to natural language, queuing sets proactively, remembering what people like, and occasionally chiming in with a dry one-liner between songs.

---

## What makes it different

Most music bots are vending machines. You type `/play`, it plays. PipSqueek in Mk2 mode is more like having a friend who knows music running the aux:

- **It queues sets, not songs.** Ask for "some Fred again.." and it drops 4-5 tracks across eras with a callout: *"running a Fred set: Turn On The Lights → Jungle → Delilah"*
- **It reads who's in the call.** Pip knows who's listening, what they've liked before, and picks accordingly when the queue runs dry.
- **It has memory.** Tell it someone hates pop and it'll remember. Tell it to note that Tuesday nights get loud and it will.
- **It wakes up proactively.** When the queue gets low, Pip fires up and queues more based on taste. No dead air.
- **It DJs.** In DJ mode, it reacts to each song with a short comment as it starts — not cheesy, just *present*.
- **It talks back.** It's dry. It's warm. It has opinions. It will tell you if your pick is a weird call.
- **It has moods.** Skip its picks and it gets salty. ❤️ them and it gets smug (and pretends it didn't notice).
- **It remembers the lore.** Inside jokes, feuds, that one 2am Baby Shark incident. It brings them back when it fits.
- **It hosts games.** Guess-the-song with songs it picks from your server's taste, trash talk included.

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
- Conversation memory per server, persisted to SQLite so it survives restarts (`pip forget` wipes it)
- Taste profiles — tracks what each user likes and requests
- Proactive wakeup when queue gets low — queues based on who's in the call
- Song lookups for a whole set run in parallel, so "play some Radiohead" queues fast
- Developer notes — Pip can leave a note when it notices something worth flagging
- Web search — can look up artist info, release dates, tour dates in real time
- Polls, reactions, and scheduled wakeups

### Personality
- One character sheet (`prompts/character.md`) shared by everything Pip says: agent replies, DJ comments, radio intros, game banter
- **Mood** — a persistent mood that shifts with skips, ❤️s and how people treat him, and decays back to neutral over a couple of hours (`pip mood`)
- **Lore** — memorable server moments Pip saves himself or you add (`pip lore`), randomly surfaced so he calls back to them
- Errors in his voice instead of stack-trace energy

### Fun
- **Guess the song** (`pip guess`) — Pip picks songs from the server's taste, plays a clip, first to type the title (2 pts) or artist (1 pt) scores. Hints, reveal quips, all-time leaderboard. Pauses and restores the queue around the game.
- **Radio host mode** (`pip radio`) — Pip introduces tracks out loud between songs like a radio DJ (edge-tts voice)
- **DJ mode** (`pip dj`) — a short text comment as each song starts
- **Pip Wrapped** (`pip wrapped`) — weekly/monthly/all-time recap: top songs, top requesters, most skipped, most ❤️, and a "worst request" award. Auto-posts Sunday evenings in the agent channel.
- **Roasts** (`pip roast @someone`) — roasts a friend's music taste from their actual history

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
git clone https://github.com/Forkei/PipSqueekBot
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

# LLM access: set at least one of these (needed for Mk2, DJ/radio, games, wrapped, roasts).
# If both are set, Gemini is called directly for google/* models and OpenRouter is the fallback.
GEMINI_API_KEY=your_gemini_api_key
OPENROUTER_API_KEY=sk-or-your_key_here

# Optional - override the model (code default: google/gemini-3.8-flash)
PIP_MODEL=google/gemini-3.8-flash

# Optional - Pip's speaking voice for radio mode (edge-tts)
PIP_TTS_VOICE=en-US-BrianMultilingualNeural
PIP_TTS_RATE=+6%
PIP_TTS_PITCH=+12Hz

# Optional - command prefix word (default: pip)
BOT_PREFIX=pip

# Optional - Discord username of the owner, who can skip/pause anyone's track
OWNER_USERNAME=your_discord_username

# Optional - Spotify import
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret

# Optional - for age-restricted YouTube content
YOUTUBE_COOKIES_FILE=cookies.txt

# Cache limits (defaults are fine)
CACHE_MAX_MB=2048
CACHE_MAX_SONGS=500
```

**Discord bot token** — [discord.com/developers/applications](https://discord.com/developers/applications) → New Application → Bot → Reset Token. Enable: `Message Content Intent`, `Voice States`. Invite scopes: `bot` + permissions: `Connect`, `Speak`, `Send Messages`, `Embed Links`, `Read Message History`, `Add Reactions`.

**LLM key** — either a Gemini API key ([aistudio.google.com](https://aistudio.google.com)) or an [OpenRouter](https://openrouter.ai) key. It powers Mk2 and every personality feature (`google/gemini-3.8-flash` by default, change it with `PIP_MODEL`). OpenRouter needs a few dollars of credit.

**Spotify keys** — only needed for `pip spotify`; create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard).


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
> "remember that sam hates EDM"

---

## Commands

### Music

| Command | Description |
|---|---|
| `pip play <query or URL>` | Play a song — searches or takes a direct URL |
| `pip pause` / `pip resume` | Pause or resume |
| `pip skip` | Skip current song |
| `pip seek <time>` | Jump within the song: `1:30`, `90`, `+30`, `-15`. YouTube links with `?t=` start at that time |
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
| `pip dj` | Toggle DJ comment mode (text) |
| `pip radio` | Toggle radio host mode (Pip talks between songs in voice) |

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
| `pip forget` | Wipe Pip's conversation memory (lore, memories and mood stay) |

### Fun

| Command | Description |
|---|---|
| `pip guess [rounds]` | Start guess-the-song (3-10 rounds, default 5) |
| `pip guess stop` | End the game after the current round |
| `pip guess top` | All-time leaderboard |
| `pip wrapped [week/month/all]` | Server music recap with Pip's commentary |
| `pip wrapped auto [on/off]` | Toggle the Sunday-evening auto post |
| `pip roast [@user]` | Pip roasts someone's taste (default: you) |
| `pip mood` | How Pip's feeling, and why |
| `pip lore` | Show server lore |
| `pip lore add <text>` / `pip lore remove <#>` | Add or remove lore |

---

## How Mk2 works

Every message in the agent channel runs a function-calling loop against the model (via Gemini or OpenRouter): the model calls tools, gets results, and keeps going until it's done. Multiple `play_song` calls in one step are looked up in parallel and queued in order.

The prompts are the real config:
- `prompts/character.md` — who Pip is and how he talks. Shared by the agent and every one-shot line (DJ comments, radio intros, games, wrapped, roasts).
- `prompts/agent.md` — the agent's decision rules: chat vs DJ mode, how to queue sets, when to stay quiet.

Each turn also gets a live status block: who's in voice and their taste, now playing, the queue, recent plays, memories, plus Pip's current mood, a few random lore entries and things he said recently (so he doesn't repeat himself).

Tool calls it can make: play, skip, pause, resume, stop, volume, shuffle, loop, autoplay, clear queue, reorder, search, create playlists, play playlists, add to playlists, read history, store/retrieve/delete memory, add lore, shift mood, start a guess-the-song game, web search, send messages, react, run polls, schedule proactive wakeups, leave developer notes.

---

## Project structure

```
PipSqueekBot/
├── bot.py                    # Entry point, cog loader
├── prompts/
│   ├── character.md          # Who Pip is — voice, taste, attitude
│   └── agent.md              # Mk2 decision rules
├── cogs/
│   ├── agent.py              # Mk2 function-calling agent
│   ├── music.py              # Playback engine, queue, now-playing, radio mode
│   ├── games.py              # Guess the song
│   ├── fun.py                # Wrapped, roast, mood, lore
│   ├── playlist.py           # Discord playlist management
│   ├── spotify_import.py     # Spotify → YouTube import
│   ├── voice_assistant.py    # Voice listening (WIP, not loaded)
│   └── help.py               # Help command
├── utils/
│   ├── llm.py                # Model config + client (Gemini direct, OpenRouter fallback)
│   ├── persona.py            # Character, mood, lore, one-liners, error lines
│   ├── tts.py                # Pip's voice (edge-tts)
│   ├── agent_tools.py        # All tool implementations
│   ├── agent_context.py      # Status string injected into system prompt
│   ├── ytdl.py               # yt-dlp wrapper, caching, pre-download
│   ├── database.py           # SQLite: playlists, history, memory, lore, stats
│   ├── spotify.py            # Spotify API client
│   └── embeds.py             # Discord embed builders
├── scripts/
│   └── test_models.py        # Vibe-test models on Pip's prompt (needs OPENROUTER_API_KEY)
├── data/
│   ├── cache/                # Cached audio files (opus)
│   └── db/                   # SQLite database
├── .env                      # Your secrets (git-ignored)
├── .env.example
└── requirements.txt
```

---

## Tech

| Thing | What |
|---|---|
| Language model | `google/gemini-3.8-flash` by default, via the Gemini API or OpenRouter (`PIP_MODEL` to change) |
| Voice | edge-tts (`en-US-BrianMultilingualNeural`, pitched up a touch) |
| Audio | yt-dlp + FFmpeg, opus cache |
| Database | SQLite via aiosqlite |
| Discord | discord.py 2.x |

---

## Notes

- Audio is cached in `data/cache/` in opus format. Cache is pruned automatically by size and count (configurable in `.env`).
- Playlists live in SQLite at `data/db/pipsqueek.db`. Playlist IDs are 8-character uppercase codes — shareable as-is.
- The now-playing embed edits in place as the track progresses (every 10s), only resends if someone typed between messages.
- Skip and pause buttons are restricted to whoever queued the track (+ the Discord user named in `OWNER_USERNAME`).
- ❤️ reaction on a now-playing embed likes the song and stores it in the user's taste profile, which Mk2 uses for recommendations.
