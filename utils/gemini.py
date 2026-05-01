import os
from google import genai
from google.genai import types

_client = None

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


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv('GEMINI_API_KEY', '').strip()
        if not api_key:
            raise RuntimeError('GEMINI_API_KEY not set in .env')
        _client = genai.Client(api_key=api_key)
    return _client


async def dj_comment(current_title: str, previous_title: str = None) -> str | None:
    try:
        client = _get_client()
        context = f'Now playing: "{current_title}"'
        if previous_title:
            context += f'\nPrevious song: "{previous_title}"'

        response = await client.aio.models.generate_content(
            model='gemini-3.1-flash-lite-preview',
            contents=context,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                max_output_tokens=80,
                temperature=1.0,
            )
        )
        text = response.text.strip() if response.text else None
        return text
    except Exception as e:
        print(f'[gemini] {type(e).__name__}: {e}')
        return None


def is_configured() -> bool:
    return bool(os.getenv('GEMINI_API_KEY', '').strip())
