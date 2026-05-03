"""
Builds the status string injected into the system message before each LLM call.
"""
import time
import discord
from utils.ytdl import format_duration


def _voice_summary(guild: discord.Guild) -> str:
    lines = []
    for vc in guild.voice_channels:
        members = [m.display_name for m in vc.members if not m.bot]
        if members:
            lines.append(f'  #{vc.name}: {", ".join(members)}')
    return '\n'.join(lines) if lines else '  (empty)'


async def build_context(guild: discord.Guild, author: discord.Member | None = None) -> str:
    from cogs.music import get_player
    from utils.database import get_play_history, list_memories, get_taste_profile, get_liked_songs

    p = get_player(guild.id)
    parts = []

    parts.append(f'=== STATUS: {guild.name} | {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())} ===')

    parts.append('\nVoice channels with people:')
    parts.append(_voice_summary(guild))

    # Per-user taste data for everyone currently in voice
    if p.voice_client:
        vc_members = [m for m in p.voice_client.channel.members if not m.bot]
        if vc_members:
            parts.append('\nListeners taste:')
            for member in vc_members:
                liked = await get_liked_songs(guild.id, user_id=member.id, limit=20)
                profile = await get_taste_profile(guild.id, member.id)
                liked_titles = [r['title'] for r in liked]
                line = f'  {member.display_name}'
                if liked_titles:
                    line += f' — ❤️ {", ".join(liked_titles[:10])}'
                    if len(liked_titles) > 10:
                        line += f' (+{len(liked_titles)-10} more)'
                if profile:
                    line += f' | {profile}'
                if not liked_titles and not profile:
                    line += ' — no history yet'
                parts.append(line)

    if p.current:
        req = p.current.get('requester')
        req_name = req.display_name if req and hasattr(req, 'display_name') else '?'
        parts.append(
            f'\nNow playing: "{p.current["title"]}" '
            f'({format_duration(p.current.get("duration", 0))}) '
            f'— requested by {req_name}'
        )
    else:
        parts.append('\nNothing playing.')

    parts.append(f'Queue: {len(p.queue)} tracks | Loop: {p.loop_mode} | Autoplay: {p.autoplay} | Volume: {int(p.volume * 100)}%')

    if p.queue:
        next_tracks = list(p.queue)[:5]
        queue_lines = []
        for i, t in enumerate(next_tracks):
            req = t.get('requester')
            req_name = req.display_name if req and hasattr(req, 'display_name') else '?'
            queue_lines.append(f'  {i+1}. {t["title"]} (req: {req_name})')
        if len(p.queue) > 5:
            queue_lines.append(f'  ... and {len(p.queue) - 5} more')
        from collections import Counter
        user_counts = Counter(
            t['requester'].display_name
            for t in p.queue
            if t.get('requester') and hasattr(t['requester'], 'display_name')
        )
        if len(user_counts) > 1:
            queue_lines.append('  Queue breakdown: ' + ', '.join(f'{u}: {c}' for u, c in user_counts.most_common()))
        parts.append('Up next:\n' + '\n'.join(queue_lines))

    history = await get_play_history(guild.id, limit=10)
    if history:
        parts.append('\nRecent plays:')
        for row in history:
            parts.append(f'  {row["user_name"]}: {row["title"]}')

    memories = await list_memories(guild.id)
    if memories:
        parts.append(f'\nYour notes ({len(memories)} total, showing up to 50 most recent):')
        for key, val in memories[:50]:
            parts.append(f'  {key}: {val}')

    if author and not author.bot:
        profile = await get_taste_profile(guild.id, author.id)
        if profile:
            parts.append(f'\nTaste profile for {author.display_name}: {profile}')

    return '\n'.join(parts)
