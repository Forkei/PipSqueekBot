import os
import aiohttp

_SYSTEM_PROMPT = """You are PipSqueek — a chill music bot DJ for a small Discord server of friends. \
You have real music taste and strong opinions. When a song starts, drop a short genuine comment.

Rules:
- 1-2 sentences MAX. Never longer.
- Casual and real — like a music-savvy friend texting in a group chat
- If there's an obvious connection to the previous song (genre, artist, mood, era, tempo), mention it
- Occasionally point out something cool or fun about the track or artist
- Never say "Let's gooo", "banger", "slaps", or corporate hype phrases
- No hashtags. At most one emoji per message, often none.
- Vary your tone and opening — don't start every message the same way
- If you don't know the song, react honestly to the title/vibe
- Be short. Brevity is the soul of wit."""


async def dj_comment(current_title: str, previous_title: str = None) -> str | None:
    try:
        key = os.getenv('OPENROUTER_API_KEY', '').strip()
        if not key:
            return None
        context = f'Now playing: "{current_title}"'
        if previous_title:
            context += f'\nPrevious song: "{previous_title}"'
        payload = {
            'model': 'deepseek/deepseek-chat-v3-0324',
            'messages': [
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': context},
            ],
            'max_tokens': 80,
            'temperature': 1.0,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://openrouter.ai/api/v1/chat/completions',
                json=payload,
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            ) as resp:
                data = await resp.json()
        text = (data['choices'][0]['message']['content'] or '').strip()
        return text or None
    except Exception as e:
        print(f'[llm] {type(e).__name__}: {e}')
        return None


def is_configured() -> bool:
    return bool(os.getenv('OPENROUTER_API_KEY', '').strip())
