import discord
from discord.ext import commands
from utils import embeds

HELP_PAGES = {
    'music': {
        'title': '🎵 Music Commands',
        'color': embeds.PINK,
        'fields': [
            ('pip play <song or url>', 'Play a song from YouTube (search or URL)'),
            ('pip pause / pip resume', 'Pause or resume playback'),
            ('pip skip', 'Skip the current song'),
            ('pip stop', 'Stop and disconnect from voice'),
            ('pip nowplaying', 'Show the currently playing song'),
            ('pip queue [page]', 'Show the song queue'),
            ('pip volume <0-100>', 'Set the volume'),
            ('pip loop [off/one/all]', 'Toggle loop mode (off → one → all)'),
            ('pip autoplay', 'Toggle autoplay of related songs when queue ends'),
            ('pip shuffle', 'Shuffle the current queue'),
            ('pip search <query>', 'Search YouTube and pick from top 5 results'),
            ('pip remove <#>', 'Remove a song from the queue by position'),
            ('pip move <from> <to>', 'Reorder a song in the queue'),
            ('pip clear', 'Clear the queue without stopping'),
            ('pip album <url> [shuffle]', 'Queue a full YouTube playlist or album'),
            ('pip join / pip leave', 'Join or leave your voice channel'),
        ]
    },
    'playlist': {
        'title': '📁 Playlist Commands',
        'color': embeds.PURPLE,
        'fields': [
            ('pip playlist create <name>', 'Create a new playlist (public by default)'),
            ('pip playlist list', 'List your playlists'),
            ('pip playlist show <id>', 'View all tracks in a playlist'),
            ('pip playlist play <id> [shuffle]', 'Play a playlist (add shuffle to randomise)'),
            ('pip playlist add <id> <song or url>', 'Add a song to one of your playlists'),
            ('pip playlist addcurrent <id>', 'Add the currently playing song to a playlist'),
            ('pip playlist removesong <id> <#>', 'Remove a track by position'),
            ('pip playlist delete <id>', 'Delete a playlist you own'),
            ('pip playlist rename <id> <name>', 'Rename a playlist'),
            ('pip playlist privacy <id> public/private', 'Make a playlist public or private'),
            ('pip playlist share <id>', 'Get the shareable ID to give to friends'),
            ('pip playlist public', 'Browse all public playlists on this server'),
        ]
    },
    'spotify': {
        'title': '🟢 Spotify Commands',
        'color': 0x1DB954,
        'fields': [
            ('pip spotify <url>', 'Convert a Spotify playlist/album and save it as a PipSqueek playlist'),
            ('pip spotify <url> play', 'Convert and immediately queue it without saving'),
            ('pip spotify <url> save <name>', 'Convert and save with a custom name'),
        ]
    },
}


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name='help', aliases=['h', 'commands'])
    async def help(self, ctx: commands.Context, section: str = None):
        """Show bot commands. Sections: music, playlist, spotify"""
        if section and section.lower() in HELP_PAGES:
            page = HELP_PAGES[section.lower()]
            e = discord.Embed(title=page['title'], color=page['color'])
            for name, value in page['fields']:
                e.add_field(name=f'`{name}`', value=value, inline=False)
            await ctx.send(embed=e)
            return

        e = discord.Embed(
            title='🐭 PipSqueek Bot',
            description=(
                'Your friendly neighbourhood music bot!\n\n'
                '**Sections** — type `pip help <section>` for full details:\n'
                '`music` • `playlist` • `spotify`'
            ),
            color=embeds.PINK
        )
        e.add_field(
            name='Quick Start',
            value=(
                '`pip play <song name>` — play a song\n'
                '`pip queue` — view the queue\n'
                '`pip album <youtube url>` — queue a full album or playlist\n'
                '`pip playlist create My Mix` — make a personal playlist\n'
                '`pip spotify <spotify url>` — import a Spotify playlist'
            ),
            inline=False
        )
        e.set_footer(text='Prefix: pip  |  PipSqueek Bot')
        await ctx.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
