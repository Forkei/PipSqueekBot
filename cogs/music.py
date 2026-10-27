import asyncio
import os
import random
import re
import time
import discord
from discord.ext import commands
from collections import deque
from utils import ytdl, embeds, persona
from utils.ytdl import format_duration

LOOKAHEAD_DEPTH = 3
OWNER_USERNAME = os.getenv('OWNER_USERNAME', '')
RADIO_TALK_CHANCE = 0.75  # radio mode: chance Pip talks before a given track


_TS_RE = re.compile(r'^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$')


def parse_timestamp(text: str) -> int | None:
    """'1:30', '1:02:03', '90', '90s', '2m10s' → seconds. None if unparseable."""
    text = text.strip().lower()
    if not text:
        return None
    if ':' in text:
        parts = text.split(':')
        if not all(p.isdigit() for p in parts) or len(parts) > 3:
            return None
        secs = 0
        for p in parts:
            secs = secs * 60 + int(p)
        return secs
    m = _TS_RE.match(text)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def start_from_url(url: str) -> int:
    """Read a YouTube ?t= / &start= timestamp from a URL (0 if none)."""
    m = re.search(r'[?&#](?:t|start)=([0-9hms:]+)', url)
    return (parse_timestamp(m.group(1)) or 0) if m else 0


def make_source(path: str, volume: float, start: float = 0) -> discord.AudioSource:
    before = f'-ss {start:.2f}' if start > 0 else None
    return discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(path, before_options=before), volume=volume)


def _is_pip(member) -> bool:
    return bool(member and getattr(member, 'bot', False))


def _label(member) -> str:
    if not member or not hasattr(member, 'display_name'):
        return '?'
    if member.display_name == member.name:
        return member.display_name
    return f'{member.display_name} (@{member.name})'


class NowPlayingView(discord.ui.View):
    def __init__(self, guild_id: int, bot: commands.Bot, requester_id: int | None = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.bot = bot
        self.requester_id = requester_id

    def _is_allowed(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        return (
            self.requester_id is None
            or user.id == self.requester_id
            or user.name == OWNER_USERNAME
        )

    @discord.ui.button(emoji='⏸️', style=discord.ButtonStyle.secondary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_allowed(interaction):
            await interaction.response.send_message('Only the person who queued this track can pause it.', ephemeral=True)
            return
        p = get_player(self.guild_id)
        if p.voice_client and p.voice_client.is_playing():
            p.voice_client.pause()
            p.record_pause()
            button.emoji = '▶️'
            paused = True
        elif p.voice_client and p.voice_client.is_paused():
            p.voice_client.resume()
            p.record_resume()
            button.emoji = '⏸️'
            paused = False
        else:
            await interaction.response.defer()
            return
        if p.current:
            embed = embeds.now_playing(p.current, p.current.get('requester'),
                                       elapsed=p.elapsed_seconds(), paused=paused)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.edit_message(view=self)

    @discord.ui.button(emoji='⏭️', style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._is_allowed(interaction):
            await interaction.response.send_message('Only the person who queued this track can skip it.', ephemeral=True)
            return
        p = get_player(self.guild_id)
        if p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()):
            p.voice_client.stop()
        await interaction.response.defer()

YES_WORDS = {'yes', 'yup', 'yeah', 'y', 'yep', 'sure', 'ok', 'okay', '1', 'first', 'this', 'that', 'it'}


class GuildPlayer:
    def __init__(self):
        self.queue: deque[dict] = deque()
        self.current: dict | None = None
        self.current_path: str | None = None  # cached audio file of the current track (for seeking)
        self.voice_client: discord.VoiceClient | None = None
        self.text_channel: discord.TextChannel | None = None
        self.loop_mode: str = 'off'
        self.autoplay: bool = False
        self.dj_mode: bool = False
        self.radio_mode: bool = False
        self.game_channel_id: int | None = None  # set while a guess-the-song game runs
        self.volume: float = 0.5
        self.play_start_time: float | None = None
        self.paused_duration: float = 0.0
        self.pause_start_time: float | None = None
        self.now_playing_msg: discord.Message | None = None
        self.last_human_msg_id: int = 0
        self._lock = asyncio.Lock()
        self._predownload_tasks: dict[str, asyncio.Task] = {}
        self._progress_task: asyncio.Task | None = None
        self._radio_prefetch: dict[str, asyncio.Task] = {}  # track url → intro line task

    def elapsed_seconds(self) -> float:
        if self.play_start_time is None:
            return 0.0
        t = time.time()
        pause_adj = (t - self.pause_start_time) if self.pause_start_time is not None else 0.0
        return max(0.0, t - self.play_start_time - self.paused_duration - pause_adj)

    def record_pause(self):
        if self.pause_start_time is None:
            self.pause_start_time = time.time()

    def record_resume(self):
        if self.pause_start_time is not None:
            self.paused_duration += time.time() - self.pause_start_time
            self.pause_start_time = None

    def kick_predownload(self):
        for track in list(self.queue)[:LOOKAHEAD_DEPTH]:
            vid_id = track.get('id')
            if vid_id and vid_id not in self._predownload_tasks:
                task = asyncio.create_task(ytdl.download_and_get_path(track['url'], vid_id))
                self._predownload_tasks[vid_id] = task

    def clear(self):
        if self._progress_task and not self._progress_task.done():
            self._progress_task.cancel()
        self._progress_task = None
        self.paused_duration = 0.0
        self.pause_start_time = None
        for task in self._predownload_tasks.values():
            if not task.done():
                task.cancel()
        self._predownload_tasks.clear()
        for task in self._radio_prefetch.values():
            if not task.done():
                task.cancel()
        self._radio_prefetch.clear()
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

    @staticmethod
    def _voice_cls():
        try:
            from discord.ext.voice_recv import VoiceRecvClient
            return VoiceRecvClient
        except ImportError:
            return discord.VoiceClient

    async def _ensure_voice(self, ctx: commands.Context) -> bool:
        if not ctx.author.voice:
            await ctx.send(embed=embeds.error(persona.line('not_in_voice')))
            return False
        if self._player(ctx.guild).game_channel_id:
            await ctx.send(embed=embeds.info("game in progress. music can wait"))
            return False
        p = self._player(ctx.guild)
        cls = self._voice_cls()
        if p.voice_client and p.voice_client.is_connected():
            if p.voice_client.channel != ctx.author.voice.channel:
                await p.voice_client.move_to(ctx.author.voice.channel)
        else:
            p.voice_client = await ctx.author.voice.channel.connect(cls=cls)
        return True

    def _after_play(self, guild_id: int, error=None):
        if error:
            print(f'[music] Player error: {error}')
        asyncio.run_coroutine_threadsafe(self._advance(guild_id), self.bot.loop)

    async def _record_if_skipped(self, guild_id: int, p: GuildPlayer):
        """Called as a track ends: if it ended well before its duration, it was skipped."""
        prev = p.current
        if not prev or not prev.get('duration') or p.loop_mode == 'one':
            return
        if p.elapsed_seconds() >= prev['duration'] - 10:
            return
        req = prev.get('requester')
        from utils.database import log_skip
        await log_skip(guild_id, getattr(req, 'id', None), _label(req), prev.get('id'), prev['title'])
        if _is_pip(req):
            await persona.shift_mood(guild_id, -0.15, f'someone skipped your pick "{prev["title"]}"')

    async def _advance(self, guild_id: int):
        p = get_player(guild_id)
        if p.game_channel_id:
            return  # the game owns the voice client right now
        try:
            await self._record_if_skipped(guild_id, p)
        except Exception as e:
            print(f'[music] skip tracking failed: {e}')
        do_autoplay = False
        async with p._lock:
            if p.loop_mode == 'one' and p.current:
                track = p.current
            elif p.loop_mode == 'all' and p.current:
                p.queue.append(p.current)
                track = p.queue.popleft() if p.queue else None
            else:
                track = p.queue.popleft() if p.queue else None

            if not track:
                do_autoplay = p.autoplay and p.current
                if not do_autoplay:
                    p.current = None
                    agent_cog = self.bot.cogs.get('Agent')
                    if agent_cog and agent_cog.get_mode(guild_id) == 'mk2':
                        agent_cog.schedule_auto_wakeup(guild_id, 2)
                    return

        if do_autoplay:
            await self._autoplay_next(guild_id, p)
            return

        p.kick_predownload()
        await self._play_track(guild_id, p, track)

    async def _autoplay_next(self, guild_id: int, p: GuildPlayer):
        if not p.current:
            return
        related = await ytdl.search_ytmusic(p.current['title'], limit=10)
        if not related:
            return
        pick = random.choice(related)
        guild = self.bot.get_guild(guild_id)
        track = {**pick, 'requester': guild.me if guild else None}
        await self._play_track(guild_id, p, track)

    async def _play_track(self, guild_id: int, p: GuildPlayer, track: dict):
        file_path = await ytdl.download_and_get_path(track['url'], track.get('id'))
        if not file_path:
            if p.text_channel:
                await p.text_channel.send(embed=embeds.error(persona.line('download_failed', title=track['title'])))
            await self._advance(guild_id)
            return

        if not p.voice_client or not p.voice_client.is_connected():
            return

        if p.radio_mode:
            await self._radio_intro(guild_id, p, track)
            if not p.voice_client or not p.voice_client.is_connected():
                return

        try:
            previous_title = p.current['title'] if p.current else None
            p._predownload_tasks.pop(track.get('id'), None)
            start = track.pop('start_at', 0) or 0  # one-shot: a looped replay starts from 0
            if track.get('duration') and start >= track['duration']:
                start = 0
            p.current = track
            p.current_path = file_path
            p.play_start_time = time.time() - start
            p.paused_duration = 0.0
            p.pause_start_time = None
            p.voice_client.play(make_source(file_path, p.volume, start),
                                after=lambda e: self._after_play(guild_id, e))
            self._schedule_song_end_wakeup(guild_id, p)

            if p.text_channel:
                requester_obj = track.get('requester')
                requester_id = requester_obj.id if isinstance(requester_obj, discord.Member) else None
                embed = embeds.now_playing(track, requester_obj, elapsed=float(start))
                # Edit in place if our message is still the most recent, otherwise resend silently
                edited = False
                old_msg = p.now_playing_msg
                if old_msg and p.last_human_msg_id <= old_msg.id:
                    try:
                        await old_msg.clear_reactions()
                        await old_msg.edit(embed=embed, view=NowPlayingView(guild_id, self.bot, requester_id))
                        edited = True
                    except Exception:
                        # clear_reactions failed (missing perms) or edit failed — fall through to resend
                        try:
                            await old_msg.delete()
                        except Exception:
                            pass
                        p.now_playing_msg = None
                if not edited:
                    if p.now_playing_msg:
                        try:
                            await p.now_playing_msg.delete()
                        except Exception:
                            pass
                    view = NowPlayingView(guild_id, self.bot, requester_id)
                    try:
                        p.now_playing_msg = await p.text_channel.send(embed=embed, view=view, silent=True)
                    except TypeError:
                        p.now_playing_msg = await p.text_channel.send(embed=embed, view=view)

            requester = track.get('requester')
            from utils.database import log_play
            if requester and isinstance(requester, discord.Member) and not requester.bot:
                uid = requester.id
                uname = requester.display_name if requester.display_name == requester.name else f'{requester.display_name} (@{requester.name})'
            else:
                guild_obj = self.bot.get_guild(guild_id)
                uid = guild_obj.me.id if guild_obj else 0
                uname = 'PipSqueek'
            asyncio.create_task(log_play(
                guild_id, uid, uname,
                track.get('id', ''), track['title'], track['url']
            ))

            if p._progress_task and not p._progress_task.done():
                p._progress_task.cancel()
            p._progress_task = asyncio.create_task(self._progress_loop(guild_id, p, track))

            if p.radio_mode:
                self._prefetch_radio_line(guild_id, p)
            elif p.dj_mode:
                asyncio.create_task(self._send_dj_comment(guild_id, p.text_channel, track, previous_title))
        except Exception as e:
            print(f'[music] Playback error: {type(e).__name__}: {e}')
            if p.text_channel:
                await p.text_channel.send(embed=embeds.error(persona.line('playback_failed')))

    # ─── Radio host mode ───────────────────────────────────────────────────────

    async def _make_radio_line(self, guild_id: int, track: dict, previous: dict | None) -> str | None:
        if previous and random.random() > RADIO_TALK_CHANCE:
            return None  # radio hosts don't talk before every single song
        req = track.get('requester')
        who = 'you picked it yourself' if _is_pip(req) else f'requested by {_label(req)}'
        task = (
            "You're on the mic between songs, like a radio host. "
            + (f'The song that just ended: "{previous["title"]}". ' if previous else "It's the start of the session. ")
            + f'Up next: "{track["title"]}" ({who}). '
            'Say ONE short spoken line (max 20 words) to bring it in. It will be read aloud: '
            'no emojis, no markdown, no links, no stage directions. Just the line.'
        )
        return await persona.one_liner(guild_id, task, max_tokens=80)

    def _prefetch_radio_line(self, guild_id: int, p: GuildPlayer):
        """Start writing the intro for the next queued track while the current one plays."""
        if not p.queue:
            return
        nxt = p.queue[0]
        url = nxt.get('url')
        if url and url not in p._radio_prefetch:
            p._radio_prefetch[url] = asyncio.create_task(
                self._make_radio_line(guild_id, nxt, p.current)
            )

    async def _radio_intro(self, guild_id: int, p: GuildPlayer, track: dict):
        from utils import tts
        task = p._radio_prefetch.pop(track.get('url'), None)
        try:
            if task:
                text = await asyncio.wait_for(task, timeout=8)
            else:
                text = await asyncio.wait_for(self._make_radio_line(guild_id, track, p.current), timeout=8)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            text = None
        if not text:
            return
        if p.text_channel:
            try:
                await p.text_channel.send(f'📻 {text}', silent=True)
            except Exception:
                pass
        try:
            await tts.say(p.voice_client, text)
        except Exception as e:
            print(f'[radio] TTS failed: {type(e).__name__}: {e}')

    def _schedule_song_end_wakeup(self, guild_id: int, p: GuildPlayer):
        """Mk2: wake the agent shortly before the current song ends."""
        duration = (p.current or {}).get('duration', 0)
        if duration <= 15:
            return
        remaining = duration - p.elapsed_seconds()
        agent_cog = self.bot.cogs.get('Agent')
        if agent_cog and agent_cog.get_mode(guild_id) == 'mk2':
            agent_cog.schedule_auto_wakeup(guild_id, max(5, int(remaining - duration * 0.05)))

    async def seek_to(self, guild_id: int, target: str) -> str:
        """Jump within the current track. target: '1:30', '90', '+30', '-15'. Returns a status string."""
        p = get_player(guild_id)
        vc = p.voice_client
        if not p.current or not p.current_path or not vc or not (vc.is_playing() or vc.is_paused()):
            return 'error: nothing playing'
        target = target.strip()
        relative = target[:1] in '+-'
        secs = parse_timestamp(target.lstrip('+-'))
        if secs is None:
            return f'error: couldn\'t read "{target}" as a time. use 1:30, 90, +30 or -15'
        now = p.elapsed_seconds()
        pos = (now + secs if target[0] == '+' else now - secs) if relative else secs
        duration = p.current.get('duration') or 0
        pos = max(0.0, pos)
        if duration and pos >= duration - 1:
            return f'error: that\'s past the end (track is {format_duration(duration)})'

        was_paused = vc.is_paused()
        old = vc.source
        vc.source = make_source(p.current_path, p.volume, pos)  # swaps in place, no "song ended" callback
        if old:
            old.cleanup()
        p.play_start_time = time.time() - pos
        p.paused_duration = 0.0
        p.pause_start_time = None
        if was_paused:
            vc.pause()
            p.record_pause()
        self._schedule_song_end_wakeup(guild_id, p)
        if p.now_playing_msg:
            try:
                await p.now_playing_msg.edit(embed=embeds.now_playing(
                    p.current, p.current.get('requester'), elapsed=pos, paused=was_paused))
            except Exception:
                pass
        return f'jumped to {format_duration(int(pos))} in "{p.current["title"]}"'

    @commands.command(name='seek', aliases=['jump', 'goto'])
    async def seek(self, ctx: commands.Context, *, position: str):
        """Jump within the current song. Usage: pip seek 1:30 | pip seek +30 | pip seek -15"""
        result = await self.seek_to(ctx.guild.id, position)
        if result.startswith('error:'):
            msg = result[6:].strip()
            await ctx.send(embed=embeds.error(persona.line('nothing_playing') if msg == 'nothing playing' else msg))
        else:
            await ctx.message.add_reaction('⏩')

    async def _progress_loop(self, guild_id: int, p: GuildPlayer, track: dict):
        track_url = track.get('url')
        try:
            while True:
                await asyncio.sleep(10)
                if not p.current or p.current.get('url') != track_url:
                    break
                if not p.now_playing_msg:
                    break
                elapsed = p.elapsed_seconds()
                paused = p.pause_start_time is not None
                embed = embeds.now_playing(p.current, p.current.get('requester'),
                                           elapsed=elapsed, paused=paused)
                try:
                    await p.now_playing_msg.edit(embed=embed)
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def _send_dj_comment(self, guild_id: int, channel, track: dict, previous_title: str = None):
        req = track.get('requester')
        task = (
            f'A new song just started: "{track["title"]}"'
            + (' (your own pick)' if _is_pip(req) else f' (requested by {_label(req)})') + '.'
            + (f' The previous song was "{previous_title}".' if previous_title else '')
            + ' Drop one short genuine comment about it, like a friend in the group chat. '
            'If there is an obvious connection to the previous song, you can mention it. '
            'Just the line, nothing else.'
        )
        comment = await persona.one_liner(guild_id, task, max_tokens=100)
        if comment and channel:
            await channel.send(f'🎙️ {comment}')

    # ──────────────────────────────── Commands ────────────────────────────────

    @commands.command(name='play', aliases=['p'])
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a song from YouTube. Accepts a URL or search query."""
        if not await self._ensure_voice(ctx):
            return

        p = self._player(ctx.guild)
        p.text_channel = ctx.channel

        async with ctx.typing():
            if query.startswith('http'):
                info = await ytdl.extract_info(query)
                if not info:
                    await ctx.send(embed=embeds.error('Could not load that URL.'))
                    return
                start = start_from_url(query)
                if start:
                    info['start_at'] = start
                await self._queue_or_play(ctx, info)
            else:
                results = await ytdl.search_ytmusic(query, limit=3)
                if not results:
                    await ctx.send(embed=embeds.error(f'Nothing found for `{query}`.'))
                    return

                if len(results) == 1:
                    await self._queue_or_play(ctx, results[0])
                    return

                lines = [
                    f"`{i}.` **{r['title']}** — {r.get('uploader', '?')} `{format_duration(r.get('duration', 0))}`"
                    for i, r in enumerate(results, 1)
                ]
                e = discord.Embed(
                    title=f'🔍 Results for "{query}"',
                    description='\n'.join(lines),
                    color=embeds.BLUE
                )
                e.set_footer(text='Reply with 1, 2, 3 — or yes / yup for the first one. Anything else cancels.')
                search_msg = await ctx.send(embed=e)

        if not query.startswith('http') and len(results) > 1:
            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel

            try:
                reply = await self.bot.wait_for('message', timeout=30.0, check=check)
            except asyncio.TimeoutError:
                await search_msg.edit(embed=embeds.info('Search timed out.'))
                return

            content = reply.content.strip().lower()

            if content in YES_WORDS:
                pick = results[0]
            elif content == '2' and len(results) >= 2:
                pick = results[1]
            elif content == '3' and len(results) >= 3:
                pick = results[2]
            elif content.isdigit() and 1 <= int(content) <= len(results):
                pick = results[int(content) - 1]
            else:
                await ctx.send(embed=embeds.info('Search cancelled.'))
                return

            await self._queue_or_play(ctx, pick)

    async def _queue_or_play(self, ctx: commands.Context, info: dict):
        track = {**info, 'requester': ctx.author}
        p = self._player(ctx.guild)
        p.text_channel = ctx.channel
        if p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()):
            p.queue.append(track)
            p.kick_predownload()
            await ctx.send(embed=embeds.queued(track, len(p.queue), ctx.author))
        else:
            async with p._lock:
                await self._play_track(ctx.guild.id, p, track)

    @commands.command(name='pause')
    async def pause(self, ctx: commands.Context):
        p = self._player(ctx.guild)
        if p.voice_client and p.voice_client.is_playing():
            p.voice_client.pause()
            p.record_pause()
            await ctx.send(embed=embeds.success('Paused.'))
        else:
            await ctx.send(embed=embeds.error(persona.line('nothing_playing')))

    @commands.command(name='resume', aliases=['unpause'])
    async def resume(self, ctx: commands.Context):
        p = self._player(ctx.guild)
        if p.voice_client and p.voice_client.is_paused():
            p.voice_client.resume()
            p.record_resume()
            await ctx.send(embed=embeds.success('Resumed.'))
        else:
            await ctx.send(embed=embeds.error('Nothing is paused.'))

    @commands.command(name='skip', aliases=['s', 'next'])
    async def skip(self, ctx: commands.Context):
        p = self._player(ctx.guild)
        if p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()):
            p.voice_client.stop()
            await ctx.send(embed=embeds.success('Skipped.'))
        else:
            await ctx.send(embed=embeds.error(persona.line('nothing_playing')))

    @commands.command(name='stop')
    async def stop(self, ctx: commands.Context):
        p = self._player(ctx.guild)
        if p.voice_client:
            p.clear()
            p.voice_client.stop()
            await p.voice_client.disconnect()
            p.voice_client = None
        await ctx.send(embed=embeds.success('Stopped and disconnected.'))

    @commands.command(name='queue', aliases=['q'])
    async def queue(self, ctx: commands.Context, page: int = 1):
        p = self._player(ctx.guild)
        await ctx.send(embed=embeds.queue_list(list(p.queue), p.current, page))

    @commands.command(name='nowplaying', aliases=['np', 'current'])
    async def nowplaying(self, ctx: commands.Context):
        p = self._player(ctx.guild)
        if p.current:
            paused = p.pause_start_time is not None or bool(p.voice_client and p.voice_client.is_paused())
            await ctx.send(embed=embeds.now_playing(p.current, p.current.get('requester'),
                                                    elapsed=p.elapsed_seconds(), paused=paused))
        else:
            await ctx.send(embed=embeds.info('Nothing is playing right now.'))

    @commands.command(name='volume', aliases=['vol'])
    async def volume(self, ctx: commands.Context, vol: int):
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
        p = self._player(ctx.guild)
        p.autoplay = not p.autoplay
        state = 'enabled' if p.autoplay else 'disabled'
        await ctx.send(embed=embeds.success(f'Autoplay **{state}**.'))

    @commands.command(name='dj')
    async def dj(self, ctx: commands.Context):
        """Toggle DJ mode — reacts to each song as it plays."""
        from utils.llm import is_configured
        if not is_configured():
            await ctx.send(embed=embeds.error(
                'DJ mode needs an LLM API key.\n'
                'Add `GEMINI_API_KEY` (or `OPENROUTER_API_KEY`) to your `.env`.'
            ))
            return
        p = self._player(ctx.guild)
        p.dj_mode = not p.dj_mode
        if p.dj_mode:
            await ctx.send(embed=embeds.success('🎙️ DJ mode **on** — I\'ll react to every song.'))
        else:
            await ctx.send(embed=embeds.success('DJ mode **off**.'))

    @commands.command(name='radio')
    async def radio(self, ctx: commands.Context):
        """Toggle radio host mode: Pip introduces tracks out loud between songs."""
        from utils.llm import is_configured
        from utils.tts import TTS_OK
        if not is_configured() or not TTS_OK:
            await ctx.send(embed=embeds.error(
                'Radio mode needs `GEMINI_API_KEY` in `.env` and `pip install edge-tts`.'
            ))
            return
        p = self._player(ctx.guild)
        p.radio_mode = not p.radio_mode
        p.text_channel = p.text_channel or ctx.channel
        if p.radio_mode:
            self._prefetch_radio_line(ctx.guild.id, p)
            await ctx.send(embed=embeds.success("📻 radio mode **on**. i'll be on the mic between songs"))
        else:
            await ctx.send(embed=embeds.success('📻 radio mode **off**. back to silent professionalism'))

    @commands.command(name='remove', aliases=['rm'])
    async def remove(self, ctx: commands.Context, index: int):
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
        p = self._player(ctx.guild)
        p.queue.clear()
        await ctx.send(embed=embeds.success('Queue cleared.'))

    @commands.command(name='move')
    async def move(self, ctx: commands.Context, from_pos: int, to_pos: int):
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
        p.text_channel = ctx.channel
        for t in tracks:
            p.queue.append({**t, 'requester': ctx.author})
        p.kick_predownload()

        playing_now = not (p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()))
        msg = f'Added **{len(tracks)} tracks** to the queue.'
        if 'shuffle' in flags.lower():
            msg += ' (shuffled)'
        await ctx.send(embed=embeds.success(msg))

        if playing_now and p.queue:
            async with p._lock:
                track = p.queue.popleft()
                await self._play_track(ctx.guild.id, p, track)

    @commands.command(name='join')
    async def join(self, ctx: commands.Context):
        await self._ensure_voice(ctx)
        await ctx.send(embed=embeds.success(f'Joined **{ctx.author.voice.channel.name}**.'))

    @commands.command(name='leave', aliases=['disconnect', 'dc'])
    async def leave(self, ctx: commands.Context):
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

    # ─── Track human messages for edit-in-place logic ─────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        p = get_player(message.guild.id)
        if p.text_channel and message.channel.id == p.text_channel.id:
            p.last_human_msg_id = message.id

    # ─── Liked songs via reaction ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id or not payload.guild_id:
            return
        p = get_player(payload.guild_id)
        if not p.now_playing_msg or p.now_playing_msg.id != payload.message_id:
            return

        if str(payload.emoji) != '❤️':
            return
        track = p.current
        if not track:
            return
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id) if guild else None
        if member:
            user_name = member.display_name if member.display_name == member.name else f'{member.display_name} (@{member.name})'
        else:
            user_name = str(payload.user_id)
        from utils.database import like_song
        await like_song(payload.user_id, user_name, track.get('id', ''), track['title'], track['url'])
        if _is_pip(track.get('requester')):
            await persona.shift_mood(payload.guild_id, 0.15, f'{user_name} ❤️\'d your pick "{track["title"]}"')

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id or not payload.guild_id:
            return
        if str(payload.emoji) != '❤️':
            return
        p = get_player(payload.guild_id)
        if not p.now_playing_msg or p.now_playing_msg.id != payload.message_id:
            return
        track = p.current
        if not track:
            return
        from utils.database import unlike_song
        await unlike_song(payload.user_id, track.get('id', ''))


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
