"""
Pip's persistent personality: character sheet, mood, lore, recent remarks,
and one-shot in-character lines (DJ comments, radio intros, game banter...).
"""
import datetime
import json
import os
import random
import re
import time
from collections import deque

from utils import llm

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'prompts')


def _load(name: str) -> str:
    with open(os.path.join(_PROMPTS_DIR, name), encoding='utf-8') as f:
        return f.read()


CHARACTER = _load('character.md')


# ─── Recent remarks (so Pip doesn't repeat himself across agent + one-shots) ───

_remarks: dict[int, deque] = {}


def remember_remark(guild_id: int, text: str):
    _remarks.setdefault(guild_id, deque(maxlen=12)).append(text[:200])


def recent_remarks(guild_id: int) -> list[str]:
    return list(_remarks.get(guild_id, []))


# ─── Mood ──────────────────────────────────────────────────────────────────────
# A single score in [-1, 1] that decays toward 0 (half-life 2h), plus the last
# few reasons it moved so Pip can reference them.

_MOOD_HALF_LIFE = 2 * 3600


async def _load_mood(guild_id: int) -> dict:
    from utils.database import kv_get
    raw = await kv_get(guild_id, 'mood')
    state = json.loads(raw) if raw else {'score': 0.0, 'updated_at': time.time(), 'reasons': []}
    elapsed = time.time() - state['updated_at']
    state['score'] *= 0.5 ** (elapsed / _MOOD_HALF_LIFE)
    state['updated_at'] = time.time()
    return state


async def shift_mood(guild_id: int, delta: float, reason: str):
    from utils.database import kv_set
    state = await _load_mood(guild_id)
    state['score'] = max(-1.0, min(1.0, state['score'] + delta))
    state['reasons'] = (state['reasons'] + [reason])[-4:]
    await kv_set(guild_id, 'mood', json.dumps(state))


def _mood_label(score: float) -> str:
    if score > 0.5:
        return 'smug and in your element'
    if score > 0.2:
        return 'in a good mood (hiding it badly)'
    if score > -0.2:
        return 'neutral, dry as usual'
    if score > -0.5:
        return 'a bit salty'
    return 'sulking'


def _time_of_day() -> str | None:
    hour = datetime.datetime.now().hour
    if 1 <= hour < 6:
        return "it's the middle of the night, you're sleepy and softer"
    if 6 <= hour < 10:
        return "it's early, you're not fully booted yet"
    return None


async def mood(guild_id: int) -> tuple[float, str, list[str]]:
    state = await _load_mood(guild_id)
    return state['score'], _mood_label(state['score']), state['reasons']


async def mood_line(guild_id: int) -> str:
    score, label, reasons = await mood(guild_id)
    line = f'Your mood right now: {label}. Let it color your tone.'
    if reasons and abs(score) > 0.1:
        line += (f' (Background, from earlier: {"; ".join(reasons[-3:])}. '
                 "Only bring these up if they're directly relevant, don't keep harping on them.)")
    tod = _time_of_day()
    if tod:
        line += f' Also, {tod}.'
    return line


# ─── Lore ──────────────────────────────────────────────────────────────────────

async def lore_sample(guild_id: int, n: int = 5) -> list[str]:
    from utils.database import list_lore
    rows = await list_lore(guild_id)
    picks = random.sample(list(rows), min(n, len(rows)))
    return [r['content'] for r in picks]


# ─── Personality block (shared by agent + one-shots) ──────────────────────────

async def personality_block(guild_id: int) -> str:
    parts = ['=== YOUR STATE ===', await mood_line(guild_id)]
    lore = await lore_sample(guild_id)
    if lore:
        parts.append('Server lore (call back to it only when it fits naturally):')
        parts.extend(f'  - {l}' for l in lore)
    remarks = recent_remarks(guild_id)
    if remarks:
        parts.append("Things you said recently (don't repeat them):")
        parts.extend(f'  - {r}' for r in remarks[-8:])
    return '\n'.join(parts)


async def one_liner(guild_id: int, task: str, max_tokens: int = 150) -> str | None:
    """Generate a short in-character line for a specific task. Records it as a remark."""
    system = CHARACTER + '\n\n' + await personality_block(guild_id)
    text = await llm.chat(system, task, max_tokens=max_tokens)
    if text:
        text = text.strip().strip('"')
        remember_remark(guild_id, text)
    return text


# ─── Speech cleanup ────────────────────────────────────────────────────────────

_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_URL_RE = re.compile(r'https?://\S+')
_EMOJI_RE = re.compile(
    '[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]+'
)


def speakable(text: str) -> str:
    """Strip markdown, links and emoji so TTS doesn't read them out."""
    text = _MD_LINK_RE.sub(r'\1', text)
    text = _URL_RE.sub('', text)
    text = _EMOJI_RE.sub('', text)
    text = re.sub(r'[*_`~>#|]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


# ─── Canned lines (errors etc.), for when an LLM call is too slow or failed ───

_LINES = {
    'crash': [
        'my brain just blue-screened. try that again',
        'something broke and i refuse to take responsibility. try again',
        "lost my train of thought. and the train. ask me again",
        'ok that one fried me. one more time?',
    ],
    'download_failed': [
        "youtube won't give me **{title}**. skipping it",
        "couldn't grab **{title}**. we move",
        '**{title}** refused to load. its loss',
    ],
    'not_in_voice': [
        "you're not in voice. hop in first",
        "i'd play it but you're not in a voice channel. that's on you",
        'get in a voice channel and ask again',
    ],
    'nothing_playing': [
        "nothing's playing. that's kind of the problem",
        'nothing on right now',
        "there's nothing to do that to",
    ],
    'missing_arg': [
        'you forgot the `{name}` part. `pip help` if you need it',
        'missing `{name}`. i need a little more to go on',
    ],
    'bad_arg': [
        "that's not a thing i understand. `pip help`?",
        "couldn't parse that. try again, slower",
    ],
    'playback_failed': [
        'playback broke. not my fault. mostly',
        'the audio gods said no. trying the next one',
    ],
}


def line(kind: str, **fmt) -> str:
    return random.choice(_LINES[kind]).format(**fmt)
