import asyncio
import random
import discord
from discord.ext import commands
from collections import deque
from utils import ytdl, embeds

# Track dict shape: {id, title, url, duration, thumbnail, uploader, requester}


class GuildPlayer:
    def __init__(self):
        self.queue: deque[dict] = deque()
        self.current: dict | None = None
        self.voice_client: discord.VoiceClient | None = None
        self.loop_mode: str = 'off'   # 'off', 'one', 'all'
        self.autoplay: bool = False
        self.volume: float = 0.5
        self._lock = asyncio.Lock()

    def clear(self):
        self.queue.clear()
        self.current = None
        self.loop_mode = 'off'
        self.autoplay = False


_players: dict[int, GuildPlayer] = {}


def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in _players:
        _players[guild_id] = GuildPlayer()
    return _players[guild_id]


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _player(self, guild: discord.Guild) -> GuildPlayer:
        return get_player(guild.id)

    async def _ensure_voice(self, ctx: commands.Context) -> bool:
        if not ctx.author.voice:
            await ctx.send(embed=embeds.error('You must be in a voice channel.'))
            return False
        p = self._player(ctx.guild)
        if p.voice_client and p.voice_client.is_connected():
            if p.voice_client.channel != ctx.author.voice.channel:
                await p.voice_client.move_to(ctx.author.voice.channel)
        else:
            p.voice_client = await ctx.author.voice.channel.connect()
        return True

    def _after_play(self, ctx: commands.Context, error=None):
        if error:
            print(f'[music] Player error: {error}')
        asyncio.run_coroutine_threadsafe(self._advance(ctx), self.bot.loop)

    async def _advance(self, ctx: commands.Context):
        p = self._player(ctx.guild)
        async with p._lock:
            if p.loop_mode == 'one' and p.current:
                track = p.current
            elif p.loop_mode == 'all' and p.current:
                p.queue.append(p.current)
                track = p.queue.popleft() if p.queue else None
            else:
                track = p.queue.popleft() if p.queue else None

            if not track:
                if p.autoplay and p.current:
                    await self._autoplay_next(ctx, p)
                    return
                p.current = None
                return

            await self._play_track(ctx, p, track)

    async def _autoplay_next(self, ctx: commands.Context, p: GuildPlayer):
        if not p.current:
            return
        related = await ytdl.search_ytmusic(p.current['title'], limit=5)
        if not related:
            return
        pick = random.choice(related)
        track = {**pick, 'requester': ctx.guild.me}
        await self._play_track(ctx, p, track)

    async def _play_track(self, ctx: commands.Context, p: GuildPlayer, track: dict):
        file_path = await ytdl.download_and_get_path(track['url'], track.get('id'))
        if not file_path:
            await ctx.send(embed=embeds.error(f"Couldn't download **{track['title']}**. Skipping."))
            await self._advance(ctx)
            return

        p.current = track
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(file_path, options='-vn'),
            volume=p.volume
        )
        p.voice_client.play(source, after=lambda e: self._after_play(ctx, e))
        await ctx.send(embed=embeds.now_playing(track, track.get('requester')))

    # ──────────────────────────────── Commands ────────────────────────────────

    @commands.command(name='play', aliases=['p'])
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a song from YouTube. Accepts a URL or search query."""
        if not await self._ensure_voice(ctx):
            return

        async with ctx.typing():
            # Resolve track info
            if query.startswith('http'):
                info = await ytdl.extract_info(query)
            else:
                results = await ytdl.search_ytmusic(query, limit=1)
                info = results[0] if results else None

            if not info:
                await ctx.send(embed=embeds.error(f'Nothing found for `{query}`.'))
                return

            track = {**info, 'requester': ctx.author}
            p = self._player(ctx.guild)

            if p.voice_client.is_playing() or p.voice_client.is_paused():
                p.queue.append(track)
                await ctx.send(embed=embeds.queued(track, len(p.queue), ctx.author))
            else:
                async with p._lock:
                    await self._play_track(ctx, p, track)

    @commands.command(name='pause')
    async def pause(self, ctx: commands.Context):
        """Pause playback."""
        p = self._player(ctx.guild)
        if p.voice_client and p.voice_client.is_playing():
            p.voice_client.pause()
            await ctx.send(embed=embeds.success('Paused.'))
        else:
            await ctx.send(embed=embeds.error('Nothing is playing.'))

    @commands.command(name='resume', aliases=['unpause'])
    async def resume(self, ctx: commands.Context):
        """Resume playback."""
        p = self._player(ctx.guild)
        if p.voice_client and p.voice_client.is_paused():
            p.voice_client.resume()
            await ctx.send(embed=embeds.success('Resumed.'))
        else:
            await ctx.send(embed=embeds.error('Nothing is paused.'))

    @commands.command(name='skip', aliases=['s', 'next'])
    async def skip(self, ctx: commands.Context):
        """Skip the current song."""
        p = self._player(ctx.guild)
        if p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()):
            p.voice_client.stop()
            await ctx.send(embed=embeds.success('Skipped.'))
        else:
            await ctx.send(embed=embeds.error('Nothing to skip.'))

    @commands.command(name='stop')
    async def stop(self, ctx: commands.Context):
        """Stop playback, clear the queue, and leave voice."""
        p = self._player(ctx.guild)
        if p.voice_client:
            p.clear()
            p.voice_client.stop()
            await p.voice_client.disconnect()
            p.voice_client = None
        await ctx.send(embed=embeds.success('Stopped and disconnected.'))

    @commands.command(name='queue', aliases=['q'])
    async def queue(self, ctx: commands.Context, page: int = 1):
        """Show the current queue."""
        p = self._player(ctx.guild)
        await ctx.send(embed=embeds.queue_list(list(p.queue), p.current, page))

    @commands.command(name='nowplaying', aliases=['np', 'current'])
    async def nowplaying(self, ctx: commands.Context):
        """Show what's currently playing."""
        p = self._player(ctx.guild)
        if p.current:
            await ctx.send(embed=embeds.now_playing(p.current, p.current.get('requester')))
        else:
            await ctx.send(embed=embeds.info('Nothing is playing right now.'))

    @commands.command(name='volume', aliases=['vol'])
    async def volume(self, ctx: commands.Context, vol: int):
        """Set volume (0–100)."""
        if not 0 <= vol <= 100:
            await ctx.send(embed=embeds.error('Volume must be between 0 and 100.'))
            return
        p = self._player(ctx.guild)
        p.volume = vol / 100
        if p.voice_client and p.voice_client.source:
            p.voice_client.source.volume = p.volume
        await ctx.send(embed=embeds.success(f'Volume set to **{vol}%**.'))

    @commands.command(name='shuffle')
    async def shuffle(self, ctx: commands.Context):
        """Shuffle the queue."""
        p = self._player(ctx.guild)
        if not p.queue:
            await ctx.send(embed=embeds.error('Queue is empty.'))
            return
        q = list(p.queue)
        random.shuffle(q)
        p.queue = deque(q)
        await ctx.send(embed=embeds.success('Queue shuffled.'))

    @commands.command(name='loop')
    async def loop(self, ctx: commands.Context, mode: str = None):
        """Toggle loop: off / one / all"""
        p = self._player(ctx.guild)
        if mode is None:
            modes = ['off', 'one', 'all']
            p.loop_mode = modes[(modes.index(p.loop_mode) + 1) % len(modes)]
        else:
            mode = mode.lower()
            if mode not in ('off', 'one', 'all'):
                await ctx.send(embed=embeds.error('Mode must be `off`, `one`, or `all`.'))
                return
            p.loop_mode = mode
        icons = {'off': '🔁 off', 'one': '🔂 one', 'all': '🔁 all'}
        await ctx.send(embed=embeds.success(f'Loop set to **{icons[p.loop_mode]}**.'))

    @commands.command(name='autoplay', aliases=['ap'])
    async def autoplay(self, ctx: commands.Context):
        """Toggle autoplay (plays related songs when queue ends)."""
        p = self._player(ctx.guild)
        p.autoplay = not p.autoplay
        state = 'enabled' if p.autoplay else 'disabled'
        await ctx.send(embed=embeds.success(f'Autoplay **{state}**.'))

    @commands.command(name='remove', aliases=['rm'])
    async def remove(self, ctx: commands.Context, index: int):
        """Remove a song from the queue by its position."""
        p = self._player(ctx.guild)
        if not p.queue or index < 1 or index > len(p.queue):
            await ctx.send(embed=embeds.error('Invalid queue position.'))
            return
        q = list(p.queue)
        removed = q.pop(index - 1)
        p.queue = deque(q)
        await ctx.send(embed=embeds.success(f'Removed **{removed["title"]}** from queue.'))

    @commands.command(name='clear', aliases=['clearqueue'])
    async def clear_queue(self, ctx: commands.Context):
        """Clear the queue without stopping current song."""
        p = self._player(ctx.guild)
        p.queue.clear()
        await ctx.send(embed=embeds.success('Queue cleared.'))

    @commands.command(name='move')
    async def move(self, ctx: commands.Context, from_pos: int, to_pos: int):
        """Move a song in the queue from one position to another."""
        p = self._player(ctx.guild)
        q = list(p.queue)
        if not (1 <= from_pos <= len(q)) or not (1 <= to_pos <= len(q)):
            await ctx.send(embed=embeds.error('Invalid position(s).'))
            return
        track = q.pop(from_pos - 1)
        q.insert(to_pos - 1, track)
        p.queue = deque(q)
        await ctx.send(embed=embeds.success(f'Moved **{track["title"]}** to position #{to_pos}.'))

    @commands.command(name='album')
    async def album(self, ctx: commands.Context, url: str, *, flags: str = ''):
        """Add a YouTube playlist/album to the queue. Add `shuffle` to shuffle it."""
        if not await self._ensure_voice(ctx):
            return
        async with ctx.typing():
            tracks = await ytdl.extract_playlist(url)
        if not tracks:
            await ctx.send(embed=embeds.error('Could not load playlist/album.'))
            return

        if 'shuffle' in flags.lower():
            random.shuffle(tracks)

        p = self._player(ctx.guild)
        for t in tracks:
            p.queue.append({**t, 'requester': ctx.author})

        playing_now = not (p.voice_client.is_playing() or p.voice_client.is_paused())
        msg = f'Added **{len(tracks)} tracks** to the queue.'
        if 'shuffle' in flags.lower():
            msg += ' (shuffled)'
        await ctx.send(embed=embeds.success(msg))

        if playing_now and p.queue:
            async with p._lock:
                track = p.queue.popleft()
                await self._play_track(ctx, p, track)

    @commands.command(name='join')
    async def join(self, ctx: commands.Context):
        """Join your voice channel."""
        await self._ensure_voice(ctx)
        await ctx.send(embed=embeds.success(f'Joined **{ctx.author.voice.channel.name}**.'))

    @commands.command(name='leave', aliases=['disconnect', 'dc'])
    async def leave(self, ctx: commands.Context):
        """Leave the voice channel."""
        p = self._player(ctx.guild)
        if p.voice_client and p.voice_client.is_connected():
            p.clear()
            await p.voice_client.disconnect()
            p.voice_client = None
            await ctx.send(embed=embeds.success('Left voice channel.'))
        else:
            await ctx.send(embed=embeds.error("I'm not in a voice channel."))

    @commands.command(name='search')
    async def search(self, ctx: commands.Context, *, query: str):
        """Search YouTube and pick from top 5 results."""
        async with ctx.typing():
            results = await ytdl.search_ytmusic(query, limit=5)
        if not results:
            await ctx.send(embed=embeds.error('No results found.'))
            return

        from utils.ytdl import format_duration
        lines = [
            f"`{i}.` **{r['title']}** — {r.get('uploader', '?')} `{format_duration(r.get('duration', 0))}`"
            for i, r in enumerate(results, 1)
        ]
        e = discord.Embed(
            title=f'🔍 Search results for "{query}"',
            description='\n'.join(lines),
            color=embeds.BLUE
        )
        e.set_footer(text='Reply with a number (1–5) to play, or anything else to cancel.')
        await ctx.send(embed=e)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.strip().isdigit()

        try:
            msg = await self.bot.wait_for('message', timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await ctx.send(embed=embeds.info('Search cancelled.'))
            return

        pick = int(msg.content.strip()) - 1
        if not 0 <= pick < len(results):
            await ctx.send(embed=embeds.error('Invalid selection.'))
            return

        await ctx.invoke(self.play, query=results[pick]['url'])


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
