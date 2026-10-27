"""
Pip's speaking voice (edge-tts) + a helper to play a one-off clip and wait for it.

Tune in .env: PIP_TTS_VOICE, PIP_TTS_RATE, PIP_TTS_PITCH.
"""
import asyncio
import os
import tempfile

import discord

try:
    import edge_tts
    TTS_OK = True
except ImportError:
    TTS_OK = False

from utils.persona import speakable

# Casual, dry young voice, pitched up a touch because he's a pipsqueak
VOICE = os.getenv('PIP_TTS_VOICE', 'en-US-BrianMultilingualNeural')
RATE = os.getenv('PIP_TTS_RATE', '+6%')
PITCH = os.getenv('PIP_TTS_PITCH', '+12Hz')


async def synthesize(text: str) -> str | None:
    """Render text to a temp mp3. Returns the file path (caller deletes) or None."""
    if not TTS_OK:
        return None
    text = speakable(text)
    if not text:
        return None
    try:
        communicate = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                buf.extend(chunk['data'])
        if not buf:
            return None
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            f.write(buf)
            return f.name
    except Exception as e:
        print(f'[tts] {type(e).__name__}: {e}')
        return None


async def play_and_wait(voice_client: discord.VoiceClient, path: str,
                        volume: float = 0.9, before_options: str | None = None,
                        max_seconds: float | None = None):
    """Play an audio file on voice_client and return when it ends (or after max_seconds)."""
    if not voice_client or not voice_client.is_connected():
        return
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(path, before_options=before_options), volume=volume
    )
    voice_client.play(source, after=lambda _e: loop.call_soon_threadsafe(done.set))
    try:
        await asyncio.wait_for(done.wait(), timeout=max_seconds)
    except asyncio.TimeoutError:
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
        await done.wait()


async def say(voice_client: discord.VoiceClient, text: str):
    """Speak a line in voice (blocking until done). No-op if TTS unavailable."""
    path = await synthesize(text)
    if not path:
        return
    try:
        await play_and_wait(voice_client, path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
