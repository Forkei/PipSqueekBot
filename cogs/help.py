import discord
from discord.ext import commands
from utils import embeds


HELP_PAGES = {
    'music': {
        'title': '🎵 Music Commands',
        'color': embeds.PINK,
        'fields': [
            ('!play <query/url>', 'Play a song from YouTube (search or URL)'),
            ('!pause / !resume', 'Pause or resume playback'),
            ('!skip', 'Skip the current song'),
            ('!stop', 'Stop and disconnect from voice'),
            ('!nowplaying', 'Show the currently playing song'),
            ('!queue [page]', 'Show the song queue'),
            ('!volume <0-100>', 'Set the volume'),
            ('!loop [off/one/all]', 'Toggle loop mode'),
            ('!autoplay', 'Toggle autoplay of related songs'),
            ('!shuffle', 'Shuffle the current queue'),
            ('!remove <#>', 'Remove a song from the queue'),
            ('!move <from> <to>', 'Move a song in the queue'),
            ('!clear', 'Clear the queue'),
            ('!search <query>', 'Search and pick from top 5 results'),
            ('!album <url> [shuffle]', 'Add a YouTube playlist/album to queue'),
            ('!join / !leave', 'Join or leave voice channel'),
        ]
    },
    'playlist': {
        'title': '📁 Playlist Commands',
        'color': embeds.PURPLE,
        'fields': [
            ('!playlist create <name>', 'Create a new playlist'),
            ('!playlist list', 'List your playlists'),
            ('!playlist show <id>', 'View tracks in a playlist'),
            ('!playlist play <id> [shuffle]', 'Play a playlist'),
            ('!playlist add <id> <query/url>', 'Add a song to a playlist'),
            ('!playlist addcurrent <id>', 'Add now-playing to a playlist'),
            ('!playlist removesong <id> <#>', 'Remove a track from a playlist'),
            ('!playlist delete <id>', 'Delete a playlist'),
            ('!playlist rename <id> <name>', 'Rename a playlist'),
            ('!playlist privacy <id> public/private', 'Set playlist privacy'),
            ('!playlist share <id>', 'Get a shareable ID'),
            ('!playlist public', 'Browse all public playlists'),
        ]
    },
    'spotify': {
        'title': '🟢 Spotify Commands',
        'color': 0x1DB954,
        'fields': [
            ('!spotify <url>', 'Convert a Spotify playlist/album to a saved PipSqueek playlist'),
            ('!spotify <url> play', 'Convert and immediately queue it (no save)'),
            ('!spotify <url> save <name>', 'Convert and save with a custom name'),
        ]
    },
}


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name='help', aliases=['h', 'commands'])
    async def help(self, ctx: commands.Context, section: str = None):
        """Show bot commands. `!help music`, `!help playlist`, `!help spotify`"""
        if section and section.lower() in HELP_PAGES:
            page = HELP_PAGES[section.lower()]
            e = discord.Embed(title=page['title'], color=page['color'])
            for name, value in page['fields']:
                e.add_field(name=f'`{name}`', value=value, inline=False)
            await ctx.send(embed=e)
            return

        e = discord.Embed(
            title='🐭 PipSqueek Bot — Help',
            description=(
                'A music bot for friends!\n\n'
                '**Sections** — use `!help <section>` for details:\n'
                '`music` • `playlist` • `spotify`'
            ),
            color=embeds.PINK
        )
        e.add_field(
            name='Quick Start',
            value=(
                '`!play <song name>` — play a song\n'
                '`!queue` — view queue\n'
                '`!album <youtube url>` — queue an album/playlist\n'
                '`!playlist create My Mix` — make a playlist\n'
                '`!spotify <spotify url>` — import from Spotify'
            ),
            inline=False
        )
        e.set_footer(text='Prefix: ! | PipSqueek Bot')
        await ctx.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
