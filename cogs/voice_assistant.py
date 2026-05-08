"""
Voice assistant: listen to voice, STT → agent, optional TTS reply.

Setup:
  pip install discord-ext-voice-recv edge-tts
  Add to .env: GROQ_API_KEY=your_key   (for Whisper STT)

Commands:
  pip listen   — activate in current voice channel
  pip unlisten — deactivate
"""
import asyncio
import io
import os
import struct
import time
import tempfile
import wave
import aiohttp
import discord
from discord.ext import commands
from utils import embeds

# ─── Optional dependency guards ───────────────────────────────────────────────

try:
    import discord.ext.voice_recv as voice_recv
    from discord.ext.voice_recv import VoiceRecvClient
    VOICE_RECV_OK = True
except ImportError:
    VOICE_RECV_OK = False
    VoiceRecvClient = discord.VoiceClient  # fallback type alias only

try:
    import edge_tts
    EDGE_TTS_OK = True
except ImportError:
    EDGE_TTS_OK = False

# ─── Audio constants ──────────────────────────────────────────────────────────

_SAMPLE_RATE = 48000
_CHANNELS = 2
_SAMPLE_WIDTH = 2      # 16-bit PCM

# VAD config
_VOICE_RMS_THRESHOLD = 800    # amplitude out of 32768
_SILENCE_TIMEOUT = 1.5         # seconds of silence → end of utterance
_MAX_UTTERANCE_SECS = 12.0     # hard cap
_MIN_UTTERANCE_SECS = 0.4      # discard shorter clips (noise)

_TTS_VOICE = 'en-US-AvaMultilingualNeural'


# ─── Audio helpers ─────────────────────────────────────────────────────────────

def _rms(pcm_chunk: bytes) -> float:
    n = len(pcm_chunk) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack_from(f'<{n}h', pcm_chunk)
    return (sum(s * s for s in samples) / n) ** 0.5


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(_CHANNELS)
        wf.setsampwidth(_SAMPLE_WIDTH)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


# ─── Per-user audio buffer ────────────────────────────────────────────────────

class _UserBuffer:
    def __init__(self):
        self.chunks: list[bytes] = []
        self.speaking = False
        self.last_voice_t: float = 0.0
        self.speech_start_t: float = 0.0

    def feed(self, pcm: bytes) -> bytes | None:
        """
        Feed a PCM packet. Returns the accumulated utterance bytes if a complete
        utterance is detected (silence timeout or max length), else None.
        """
        now = time.monotonic()
        amp = _rms(pcm)

        if amp > _VOICE_RMS_THRESHOLD:
            if not self.speaking:
                self.speaking = True
                self.speech_start_t = now
                self.chunks = []
            self.chunks.append(pcm)
            self.last_voice_t = now
            return None

        if not self.speaking:
            return None

        # Still speaking — accumulate tail silence
        self.chunks.append(pcm)
        elapsed = now - self.speech_start_t
        silence = now - self.last_voice_t

        if silence >= _SILENCE_TIMEOUT or elapsed >= _MAX_UTTERANCE_SECS:
            self.speaking = False
            duration = self.last_voice_t - self.speech_start_t
            audio = b''.join(self.chunks)
            self.chunks = []
            if duration < _MIN_UTTERANCE_SECS:
                return None  # too short — discard (noise)
            return audio

        return None


# ─── Audio sink ───────────────────────────────────────────────────────────────

if VOICE_RECV_OK:
    class _ListenSink(voice_recv.AudioSink):
        """Routes completed utterances to the async callback."""

        def __init__(self, callback, loop: asyncio.AbstractEventLoop):
            super().__init__()
            self._callback = callback  # async (user, pcm_bytes) -> None
            self._buffers: dict[int, _UserBuffer] = {}
            self._loop = loop

        def wants_opus(self) -> bool:
            return False  # we want decoded PCM, not Opus

        def write(self, user, data):
            if user is None or user.bot:
                return
            uid = user.id
            if uid not in self._buffers:
                self._buffers[uid] = _UserBuffer()
            audio = self._buffers[uid].feed(data.pcm)
            if audio:
                asyncio.run_coroutine_threadsafe(
                    self._callback(user, audio), self._loop
                )

        def cleanup(self):
            self._buffers.clear()
else:
    _ListenSink = None  # type: ignore


# ─── STT via Groq Whisper ─────────────────────────────────────────────────────

async def _transcribe(pcm_bytes: bytes) -> str | None:
    key = os.getenv('GROQ_API_KEY', '').strip()
    if not key:
        return None

    wav_bytes = _pcm_to_wav(pcm_bytes)
    form = aiohttp.FormData()
    form.add_field(
        'file', wav_bytes,
        filename='audio.wav',
        content_type='audio/wav',
    )
    form.add_field('model', 'whisper-large-v3-turbo')
    form.add_field('response_format', 'text')
    form.add_field('language', 'en')

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://api.groq.com/openai/v1/audio/transcriptions',
                data=form,
                headers={'Authorization': f'Bearer {key}'},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    text = (await resp.text()).strip()
                    return text if text else None
                else:
                    body = await resp.text()
                    print(f'[voice] Groq STT {resp.status}: {body[:200]}')
    except Exception as e:
        print(f'[voice] STT error: {type(e).__name__}: {e}')
    return None


# ─── TTS via edge-tts ────────────────────────────────────────────────────────

async def _synthesize(text: str) -> bytes | None:
    if not EDGE_TTS_OK:
        return None
    try:
        communicate = edge_tts.Communicate(text, _TTS_VOICE)
        mp3_buf = bytearray()
        async for chunk in communicate.stream():
            if chunk['type'] == 'audio':
                mp3_buf.extend(chunk['data'])
        return bytes(mp3_buf) if mp3_buf else None
    except Exception as e:
        print(f'[voice] TTS error: {type(e).__name__}: {e}')
        return None


async def _play_tts(p, mp3_bytes: bytes):
    """Play TTS audio; ducks music while playing, restores after."""
    if not (p.voice_client and p.voice_client.is_connected()):
        return

    loop = asyncio.get_running_loop()
    original_vol = p.volume
    music_playing = p.voice_client.is_playing() or p.voice_client.is_paused()

    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        f.write(mp3_bytes)
        tmp_path = f.name

    try:
        if music_playing:
            # Duck music to 15% while TTS plays
            if p.voice_client.source:
                p.voice_client.source.volume = original_vol * 0.15

        tts_done = asyncio.Event()

        def _after(_err):
            loop.call_soon_threadsafe(tts_done.set)

        tts_source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(tmp_path),
            volume=0.9,
        )

        if not music_playing:
            p.voice_client.play(tts_source, after=_after)
            await tts_done.wait()
        else:
            # Can't mix audio streams in discord.py — skip audio, text already sent
            pass
    finally:
        if music_playing and p.voice_client and p.voice_client.source:
            p.voice_client.source.volume = original_vol
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ─── Cog ─────────────────────────────────────────────────────────────────────

class VoiceAssistant(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._listening: set[int] = set()   # guild_ids where listen is active
        self._sinks: dict[int, '_ListenSink'] = {}
        self._text_channels: dict[int, discord.TextChannel] = {}

    def is_listening(self, guild_id: int) -> bool:
        return guild_id in self._listening

    async def _on_utterance(self, guild_id: int, user: discord.Member, pcm: bytes):
        """Called when a complete utterance is captured from a user."""
        transcript = await _transcribe(pcm)
        if not transcript:
            return

        print(f'[voice] {user.display_name}: {transcript!r}')

        channel = self._text_channels.get(guild_id)
        if not channel:
            return

        # Show transcript in text channel
        await channel.send(f'🎙️ **{user.display_name}:** {transcript}')

        # Route through the agent (mk2 only — agent handles its own mode check)
        agent_cog = self.bot.cogs.get('Agent')
        if agent_cog:
            await agent_cog.run_from_voice(guild_id, channel, user, transcript)

    @commands.command(name='listen')
    async def listen_cmd(self, ctx: commands.Context):
        """Activate voice listening in your current voice channel."""
        if not VOICE_RECV_OK:
            await ctx.send(embed=embeds.error(
                'Voice receiving not installed.\n'
                'Run: `pip install discord-ext-voice-recv`'
            ))
            return

        if not os.getenv('GROQ_API_KEY', '').strip():
            await ctx.send(embed=embeds.error(
                'Voice assistant needs `GROQ_API_KEY` for speech recognition.\n'
                'Add it to your `.env` file.'
            ))
            return

        if not ctx.author.voice:
            await ctx.send(embed=embeds.error('You must be in a voice channel.'))
            return

        guild_id = ctx.guild.id

        if guild_id in self._listening:
            await ctx.send(embed=embeds.info('Already listening. Use `pip unlisten` to stop.'))
            return

        vc_channel = ctx.author.voice.channel

        # Connect or move; ensure we have a VoiceRecvClient (needed for receiving)
        from cogs.music import get_player
        p = get_player(guild_id)

        if p.voice_client and p.voice_client.is_connected():
            if not isinstance(p.voice_client, VoiceRecvClient):
                # Reconnect with receiving support
                await p.voice_client.disconnect(force=True)
                p.voice_client = await vc_channel.connect(cls=VoiceRecvClient)
            elif p.voice_client.channel != vc_channel:
                await p.voice_client.move_to(vc_channel)
        else:
            p.voice_client = await vc_channel.connect(cls=VoiceRecvClient)

        self._listening.add(guild_id)
        self._text_channels[guild_id] = ctx.channel

        loop = asyncio.get_running_loop()
        sink = _ListenSink(
            callback=lambda user, pcm: self._on_utterance(guild_id, user, pcm),
            loop=loop,
        )
        self._sinks[guild_id] = sink
        p.voice_client.listen(sink)

        tts_note = ' • TTS replies active' if EDGE_TTS_OK else ''
        await ctx.send(embed=embeds.success(
            f'👂 Now listening in **{vc_channel.name}**.\n'
            f'Transcripts and agent responses appear here{tts_note}.\n'
            f'Say `pip unlisten` to stop.'
        ))

    @commands.command(name='unlisten', aliases=['stoplisten', 'deaf'])
    async def unlisten_cmd(self, ctx: commands.Context):
        """Stop voice listening."""
        guild_id = ctx.guild.id

        if guild_id not in self._listening:
            await ctx.send(embed=embeds.info('Not currently listening.'))
            return

        self._listening.discard(guild_id)
        self._text_channels.pop(guild_id, None)

        from cogs.music import get_player
        p = get_player(guild_id)
        if p.voice_client and isinstance(p.voice_client, VoiceRecvClient):
            p.voice_client.stop_listening()
        sink = self._sinks.pop(guild_id, None)
        if sink:
            sink.cleanup()

        await ctx.send(embed=embeds.success('Stopped listening.'))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """TTS any bot response when listen mode is active in that channel."""
        if not EDGE_TTS_OK:
            return
        if message.author.id != self.bot.user.id:
            return
        if not message.guild:
            return
        guild_id = message.guild.id
        if guild_id not in self._listening:
            return
        if message.channel.id != self._text_channels.get(guild_id, 0):
            return
        # Don't TTS the transcript echo (starts with 🎙️)
        if message.content.startswith('🎙️'):
            return
        text = message.content
        if not text:
            return
        mp3 = await _synthesize(text)
        if mp3:
            from cogs.music import get_player
            p = get_player(guild_id)
            await _play_tts(p, mp3)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Auto-deactivate listen mode if bot is disconnected from voice."""
        if member != self.bot.user:
            return
        guild_id = member.guild.id
        if guild_id not in self._listening:
            return
        if after.channel is None:
            # Bot was disconnected
            self._listening.discard(guild_id)
            self._text_channels.pop(guild_id, None)
            self._sinks.pop(guild_id, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceAssistant(bot))
