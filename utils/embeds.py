import discord
from utils.ytdl import format_duration

PINK = 0xFF69B4
GREEN = 0x00C853
RED = 0xFF3D00
BLUE = 0x2196F3
PURPLE = 0x9C27B0
GOLD = 0xFFD700


_BAR_WIDTH = 22


def _progress_bar(elapsed: float, duration: float, paused: bool = False) -> str:
    ratio = min(elapsed / duration, 1.0) if duration else 0.0
    pos = min(round(ratio * _BAR_WIDTH), _BAR_WIDTH - 1)
    bar = '━' * pos + '●' + '━' * (_BAR_WIDTH - 1 - pos)
    icon = '⏸' if paused else '▶'
    return f'{icon}  {format_duration(int(elapsed))}  {bar}  {format_duration(int(duration))}'


def now_playing(track: dict, requester: discord.Member = None,
                elapsed: float | None = None, paused: bool = False) -> discord.Embed:
    e = discord.Embed(title=track['title'], url=track.get('url'), color=PINK)
    e.set_author(name='Now Playing')
    if elapsed is not None and track.get('duration'):
        e.description = _progress_bar(elapsed, track['duration'], paused)
    if track.get('thumbnail'):
        e.set_thumbnail(url=track['thumbnail'])
    parts = []
    if track.get('duration') and elapsed is None:
        parts.append(format_duration(track['duration']))
    if track.get('uploader'):
        parts.append(track['uploader'])
    footer_text = ' · '.join(parts)
    if requester and hasattr(requester, 'display_name') and not getattr(requester, 'bot', False):
        footer_text += f' · {requester.display_name}'
        try:
            e.set_footer(text=footer_text, icon_url=requester.display_avatar.url)
        except Exception:
            e.set_footer(text=footer_text)
    elif footer_text:
        e.set_footer(text=footer_text)
    return e


def queued(track: dict, position: int, requester: discord.Member = None) -> discord.Embed:
    e = discord.Embed(title='➕ Added to Queue', description=f"**{track['title']}**", color=GREEN)
    if track.get('thumbnail'):
        e.set_thumbnail(url=track['thumbnail'])
    e.add_field(name='Position', value=f'#{position}', inline=True)
    if track.get('duration'):
        e.add_field(name='Duration', value=format_duration(track['duration']), inline=True)
    if requester:
        e.set_footer(text=f'Requested by {requester.display_name}', icon_url=requester.display_avatar.url)
    return e


def queue_list(tracks: list[dict], current: dict, page: int = 1, per_page: int = 10) -> discord.Embed:
    e = discord.Embed(title='📋 Queue', color=BLUE)
    if current:
        e.add_field(
            name='Now Playing',
            value=f"🎵 **{current['title']}** `{format_duration(current.get('duration', 0))}`",
            inline=False
        )
    if not tracks:
        e.add_field(name='Up Next', value='Queue is empty', inline=False)
        return e

    start = (page - 1) * per_page
    end = start + per_page
    chunk = tracks[start:end]
    lines = []
    for i, t in enumerate(chunk, start=start + 1):
        lines.append(f"`{i}.` **{t['title']}** `{format_duration(t.get('duration', 0))}`")

    total_pages = (len(tracks) + per_page - 1) // per_page
    e.add_field(name='Up Next', value='\n'.join(lines) or 'Nothing', inline=False)
    e.set_footer(text=f'Page {page}/{total_pages} • {len(tracks)} song(s) in queue')
    return e


def playlist_info(pl, tracks: list) -> discord.Embed:
    privacy = '🔒 Private' if not pl['is_public'] else '🌐 Public'
    e = discord.Embed(
        title=f"📁 {pl['name']}",
        description=f"**ID:** `{pl['id']}` • {privacy}",
        color=PURPLE
    )
    e.add_field(name='Owner', value=pl['owner_name'], inline=True)
    e.add_field(name='Tracks', value=str(len(tracks)), inline=True)

    if tracks:
        total_dur = sum(t['duration'] or 0 for t in tracks)
        e.add_field(name='Total Duration', value=format_duration(total_dur), inline=True)
        preview = '\n'.join(
            f"`{i}.` {t['title']} `{format_duration(t['duration'] or 0)}`"
            for i, t in enumerate(tracks[:10], 1)
        )
        if len(tracks) > 10:
            preview += f'\n*...and {len(tracks) - 10} more*'
        e.add_field(name='Tracks', value=preview, inline=False)
    return e


def error(message: str) -> discord.Embed:
    return discord.Embed(description=f'❌ {message}', color=RED)


def success(message: str) -> discord.Embed:
    return discord.Embed(description=f'✅ {message}', color=GREEN)


def info(message: str) -> discord.Embed:
    return discord.Embed(description=f'ℹ️ {message}', color=BLUE)
