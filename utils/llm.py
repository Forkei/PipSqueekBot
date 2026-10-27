"""
Single place for LLM config + a shared client.

Uses Google's Gemini API directly when GEMINI_API_KEY is set (via its
OpenAI-compatible endpoint), and falls back to OpenRouter (OPENROUTER_API_KEY)
if that's missing or a Gemini call fails.

Override the model with PIP_MODEL in .env (OpenRouter-style id, e.g. google/gemini-3.8-flash).
"""
import os
import aiohttp

MODEL = os.getenv('PIP_MODEL', 'google/gemini-3.8-flash').strip()

_GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'
_OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'

_session: aiohttp.ClientSession | None = None


def _gemini_key() -> str:
    return os.getenv('GEMINI_API_KEY', '').strip()


def _openrouter_key() -> str:
    return os.getenv('OPENROUTER_API_KEY', '').strip()


def is_configured() -> bool:
    return bool(_gemini_key() or _openrouter_key())


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    return _session


async def close():
    if _session and not _session.closed:
        await _session.close()


async def _post(url: str, key: str, payload: dict) -> dict:
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    async with _get_session().post(url, json=payload, headers=headers) as resp:
        data = await resp.json(content_type=None)
    if isinstance(data, list):  # Google sometimes wraps errors in a list
        data = data[0] if data else {}
    if 'error' in data:
        raise RuntimeError(f'{url.split("/")[2]}: {data["error"]}')
    return data['choices'][0]['message']


async def complete(
    messages: list,
    *,
    tools: list | None = None,
    max_tokens: int = 400,
    temperature: float = 1.0,
    effort: str = 'minimal',
) -> dict:
    """One chat completion. Returns the raw assistant message dict. Raises on API errors."""
    base = {'messages': messages, 'max_tokens': max_tokens, 'temperature': temperature}
    if tools:
        base['tools'] = tools

    gemini_error = None
    if _gemini_key() and MODEL.startswith('google/'):
        try:
            return await _post(_GEMINI_URL, _gemini_key(), {
                **base,
                'model': MODEL.removeprefix('google/'),
                # Gemini 3.8 Flash rejects 'minimal'; 'low' is its lightest level
                'reasoning_effort': 'low' if effort == 'minimal' else effort,
            })
        except Exception as e:
            gemini_error = e
            if not _openrouter_key():
                raise
            print(f'[llm] Gemini failed, falling back to OpenRouter: {e}')

    if not _openrouter_key():
        raise RuntimeError(gemini_error or 'no LLM API key configured')
    return await _post(_OPENROUTER_URL, _openrouter_key(), {
        **base,
        'model': MODEL,
        'reasoning': {'effort': effort},
    })


async def chat(system: str, user: str, *, max_tokens: int = 200, temperature: float = 1.0) -> str | None:
    """Simple one-shot text call. Returns None on any failure."""
    if not is_configured():
        return None
    try:
        msg = await complete(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            max_tokens=max_tokens, temperature=temperature,
        )
        return (msg.get('content') or '').strip() or None
    except Exception as e:
        print(f'[llm] {type(e).__name__}: {e}')
        return None
