"""
Pip's personality commands: wrapped, roast, mood, lore.
"""
import datetime
import time

import discord
from discord.ext import commands, tasks

from utils import embeds, llm, persona

WRAPPED_DAY = 6    # Sunday
WRAPPED_HOUR = 19  # local time, auto-post after this


def _label(member: discord.Member) -> str:
    if member.display_name == member.name:
        return member.display_name
    return f'{member.display_name} (@{member.name})'


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.weekly_wrapped.start()

    async def cog_unload(self):
        self.weekly_wrapped.cancel()

    # ─── Wrapped ───────────────────────────────────────────────────────────────

    async def _build_wrapped(self, guild: discord.Guild, days: int, title: str) -> discord.Embed | None:
        from utils import database as db
        since = int(time.time()) - days * 86400
        bot_id = guild.me.id
        counts = await db.play_counts(guild.id, since, bot_id)
        if not counts['total']:
            return None

        songs = await db.top_songs(guild.id, since)
        people = await db.top_requesters(guild.id, since, bot_id)
        skipped = await db.most_skipped(guild.id, since)
        liked = await db.most_liked(await db.get_guild_user_ids(guild.id), since)
        requests = await db.requests_in_window(guild.id, since, bot_id)

        stats = (
            f'Period: {title}. {counts["total"]} plays, {counts["unique"]} unique songs, {counts["pip"]} picked by you.\n'
            f'Top songs: {"; ".join(f"{r["title"]} ({r["n"]}x)" for r in songs) or "none"}\n'
            f'Top requesters: {"; ".join(f"{r["user_name"]} ({r["n"]})" for r in people) or "none"}\n'
            f'Most skipped: {"; ".join(f"{r["title"]} (req by {r["requester_name"]}, {r["n"]}x)" for r in skipped) or "none"}\n'
            f'Most liked: {"; ".join(f"{r["title"]} ({r["n"]} ❤️)" for r in liked) or "none"}\n'
            f'All requests: {"; ".join(f"{r["user_name"]}: {r["title"]}" for r in requests)}'
        )
        commentary = await persona.one_liner(
            guild.id,
            f'You are presenting the server\'s music Wrapped.\n{stats}\n\n'
            'Write your commentary, 3 to 5 short lines, each starting with an emoji. Include: '
            'a line about the overall listening, a shoutout (or light roast) for the top requester, '
            'a "worst request" award where you pick one real request from the list and roast it, '
            'and a sign-off. Use **bold** for names and songs. No intro like "here is".',
            max_tokens=500,
        )

        e = discord.Embed(title=f'💿 Pip Wrapped — {title}', color=embeds.PINK)
        e.description = f'**{counts["total"]}** plays · **{counts["unique"]}** unique songs · **{counts["pip"]}** picked by me'
        medals = ['🥇', '🥈', '🥉', '4.', '5.']
        if songs:
            e.add_field(name='Top songs', value='\n'.join(
                f'{medals[i]} {r["title"][:60]} · {r["n"]}x' for i, r in enumerate(songs)), inline=False)
        if people:
            e.add_field(name='Top requesters', value='\n'.join(
                f'{medals[i]} {r["user_name"]} · {r["n"]}' for i, r in enumerate(people)), inline=True)
        if liked:
            e.add_field(name='Most ❤️', value='\n'.join(
                f'{r["title"][:40]} · {r["n"]}' for r in liked), inline=True)
        if skipped:
            e.add_field(name='Most skipped', value='\n'.join(
                f'{r["title"][:40]} · {r["n"]}x' for r in skipped), inline=False)
        if commentary:
            e.add_field(name='Pip says', value=commentary[:1024], inline=False)
        return e

    @commands.group(name='wrapped', invoke_without_command=True)
    async def wrapped(self, ctx: commands.Context, period: str = 'week'):
        """Server music recap. Usage: pip wrapped [week|month|all]"""
        periods = {'week': (7, 'this week'), 'month': (30, 'this month'), 'all': (36500, 'all time')}
        days, title = periods.get(period.lower(), periods['week'])
        async with ctx.typing():
            e = await self._build_wrapped(ctx.guild, days, title)
        if not e:
            await ctx.send(embed=embeds.info("nothing played in that window. nothing to wrap. sad"))
            return
        await ctx.send(embed=e)

    @wrapped.command(name='auto')
    async def wrapped_auto(self, ctx: commands.Context, setting: str = None):
        """Toggle the automatic Sunday-evening Wrapped post. Usage: pip wrapped auto [on|off]"""
        from utils.database import kv_get, kv_set
        current = (await kv_get(ctx.guild.id, 'wrapped_auto')) != 'off'
        new = {'on': True, 'off': False}.get((setting or '').lower(), not current)
        await kv_set(ctx.guild.id, 'wrapped_auto', 'on' if new else 'off')
        where = "in the agent channel (set with `pip setchannel`)"
        await ctx.send(embed=embeds.success(
            f'weekly wrapped **{"on" if new else "off"}**' + (f'. sundays after {WRAPPED_HOUR}:00, {where}' if new else '')))

    @tasks.loop(minutes=30)
    async def weekly_wrapped(self):
        now = datetime.datetime.now()
        if now.weekday() != WRAPPED_DAY or now.hour < WRAPPED_HOUR:
            return
        from utils.database import kv_get, kv_set
        agent = self.bot.cogs.get('Agent')
        if not agent or not llm.is_configured():
            return
        week = f'{now.isocalendar().year}-W{now.isocalendar().week}'
        for guild_id, channel_id in list(agent._agent_channel.items()):
            try:
                if await kv_get(guild_id, 'wrapped_auto') == 'off':
                    continue
                if await kv_get(guild_id, 'wrapped_last') == week:
                    continue
                guild = self.bot.get_guild(guild_id)
                channel = guild.get_channel(channel_id) if guild else None
                if not channel:
                    continue
                await kv_set(guild_id, 'wrapped_last', week)
                e = await self._build_wrapped(guild, 7, 'this week')
                if e:
                    await channel.send(embed=e)
            except Exception as ex:
                print(f'[wrapped] auto-post failed for {guild_id}: {type(ex).__name__}: {ex}')

    @weekly_wrapped.before_loop
    async def _before_wrapped(self):
        await self.bot.wait_until_ready()

    # ─── Roast ─────────────────────────────────────────────────────────────────

    @commands.command(name='roast')
    async def roast(self, ctx: commands.Context, member: discord.Member = None):
        """Pip roasts someone's music taste. Usage: pip roast [@user]"""
        from utils import database as db
        member = member or ctx.author
        if member.id == self.bot.user.id:
            await ctx.send('my taste is flawless. next')
            return
        if not llm.is_configured():
            await ctx.send(embed=embeds.error('roasting needs `GEMINI_API_KEY` in `.env`'))
            return

        async with ctx.typing():
            history = await db.get_user_play_history(ctx.guild.id, member.id, limit=40)
            liked = await db.get_liked_songs(user_id=member.id, limit=25)
            skips = await db.skip_count_for_user(ctx.guild.id, member.id)
            score = await db.game_score_for(ctx.guild.id, member.id)
            if not history and not liked:
                await ctx.send(f"i don't know enough about {member.mention}'s taste to roast it. which honestly says it all")
                return
            requested = list(dict.fromkeys(r['title'] for r in history))[:25]
            facts = (
                f'Target: {_label(member)}\n'
                f'Songs they requested: {"; ".join(requested) or "none"}\n'
                f'Songs they ❤️\'d: {"; ".join(r["title"] for r in liked) or "none"}\n'
                f'Times their requests got skipped: {skips}\n'
                + (f'Guess-the-song record: {score["points"]} pts over {score["games"]} games\n' if score else '')
            )
            text = await persona.one_liner(
                ctx.guild.id,
                f'{facts}\nRoast their music taste. 2-4 short lines, specific to the actual songs and patterns above. '
                'Playful, the way you roast a friend. Never about appearance, identity or anything outside music. '
                'End with a backhanded compliment.',
                max_tokens=300,
            )
        await ctx.send(f'{member.mention} {text}' if text else persona.line('crash'))

    # ─── Mood ──────────────────────────────────────────────────────────────────

    @commands.command(name='mood')
    async def mood(self, ctx: commands.Context):
        """How's Pip feeling?"""
        score, label, reasons = await persona.mood(ctx.guild.id)
        bar_pos = round((score + 1) * 5)
        bar = '▰' * bar_pos + '▱' * (10 - bar_pos)
        line = await persona.one_liner(
            ctx.guild.id, f'{ctx.author.display_name} asked how you\'re feeling. Answer in one short line, true to your mood.',
            max_tokens=80,
        )
        e = discord.Embed(description=f'**{line or label}**', color=embeds.PINK)
        e.add_field(name='mood', value=f'😤 {bar} 😌  ({label})', inline=False)
        if reasons:
            e.add_field(name='lately', value='\n'.join(f'· {r}' for r in reasons[-3:]), inline=False)
        await ctx.send(embed=e)

    # ─── Lore ──────────────────────────────────────────────────────────────────

    @commands.group(name='lore', invoke_without_command=True)
    async def lore(self, ctx: commands.Context):
        """Show the server lore Pip remembers."""
        from utils.database import list_lore
        rows = await list_lore(ctx.guild.id)
        if not rows:
            await ctx.send(embed=embeds.info('no lore yet. do something memorable. or `pip lore add <moment>`'))
            return
        lines = [f'`#{r["id"]}` {r["content"]}' for r in rows[:20]]
        e = discord.Embed(title='📜 Server lore', description='\n'.join(lines), color=embeds.PURPLE)
        e.set_footer(text=f'{len(rows)} entries · pip lore add <text> · pip lore remove <#>')
        await ctx.send(embed=e)

    @lore.command(name='add')
    async def lore_add(self, ctx: commands.Context, *, content: str):
        from utils.database import add_lore
        lore_id = await add_lore(ctx.guild.id, content[:300], ctx.author.display_name)
        await ctx.send(embed=embeds.success(f'lore #{lore_id} recorded. i will never forget. literally'))

    @lore.command(name='remove', aliases=['delete', 'rm'])
    async def lore_remove(self, ctx: commands.Context, lore_id: int):
        from utils.database import delete_lore
        if await delete_lore(ctx.guild.id, lore_id):
            await ctx.send(embed=embeds.success(f'lore #{lore_id} erased from history'))
        else:
            await ctx.send(embed=embeds.error(f'no lore #{lore_id}'))


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
