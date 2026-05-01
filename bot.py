import asyncio
import os
import sys
import discord
from discord.ext import commands
from dotenv import load_dotenv
from utils.database import init_db

# Force line-buffered output so logs appear immediately
sys.stdout.reconfigure(line_buffering=True, errors='replace')
sys.stderr.reconfigure(line_buffering=True, errors='replace')

load_dotenv(override=True)

TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('BOT_PREFIX', 'pip').strip() + ' '

if not TOKEN:
    raise RuntimeError('DISCORD_TOKEN not set in .env file')

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True,
)

COGS = [
    'cogs.music',
    'cogs.playlist',
    'cogs.spotify_import',
    'cogs.help',
    'cogs.agent',
]


@bot.event
async def on_ready():
    print(f'[PipSqueek] Online as {bot.user}')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name=f'pip play | pip help'
    ))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # In Mk2 mode: only process the 'mode' and 'setchannel' commands directly;
    # everything else is routed through the agent's on_message listener.
    agent_cog = bot.cogs.get('Agent')
    if agent_cog and message.guild:
        guild_id = message.guild.id
        if agent_cog.get_mode(guild_id) == 'mk2':
            content_lower = message.content.lower().strip()
            prefix_lower = PREFIX.lower()
            # Allow pip mode and pip setchannel through as commands
            if content_lower.startswith(prefix_lower + 'mode') or \
               content_lower.startswith(prefix_lower + 'setchannel'):
                await bot.process_commands(message)
            # All other messages: the agent's on_message listener handles it
            return

    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=discord.Embed(
            description=f'❌ Missing argument: `{error.param.name}`. Use `pip help` for usage.',
            color=0xFF3D00
        ))
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(embed=discord.Embed(
            description=f'❌ Bad argument: {error}',
            color=0xFF3D00
        ))
        return
    raise error


async def main():
    await init_db()
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f'  [OK] Loaded {cog}')
            except Exception as e:
                print(f'  [FAIL] Failed to load {cog}: {e}')
        await bot.start(TOKEN)


if __name__ == '__main__':
    asyncio.run(main())
