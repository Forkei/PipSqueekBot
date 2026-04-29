import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from utils.database import init_db

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('BOT_PREFIX', '!')

if not TOKEN:
    raise RuntimeError('DISCORD_TOKEN not set in .env file')

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,   # we use our own
    case_insensitive=True,
)

COGS = [
    'cogs.music',
    'cogs.playlist',
    'cogs.spotify_import',
    'cogs.help',
]


@bot.event
async def on_ready():
    print(f'🐭 PipSqueek is online as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name=f'{PREFIX}play | {PREFIX}help'
    ))


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=discord.Embed(
            description=f'❌ Missing argument: `{error.param.name}`. Use `!help` for usage.',
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
                print(f'  ✓ Loaded {cog}')
            except Exception as e:
                print(f'  ✗ Failed to load {cog}: {e}')
        await bot.start(TOKEN)


if __name__ == '__main__':
    asyncio.run(main())
