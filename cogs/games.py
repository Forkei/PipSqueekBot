"""
Guess the song: Pip picks tracks from the server's taste, plays a clip,
first to type the title (2 pts) or artist (1 pt) scores.
"""
import asyncio
import json
import random
import re
from difflib import SequenceMatcher

import discord
from discord.ext import commands

from utils import embeds, llm, persona, ytdl

ROUND_SECONDS = 30
HINT_AT = 15
MAX_ROUNDS = 10


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r'\(.*?\)|\[.*?\]', '', s)
    s = re.sub(r'\b(feat|ft)\b.*', '', s)
    s = re.sub(r'[^\w ]', '', s)
    s = re.sub(r'^the ', '', s.strip())
    return ' '.join(s.split())


def _matches(guess: str, answer: str) -> bool:
    g, a = _norm(guess), _norm(answer)
    if not g or not a:
        return False
    if g == a or (len(a) >= 4 and a in g):
        return True
    return SequenceMatcher(None, g, a).ratio() >= 0.8


def _artist_matches(guess: str, artist: str) -> bool:
    parts = re.split(r',|&| x | and ', artist, flags=re.IGNORECASE)
    return any(_matches(guess, p) for p in parts + [artist] if p.strip())


def _hint(title: str) -> str:
    clean = re.sub(r'\(.*?\)|\[.*?\]', '', title).strip()
    return ' '.join(w[0] + '·' * (len(w) - 1) if w[0].isalnum() else w for w in clean.split())


def _name(member) -> str:
    return member.display_name


class _Game:
    def __init__(self, guild: discord.Guild, channel: discord.TextChannel, rounds: int):
        self.guild = guild
        self.channel = channel
        self.rounds = rounds
        self.scores: dict[int, int] = {}
        self.members: dict[int, discord.Member] = {}
        self.cancelled = False

    def award(self, member: discord.Member, pts: int):
        self.members[member.id] = member
        self.scores[member.id] = self.scores.get(member.id, 0) + pts

    def standings(self) -> list[tuple[discord.Member, int]]:
        return sorted(((self.members[u], s) for u, s in self.scores.items()), key=lambda x: -x[1])


class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._games: dict[int, _Game] = {}

    # ─── Commands ──────────────────────────────────────────────────────────────

    @commands.group(name='guess', aliases=['quiz'], invoke_without_command=True)
    async def guess(self, ctx: commands.Context, rounds: int = 5):
        """Start guess-the-song. Pip picks the songs. Usage: pip guess [rounds]"""
        err = await self._start(ctx.guild, ctx.channel, ctx.author, rounds)
        if err:
            await ctx.send(embed=embeds.error(err))

    @guess.command(name='stop', aliases=['end'])
    async def guess_stop(self, ctx: commands.Context):
        game = self._games.get(ctx.guild.id)
        if not game:
            await ctx.send(embed=embeds.info('no game running'))
            return
        game.cancelled = True
        await ctx.send(embed=embeds.info('fine. ending it after this round'))

    @guess.command(name='top', aliases=['leaderboard', 'lb'])
    async def guess_top(self, ctx: commands.Context):
        from utils.database import game_leaderboard
        rows = await game_leaderboard(ctx.guild.id)
        if not rows:
            await ctx.send(embed=embeds.info('nobody has played yet. cowards'))
            return
        medals = ['🥇', '🥈', '🥉']
        lines = [
            f'{medals[i] if i < 3 else f"`{i + 1}.`"} **{r["user_name"]}** — {r["points"]} pts · {r["wins"]} wins · {r["games"]} games'
            for i, r in enumerate(rows)
        ]
        await ctx.send(embed=discord.Embed(title='🎧 Guess the Song — all time', description='\n'.join(lines), color=embeds.GOLD))

    async def start_from_agent(self, guild, channel, author, rounds: int = 5) -> str:
        err = await self._start(guild, channel, author, rounds)
        return f'error: {err}' if err else f'game started ({max(3, min(MAX_ROUNDS, rounds))} rounds). it runs itself now'

    # ─── Setup / teardown ──────────────────────────────────────────────────────

    async def _start(self, guild, channel, author, rounds: int) -> str | None:
        from cogs.music import get_player
        if not llm.is_configured():
            return 'guess the song needs `GEMINI_API_KEY` in `.env`'
        p = get_player(guild.id)
        if p.game_channel_id:
            return "there's already a game going. focus"
        rounds = max(3, min(MAX_ROUNDS, rounds))

        voice_ch = author.voice.channel if author and getattr(author, 'voice', None) else None
        if not voice_ch and not (p.voice_client and p.voice_client.is_connected()):
            return persona.line('not_in_voice')
        if voice_ch:
            music = self.bot.cogs.get('Music')
            if not p.voice_client or not p.voice_client.is_connected():
                p.voice_client = await voice_ch.connect(cls=music._voice_cls())
            elif p.voice_client.channel != voice_ch:
                await p.voice_client.move_to(voice_ch)

        # Park whatever was playing; it comes back after the game
        p.game_channel_id = channel.id
        saved = ([p.current] if p.current else []) + list(p.queue)
        p.queue.clear()
        p.current = None
        if p._progress_task and not p._progress_task.done():
            p._progress_task.cancel()
        if p.voice_client.is_playing() or p.voice_client.is_paused():
            p.voice_client.stop()

        game = _Game(guild, channel, rounds)
        self._games[guild.id] = game
        asyncio.create_task(self._run(game, saved))
        return None

    async def _finish(self, game: _Game, saved: list):
        from cogs.music import get_player
        p = get_player(game.guild.id)
        self._games.pop(game.guild.id, None)
        if p.voice_client and (p.voice_client.is_playing() or p.voice_client.is_paused()):
            p.voice_client.stop()
        await asyncio.sleep(0.5)  # let the stopped clip's callback settle
        p.game_channel_id = None
        if saved and p.voice_client and p.voice_client.is_connected():
            p.queue.extend(saved)
            music = self.bot.cogs.get('Music')
            if music:
                await music._advance(game.guild.id)

    # ─── Song picking ──────────────────────────────────────────────────────────

    async def _pick_songs(self, game: _Game, count: int) -> list[dict]:
        from cogs.music import get_player
        from utils.database import get_liked_songs, get_play_history, get_guild_user_ids
        p = get_player(game.guild.id)
        listeners = [m for m in p.voice_client.channel.members if not m.bot] if p.voice_client else []
        user_ids = set(await get_guild_user_ids(game.guild.id))
        liked = [r['title'] for r in await get_liked_songs(limit=200) if r['user_id'] in user_ids]
        history = list({r['title'] for r in await get_play_history(game.guild.id, limit=150)})
        random.shuffle(liked)
        random.shuffle(history)

        task = (
            f'You are hosting a guess-the-song game for: {", ".join(_name(m) for m in listeners) or "the server"}.\n'
            f'Songs they have liked: {"; ".join(liked[:40]) or "none yet"}\n'
            f'Songs played here before: {"; ".join(history[:40]) or "none yet"}\n\n'
            f'Pick {count} songs for the game. Rules:\n'
            '- recognizable enough to be guessable from a 20 second clip, but not all obvious\n'
            '- mostly based on their taste (artists they like, adjacent artists), plus a couple of wildcards\n'
            '- do not just repeat the exact songs above; one or two is fine\n'
            '- varied: different artists, eras and energy\n'
            '- for each, write a short reveal quip in your voice (under 15 words) to post after the answer\n\n'
            'Return ONLY a JSON array: [{"artist": "...", "title": "...", "quip": "..."}]'
        )
        system = persona.CHARACTER + '\n\n' + await persona.personality_block(game.guild.id)
        text = await llm.chat(system, task, max_tokens=1500)
        if not text:
            return []
        match = re.search(r'\[.*\]', text, re.DOTALL)
        try:
            songs = json.loads(match.group() if match else text)
        except json.JSONDecodeError:
            return []
        return [s for s in songs if isinstance(s, dict) and s.get('artist') and s.get('title')]

    async def _prepare(self, song: dict, sem: asyncio.Semaphore) -> dict | None:
        """Find + download a song. Returns song with 'path'/'duration' or None."""
        async with sem:
            results = await ytdl.search_ytmusic(f'{song["artist"]} - {song["title"]} official audio', limit=3)
            info = next((r for r in results if 60 <= (r.get('duration') or 0) <= 600), None)
            if not info:
                return None
            path = await ytdl.download_and_get_path(info['url'], info.get('id'))
            if not path:
                return None
            return {**song, 'path': path, 'duration': info['duration'], 'url': info['url']}

    # ─── Game loop ─────────────────────────────────────────────────────────────

    async def _run(self, game: _Game, saved: list):
        ch = game.channel
        try:
            await ch.send(embed=discord.Embed(
                title='🎧 Guess the Song',
                description=(
                    f'**{game.rounds} rounds.** i play a clip, you type what it is.\n'
                    'title = **2 pts** · artist = **1 pt** · 30s per round\n'
                    'picking songs. try not to embarrass yourselves'
                ),
                color=embeds.PINK,
            ))
            songs = await self._pick_songs(game, game.rounds + 3)
            if len(songs) < 3:
                await ch.send(embed=embeds.error("couldn't come up with songs. my brain's buffering. try again"))
                return

            sem = asyncio.Semaphore(3)
            tasks = [asyncio.create_task(self._prepare(s, sem)) for s in songs]
            played = 0
            for task in tasks:
                if played >= game.rounds or game.cancelled:
                    break
                song = await task
                if not song:
                    continue
                played += 1
                await self._round(game, song, played)
                await asyncio.sleep(3)
            for task in tasks:
                task.cancel()

            if played == 0:
                await ch.send(embed=embeds.error("youtube wouldn't give me a single song. game off"))
                return
            await self._final(game)
        except Exception as e:
            print(f'[games] {type(e).__name__}: {e}')
            await ch.send(embed=embeds.error(persona.line('crash')))
        finally:
            await self._finish(game, saved)

    async def _round(self, game: _Game, song: dict, number: int):
        from cogs.music import get_player
        p = get_player(game.guild.id)
        vc = p.voice_client
        if not vc or not vc.is_connected():
            game.cancelled = True
            return

        dur = song['duration']
        offset = int(dur * random.uniform(0.25, 0.45))
        await game.channel.send(f'**round {number}/{game.rounds}** 🎵 go')
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(song['path'], before_options=f'-ss {offset}'), volume=p.volume
        )
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        vc.play(source)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + ROUND_SECONDS
        hint_sent = False
        artist_taken = False
        winner = None

        def check(m: discord.Message):
            return m.channel.id == game.channel.id and not m.author.bot

        while not game.cancelled:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            if not hint_sent and remaining <= ROUND_SECONDS - HINT_AT:
                hint_sent = True
                await game.channel.send(f'hint: `{_hint(song["title"])}`')
                continue
            wait = remaining - (ROUND_SECONDS - HINT_AT) if not hint_sent else remaining
            try:
                msg = await self.bot.wait_for('message', check=check, timeout=max(0.1, wait))
            except asyncio.TimeoutError:
                continue
            if _matches(msg.content, song['title']):
                winner = msg.author
                game.award(msg.author, 2)
                await msg.add_reaction('✅')
                break
            if not artist_taken and _artist_matches(msg.content, song['artist']):
                artist_taken = True
                game.award(msg.author, 1)
                await msg.add_reaction('🎤')
                await game.channel.send(f'artist ✓ **+1** {msg.author.mention}. title is still up for grabs')

        if vc.is_playing() or vc.is_paused():
            vc.stop()

        head = f'✅ **{_name(winner)}** got it. ' if winner else '⏰ nobody. '
        e = discord.Embed(
            description=f'{head}it was **[{song["title"]}]({song["url"]})** — {song["artist"]}\n*{song.get("quip", "")}*',
            color=embeds.GREEN if winner else embeds.BLUE,
        )
        board = game.standings()
        if board:
            e.set_footer(text=' · '.join(f'{_name(m)} {s}' for m, s in board[:5]))
        await game.channel.send(embed=e)

    async def _final(self, game: _Game):
        from utils.database import add_game_result
        board = game.standings()
        if not board:
            await persona.shift_mood(game.guild.id, 0.15, 'nobody scored a single point at guess the song')
            line = await persona.one_liner(
                game.guild.id,
                'You just hosted a guess-the-song game and NOBODY got a single point. React in one line.',
            )
            await game.channel.send(embed=discord.Embed(
                title='🎧 Game over', description=f'zero points. all of you.\n\n{line or ""}', color=embeds.GOLD))
            return

        top_score = board[0][1]
        for member, score in board:
            await add_game_result(game.guild.id, member.id, _name(member), score, score == top_score)

        medals = ['🥇', '🥈', '🥉']
        lines = [f'{medals[i] if i < 3 else "▫️"} **{_name(m)}** — {s}' for i, (m, s) in enumerate(board)]
        winners = [_name(m) for m, s in board if s == top_score]
        summary = ', '.join(f'{_name(m)}: {s}' for m, s in board)
        line = await persona.one_liner(
            game.guild.id,
            f'You just hosted a guess-the-song game ({game.rounds} rounds, 2 pts per title, 1 per artist). '
            f'Final scores: {summary}. Winner: {" and ".join(winners)}. '
            'Give the closing line: congratulate the winner in your way and trash-talk the rest a little. One or two lines.',
        )
        e = discord.Embed(title='🎧 Game over', description='\n'.join(lines), color=embeds.GOLD)
        if line:
            e.add_field(name='​', value=line, inline=False)
        e.set_footer(text='pip guess top for the all-time board')
        await game.channel.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))
