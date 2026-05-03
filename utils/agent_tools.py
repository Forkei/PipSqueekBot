"""
Tool implementations for the PipSqueek Mk2 agent.
"""
import asyncio
import random
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from discord.ext import commands


@dataclass
class ToolContext:
    guild: discord.Guild
    channel: discord.TextChannel
    author: discord.Member | None
    bot: 'commands.Bot'
    trigger_message: discord.Message | None = None
    _wakeup_scheduled: bool = field(default=False, init=False)
    _wakeup_seconds: int = field(default=0, init=False)
    _wakeup_cancel: bool = field(default=False, init=False)


# ─── Tool Implementations ──────────────────────────────────────────────────────

async def _ensure_voice(tc: ToolContext) -> tuple[bool, str]:
    """Join voice if needed. Returns (success, error_message)."""
    from cogs.music import get_player
    p = get_player(tc.guild.id)

    if tc.author and hasattr(tc.author, 'voice') and tc.author.voice:
        vc = tc.author.voice.channel
        if not p.voice_client or not p.voice_client.is_connected():
            p.voice_client = await vc.connect()
        elif p.voice_client.channel != vc:
            await p.voice_client.move_to(vc)
        p.text_channel = tc.channel
        return True, ''
    elif p.voice_client and p.voice_client.is_connected():
        p.text_channel = tc.channel
        return True, ''
    return False, 'no voice channel available'


_MAX_PLAY_DURATION = 600  # 10 minutes — skip mixes/compilations


async def play_song(tc: ToolContext, query: str) -> str:
    from cogs.music import get_player
    from utils import ytdl

    ok, err = await _ensure_voice(tc)
    if not ok:
        return f'error: {err}'

    p = get_player(tc.guild.id)
    p.text_channel = tc.channel

    if query.startswith('http'):
        info = await ytdl.extract_info(query)
        if info and info.get('duration', 0) > _MAX_PLAY_DURATION:
            return f'error: "{info["title"]}" is too long ({info["duration"]}s) — looks like a mix/compilation'
    else:
        results = await ytdl.search_ytmusic(query, limit=5)
        info = next((r for r in results if r.get('duration', 0) <= _MAX_PLAY_DURATION), None)
        if not info and results:
            info = min(results, key=lambda r: r.get('duration', 9999))

    if not info:
        return f'error: nothing found for "{query}"'

    # Dedup only for non-user-initiated plays (wakeups, autoplay)
    user_initiated = tc.author is not None and tc.trigger_message is not None
    if not user_initiated:
        from utils.database import get_play_history
        recent = await get_play_history(tc.guild.id, limit=20)
        recent_titles = {r['title'].lower() for r in recent}
        if info['title'].lower() in recent_titles:
            return f'skipped: "{info["title"]}" was recently played'

    track = {**info, 'requester': tc.author or tc.guild.me}

    music_cog = tc.bot.cogs.get('Music')
    if not music_cog:
        return 'error: music cog not loaded'

    async with p._lock:
        play_immediately = not (p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()))
        if not play_immediately:
            p.queue.append(track)
            p.kick_predownload()
            queue_pos = len(p.queue)

    if play_immediately:
        await music_cog._play_track(tc.guild.id, p, track)
        return f'playing: "{info["title"]}"'
    else:
        return f'queued: "{info["title"]}" at position #{queue_pos}'


async def skip_song(tc: ToolContext) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    if p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()):
        p.voice_client.stop()
        return 'skipped'
    return 'nothing playing'


async def pause_playback(tc: ToolContext) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    if p.voice_client and p.voice_client.is_playing():
        p.voice_client.pause()
        return 'paused'
    return 'nothing playing'


async def resume_playback(tc: ToolContext) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    if p.voice_client and p.voice_client.is_paused():
        p.voice_client.resume()
        return 'resumed'
    return 'not paused'


async def stop_and_leave(tc: ToolContext) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    if p.voice_client:
        p.clear()
        p.voice_client.stop()
        await p.voice_client.disconnect()
        p.voice_client = None
        return 'stopped and disconnected'
    return 'not in voice'


async def set_volume(tc: ToolContext, volume: int) -> str:
    from cogs.music import get_player
    volume = max(0, min(100, volume))
    p = get_player(tc.guild.id)
    p.volume = volume / 100
    return f'volume set to {volume}% (takes effect on next track)'


async def shuffle_queue(tc: ToolContext) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    if not p.queue:
        return 'queue is empty'
    q = list(p.queue)
    random.shuffle(q)
    p.queue = deque(q)
    return f'shuffled {len(q)} tracks'


async def set_loop_mode(tc: ToolContext, mode: str) -> str:
    from cogs.music import get_player
    mode = mode.lower()
    if mode not in ('off', 'one', 'all'):
        return 'error: mode must be off, one, or all'
    p = get_player(tc.guild.id)
    p.loop_mode = mode
    return f'loop set to {mode}'


async def toggle_autoplay(tc: ToolContext) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    p.autoplay = not p.autoplay
    return f'autoplay {"enabled" if p.autoplay else "disabled"}'


async def clear_queue(tc: ToolContext) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    count = len(p.queue)
    p.queue.clear()
    return f'cleared {count} tracks'


async def remove_from_queue(tc: ToolContext, position: int) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    if not p.queue or position < 1 or position > len(p.queue):
        return f'error: invalid position {position} (queue has {len(p.queue)} tracks)'
    q = list(p.queue)
    removed = q.pop(position - 1)
    p.queue = deque(q)
    return f'removed: "{removed["title"]}" from position #{position}'


async def move_in_queue(tc: ToolContext, from_pos: int, to_pos: int) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    q = list(p.queue)
    if not (1 <= from_pos <= len(q)) or not (1 <= to_pos <= len(q)):
        return f'error: invalid positions (queue has {len(q)} tracks)'
    track = q.pop(from_pos - 1)
    q.insert(to_pos - 1, track)
    p.queue = deque(q)
    return f'moved "{track["title"]}" to position #{to_pos}'


async def join_voice(tc: ToolContext) -> str:
    ok, err = await _ensure_voice(tc)
    if ok:
        from cogs.music import get_player
        p = get_player(tc.guild.id)
        channel_name = p.voice_client.channel.name if p.voice_client else '?'
        return f'joined {channel_name}'
    return f'error: {err}'


async def leave_voice(tc: ToolContext) -> str:
    from cogs.music import get_player
    p = get_player(tc.guild.id)
    if p.voice_client and p.voice_client.is_connected():
        p.clear()
        await p.voice_client.disconnect()
        p.voice_client = None
        return 'left voice'
    return 'not in voice'


async def get_queue(tc: ToolContext) -> str:
    from cogs.music import get_player
    from utils.ytdl import format_duration
    p = get_player(tc.guild.id)
    if not p.queue and not p.current:
        return 'queue is empty, nothing playing'
    lines = []
    if p.current:
        lines.append(f'NOW: {p.current["title"]} ({format_duration(p.current.get("duration", 0))})')
    for i, t in enumerate(p.queue, 1):
        lines.append(f'{i}. {t["title"]} ({format_duration(t.get("duration", 0))})')
    return '\n'.join(lines) or 'empty'


async def get_now_playing(tc: ToolContext) -> str:
    from cogs.music import get_player
    from utils.ytdl import format_duration
    p = get_player(tc.guild.id)
    if not p.current:
        return 'nothing playing'
    t = p.current
    req = t.get('requester')
    req_name = req.display_name if req and hasattr(req, 'display_name') else 'unknown'
    return (
        f'title: {t["title"]}\n'
        f'duration: {format_duration(t.get("duration", 0))}\n'
        f'requested by: {req_name}\n'
        f'loop: {p.loop_mode} | autoplay: {p.autoplay} | volume: {int(p.volume * 100)}%'
    )


async def search_songs(tc: ToolContext, query: str, limit: int = 3) -> str:
    from utils import ytdl
    from utils.ytdl import format_duration
    limit = max(1, min(5, limit))
    results = await ytdl.search_ytmusic(query, limit=limit)
    if not results:
        return f'no results for "{query}"'
    lines = [
        f'{i}. {r["title"]} — {r.get("uploader", "?")} ({format_duration(r.get("duration", 0))}) url:{r["url"]}'
        for i, r in enumerate(results, 1)
    ]
    return '\n'.join(lines)


async def create_playlist(tc: ToolContext, name: str) -> str:
    from utils.database import create_playlist as db_create
    if not tc.author:
        return 'error: no user context'
    playlist_id = await db_create(name, tc.author.id, tc.author.display_name, is_public=True)
    return f'created playlist "{name}" with ID {playlist_id}'


async def list_playlists(tc: ToolContext) -> str:
    from utils.database import get_public_playlists
    playlists = await get_public_playlists()
    if not playlists:
        return 'no playlists on this server'
    lines = [f'{p["id"]} — {p["name"]} by {p["owner_name"]} ({p["track_count"]} tracks)' for p in playlists]
    return '\n'.join(lines)


async def play_playlist(tc: ToolContext, playlist_id: str, shuffle: bool = False) -> str:
    from utils.database import get_playlist, get_playlist_tracks
    from cogs.music import get_player

    playlist = await get_playlist(playlist_id)
    if not playlist:
        return f'error: playlist {playlist_id} not found'

    tracks_db = await get_playlist_tracks(playlist_id)
    if not tracks_db:
        return f'playlist "{playlist["name"]}" is empty'

    ok, err = await _ensure_voice(tc)
    if not ok:
        return f'error: {err}'

    tracks = [
        {
            'id': None,
            'title': t['title'],
            'url': t['url'],
            'duration': t['duration'],
            'thumbnail': t['thumbnail'],
            'uploader': None,
            'requester': tc.author or tc.guild.me,
        }
        for t in tracks_db
    ]

    if shuffle:
        random.shuffle(tracks)

    p = get_player(tc.guild.id)
    p.text_channel = tc.channel
    was_idle = not (p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()))

    for t in tracks:
        p.queue.append(t)
    p.kick_predownload()

    if was_idle and p.queue:
        music_cog = tc.bot.cogs.get('Music')
        if music_cog:
            async with p._lock:
                track = p.queue.popleft()
                await music_cog._play_track(tc.guild.id, p, track)

    return f'queued {len(tracks)} tracks from "{playlist["name"]}"{"(shuffled)" if shuffle else ""}'


async def add_to_playlist(tc: ToolContext, playlist_id: str, query: str) -> str:
    from utils.database import get_playlist, add_track_to_playlist
    from utils import ytdl

    playlist = await get_playlist(playlist_id)
    if not playlist:
        return f'error: playlist {playlist_id} not found'

    if query.startswith('http'):
        info = await ytdl.extract_info(query)
    else:
        results = await ytdl.search_ytmusic(query, limit=1)
        info = results[0] if results else None

    if not info:
        return f'error: nothing found for "{query}"'

    user_id = tc.author.id if tc.author else 0
    user_name = tc.author.display_name if tc.author else 'Agent'
    await add_track_to_playlist(
        playlist_id, info['title'], info['url'],
        info.get('duration', 0), info.get('thumbnail'), user_id, user_name
    )
    return f'added "{info["title"]}" to playlist "{playlist["name"]}"'


async def get_recent_history(tc: ToolContext, limit: int = 10) -> str:
    from utils.database import get_play_history
    limit = max(1, min(50, limit))
    rows = await get_play_history(tc.guild.id, limit)
    if not rows:
        return 'no play history yet'
    lines = [f'{r["user_name"]}: {r["title"]}' for r in rows]
    return '\n'.join(lines)


async def get_user_history(tc: ToolContext, user_name: str, limit: int = 10) -> str:
    from utils.database import get_user_id_by_name, get_user_play_history, get_play_history
    limit = max(1, min(50, limit))
    user_id = await get_user_id_by_name(tc.guild.id, user_name)
    if user_id:
        rows = await get_user_play_history(tc.guild.id, user_id, limit)
    else:
        all_rows = await get_play_history(tc.guild.id, 200)
        rows = [r for r in all_rows if r['user_name'].lower() == user_name.lower()][:limit]
    if not rows:
        return f'no history for {user_name}'
    lines = [r['title'] for r in rows]
    return f"{user_name}'s recent plays:\n" + '\n'.join(lines)


async def store_memory(tc: ToolContext, key: str, value: str) -> str:
    from utils.database import store_memory as db_store, store_taste_profile
    await db_store(tc.guild.id, key, value)
    taste_keywords = ('likes', 'loves', 'hates', 'prefers', 'genre', 'mood', 'vibe', 'taste', 'favorite', 'fan')
    if tc.author and any(kw in key.lower() or kw in value.lower() for kw in taste_keywords):
        await store_taste_profile(tc.guild.id, tc.author.id, value)
    return f'stored: {key} = {value}'


async def retrieve_memory(tc: ToolContext, key: str) -> str:
    from utils.database import get_memory
    val = await get_memory(tc.guild.id, key)
    if val is None:
        return f'no memory found for key "{key}"'
    return f'{key} = {val}'


async def list_memories(tc: ToolContext) -> str:
    from utils.database import list_memories as db_list
    memories = await db_list(tc.guild.id)
    if not memories:
        return 'no memories stored'
    return '\n'.join(f'{k}: {v}' for k, v in memories)


async def schedule_wakeup(tc: ToolContext, seconds: int) -> str:
    seconds = max(60, min(3600, seconds))
    tc._wakeup_scheduled = True
    tc._wakeup_seconds = seconds
    return f'wakeup scheduled in {seconds}s'


async def cancel_wakeup(tc: ToolContext) -> str:
    tc._wakeup_scheduled = False
    tc._wakeup_cancel = True
    return 'wakeup cancelled'


async def send_message(tc: ToolContext, content: str) -> str:
    await tc.channel.send(content)
    return 'sent'


async def add_reaction(tc: ToolContext, emoji: str) -> str:
    if tc.trigger_message:
        try:
            await tc.trigger_message.add_reaction(emoji)
            return f'reacted with {emoji}'
        except discord.HTTPException:
            return 'reaction failed (invalid emoji?)'
    return 'no trigger message to react to'


async def poll(tc: ToolContext, question: str, options: str) -> str:
    option_list = [o.strip() for o in options.split(',') if o.strip()][:3]
    if len(option_list) < 2:
        return 'error: need at least 2 options'
    emojis = ['1️⃣', '2️⃣', '3️⃣']
    lines = [f'**{question}**']
    for emoji, opt in zip(emojis, option_list):
        lines.append(f'{emoji} {opt}')
    msg = await tc.channel.send('\n'.join(lines))
    for emoji in emojis[:len(option_list)]:
        await msg.add_reaction(emoji)
    return f'poll posted with {len(option_list)} options'


async def web_search(tc: ToolContext, query: str) -> str:
    import aiohttp
    url = 'https://api.duckduckgo.com/'
    params = {'q': query, 'format': 'json', 'no_html': '1', 'skip_disambig': '1'}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json(content_type=None)
        lines = []
        if data.get('AbstractText'):
            lines.append(data['AbstractText'])
        for r in data.get('RelatedTopics', [])[:5]:
            if isinstance(r, dict) and r.get('Text'):
                lines.append(r['Text'])
        return '\n'.join(lines) if lines else 'no results found'
    except Exception as e:
        return f'error: {type(e).__name__}: {e}'


async def done(tc: ToolContext) -> str:
    return 'done'


# ─── Dispatch table ────────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    'play_song': play_song,
    'skip_song': skip_song,
    'pause_playback': pause_playback,
    'resume_playback': resume_playback,
    'stop_and_leave': stop_and_leave,
    'set_volume': set_volume,
    'shuffle_queue': shuffle_queue,
    'set_loop_mode': set_loop_mode,
    'toggle_autoplay': toggle_autoplay,
    'clear_queue': clear_queue,
    'remove_from_queue': remove_from_queue,
    'move_in_queue': move_in_queue,
    'join_voice': join_voice,
    'leave_voice': leave_voice,
    'get_queue': get_queue,
    'get_now_playing': get_now_playing,
    'search_songs': search_songs,
    'create_playlist': create_playlist,
    'list_playlists': list_playlists,
    'play_playlist': play_playlist,
    'add_to_playlist': add_to_playlist,
    'get_recent_history': get_recent_history,
    'get_user_history': get_user_history,
    'store_memory': store_memory,
    'retrieve_memory': retrieve_memory,
    'list_memories': list_memories,
    'schedule_wakeup': schedule_wakeup,
    'cancel_wakeup': cancel_wakeup,
    'send_message': send_message,
    'add_reaction': add_reaction,
    'poll': poll,
    'web_search': web_search,
    'done': done,
}


async def dispatch(name: str, args: dict, tc: ToolContext) -> str:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return f'error: unknown tool "{name}"'
    try:
        return await handler(tc, **args)
    except Exception as e:
        print(f'[agent_tools] {name} failed: {type(e).__name__}: {e}')
        return f'error: {type(e).__name__}: {e}'
