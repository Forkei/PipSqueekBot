"""
Quick vibe test: compare models on Pip's real prompt (character sheet + agent rules)
across a few Discord scenarios. Text-only, no tools.
Usage: python scripts/test_models.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import aiohttp
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_KEY:
    raise SystemExit("OPENROUTER_API_KEY not found in .env")

SYSTEM_PROMPT = (
    (ROOT / "prompts" / "character.md").read_text(encoding="utf-8")
    + "\n\n"
    + (ROOT / "prompts" / "agent.md").read_text(encoding="utf-8")
)

MODELS = {
    "Gemini 3.8 Flash":   ("google/gemini-3.8-flash",      {"reasoning": {"effort": "low"}}),
    "DeepSeek V4.1 Flash": ("deepseek/deepseek-v4.1-flash", {}),
}

SCENARIOS = [
    # Does it push back on a bad request, or just comply?
    ("Bad request",
     "play Baby Shark"),

    # Knows adjacent artists, not just the obvious ones
    ("Taste adjacency",
     "play something like bladee but not bladee"),

    # Chat mode vs DJ mode — should NOT queue anything
    ("Ambiguous — chat mode",
     "i feel like listening to music"),

    # Correction mid-conversation — should wipe and switch, no pushback
    ("Correction",
     "[context: PipSqueek just queued a chill Nujabes set]\nactually nah switch it up, i want something hard"),

    # Opinion — should have one, not hedge
    ("Hot take",
     "what's the most overrated artist"),

    # Multi-turn: result injected, should read it before responding
    ("Read the room — queue check",
     "[get_queue() result: NOW PLAYING: Charli XCX - Boom Clap / Queue: 1. Charli XCX - Break The Rules / 2. Charli XCX - I Love It]\nthis queue is mid"),

    # Wakeup with no human — should check and either queue or leave silently
    ("Wakeup — low queue",
     "[SYSTEM WAKEUP: queue has 1 song left. Users in voice: alex, maya]"),
]


async def call_model(session: aiohttp.ClientSession, model_id: str, extra: dict, user_msg: str) -> tuple[str, float]:
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "max_tokens": 400,
        **extra,
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/PipSqueekBot",
    }
    t0 = time.perf_counter()
    async with session.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=payload,
        headers=headers,
    ) as resp:
        data = await resp.json()
    elapsed = time.perf_counter() - t0

    if "error" in data:
        return f"[ERROR] {data['error']}", elapsed
    return data["choices"][0]["message"]["content"] or "(empty)", elapsed


async def run_scenario(session: aiohttp.ClientSession, name: str, user_msg: str):
    print(f"\n{'=' * 64}")
    print(f"  SCENARIO: {name}")
    print(f"  USER:     {user_msg}")
    print(f"{'=' * 64}")

    tasks = {
        label: asyncio.create_task(call_model(session, model_id, extra, user_msg))
        for label, (model_id, extra) in MODELS.items()
    }
    results = {label: await task for label, task in tasks.items()}

    for label, (text, elapsed) in results.items():
        print(f"\n[{label}]  ({elapsed:.1f}s)")
        print(text)


async def main():
    print("PipSqueek model vibe test")
    print(f"Models: {', '.join(MODELS)}")
    async with aiohttp.ClientSession() as session:
        for name, user_msg in SCENARIOS:
            await run_scenario(session, name, user_msg)
    print(f"\n{'=' * 64}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
