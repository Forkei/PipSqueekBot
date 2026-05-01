"""
Builds the context string passed to Gemini at the start of each agent call.
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


async def build_context(
    guild: discord.Guild,
    author: discord.Member | None,
    message: discord.Message | None,
    wakeup_reason: str | None = None,
) -> str:
    from cogs.music import get_player
    from utils.database import (
        get_play_history, list_memories, get_taste_profile, get_conversation_history
    )

    p = get_player(guild.id)
    parts = []

    # Server state
    parts.append(f'=== SERVER: {guild.name} ===')
    parts.append(f'Time: {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}')

    # Voice state
    parts.append('\nVoice channels with people:')
    parts.append(_voice_summary(guild))

    # Music state
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
        # Per-user queue counts
        from collections import Counter
        user_counts = Counter(
            t['requester'].display_name
            for t in p.queue
            if t.get('requester') and hasattr(t['requester'], 'display_name')
        )
        if len(user_counts) > 1:
            queue_lines.append('  Queue breakdown: ' + ', '.join(f'{u}: {c}' for u, c in user_counts.most_common()))
        parts.append('Up next:\n' + '\n'.join(queue_lines))

    # Recent play history
    history = await get_play_history(guild.id, limit=10)
    if history:
        parts.append('\nRecent plays:')
        for row in history:
            parts.append(f'  {row["user_name"]}: {row["title"]}')

    # Agent memories (most recently updated first, up to 20)
    memories = await list_memories(guild.id)
    if memories:
        parts.append(f'\nYour notes ({len(memories)} total, showing up to 50 most recent):')
        for key, val in memories[:50]:
            parts.append(f'  {key}: {val}')

    # Taste profile for current user
    if author and not author.bot:
        profile = await get_taste_profile(guild.id, author.id)
        if profile:
            parts.append(f'\nTaste profile for {author.display_name}: {profile}')

    # Conversation history
    conv = await get_conversation_history(guild.id, limit=10)
    if conv:
        parts.append('\nRecent conversation:')
        for row in conv:
            who = row['author_name'] or row['role']
            parts.append(f'  [{who}]: {row["content"][:200]}')

    # Trigger event
    parts.append('\n=== CURRENT EVENT ===')
    if wakeup_reason:
        parts.append(f'WAKEUP — you scheduled this to check in. Reason: {wakeup_reason}')
        parts.append('Decide what (if anything) to do. Call done() if nothing is needed.')
    elif message and author:
        parts.append(f'{author.display_name} says: {message.content}')
    elif message:
        parts.append(f'Message: {message.content}')
    else:
        parts.append('(no specific trigger)')

    return '\n'.join(parts)
