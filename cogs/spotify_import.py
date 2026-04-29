import asyncio
import discord
from discord.ext import commands
from utils import spotify as sp_utils, ytdl, database as db, embeds
from cogs.music import get_player


class SpotifyImport(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name='spotify', aliases=['sp'])
    async def spotify_import(self, ctx: commands.Context, url: str, *, flags: str = ''):
        """
        Convert a Spotify playlist or album to a PipSqueek playlist.

        Usage:
          !spotify <spotify_url>              — save to a new playlist
          !spotify <spotify_url> play         — queue it directly
          !spotify <spotify_url> save <name>  — save with custom name
        """
        if not sp_utils.is_spotify_url(url):
            await ctx.send(embed=embeds.error('That does not look like a Spotify URL.'))
            return

        kind = sp_utils.get_spotify_type(url)
        if kind not in ('playlist', 'album'):
            await ctx.send(embed=embeds.error('Only Spotify **playlists** and **albums** are supported.'))
            return

        msg = await ctx.send(embed=embeds.info(f'Fetching Spotify {kind}…'))

        try:
            if kind == 'playlist':
                name, tracks = sp_utils.get_playlist_tracks(url)
            else:
                name, tracks = sp_utils.get_album_tracks(url)
        except ValueError as e:
            await msg.edit(embed=embeds.error(str(e)))
            return
        except Exception as e:
            await msg.edit(embed=embeds.error(f'Spotify error: {e}'))
            return

        if not tracks:
            await msg.edit(embed=embeds.error('No tracks found in that Spotify item.'))
            return

        flags_lower = flags.lower()
        play_direct = 'play' in flags_lower

        # Parse custom name from flags
        if 'save' in flags_lower:
            parts = flags.split('save', 1)
            custom_name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else name
        else:
            custom_name = name

        await msg.edit(embed=embeds.info(
            f'Found **{len(tracks)} tracks** in **{name}**.\n'
            f'Searching YouTube for each track… this may take a moment.'
        ))

        # Search YouTube for each Spotify track
        resolved = []
        failed = []
        for i, track in enumerate(tracks):
            results = await ytdl.search_ytmusic(track['search_query'], limit=1)
            if results:
                resolved.append(results[0])
            else:
                failed.append(track['title'])
            if i % 10 == 9:
                await msg.edit(embed=embeds.info(
                    f'Searching… {i+1}/{len(tracks)} done.'
                ))

        if not resolved:
            await msg.edit(embed=embeds.error('Could not find any tracks on YouTube.'))
            return

        summary = f'Matched **{len(resolved)}/{len(tracks)}** tracks.'
        if failed:
            summary += f'\n\nCould not find:\n' + '\n'.join(f'• {t}' for t in failed[:5])
            if len(failed) > 5:
                summary += f'\n*...and {len(failed) - 5} more*'

        if play_direct:
            # Queue directly without saving
            if not ctx.author.voice:
                await msg.edit(embed=embeds.error('You must be in a voice channel to play.'))
                return

            p = get_player(ctx.guild.id)
            if p.voice_client and p.voice_client.is_connected():
                if p.voice_client.channel != ctx.author.voice.channel:
                    await p.voice_client.move_to(ctx.author.voice.channel)
            else:
                p.voice_client = await ctx.author.voice.channel.connect()

            for t in resolved:
                p.queue.append({**t, 'requester': ctx.author})

            await msg.edit(embed=embeds.success(
                f'{summary}\n\nAdded to queue.'
            ))

            if not (p.voice_client.is_playing() or p.voice_client.is_paused()):
                from cogs.music import Music
                music_cog: Music = self.bot.get_cog('Music')
                if music_cog and p.queue:
                    async with p._lock:
                        track = p.queue.popleft()
                        await music_cog._play_track(ctx, p, track)
        else:
            # Save as a PipSqueek playlist
            playlist_id = await db.create_playlist(custom_name, ctx.author.id, ctx.author.display_name)
            for t in resolved:
                await db.add_track_to_playlist(
                    playlist_id, t['title'], t['url'],
                    t.get('duration', 0), t.get('thumbnail', ''),
                    ctx.author.id, ctx.author.display_name
                )

            e = discord.Embed(
                title='🎵 Spotify Import Complete',
                description=summary,
                color=embeds.GREEN
            )
            e.add_field(name='Saved as', value=f'**{custom_name}**', inline=True)
            e.add_field(name='Playlist ID', value=f'`{playlist_id}`', inline=True)
            e.add_field(
                name='Play it',
                value=f'`!playlist play {playlist_id}`',
                inline=False
            )
            await msg.edit(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(SpotifyImport(bot))
