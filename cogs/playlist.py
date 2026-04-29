import asyncio
import random
import discord
from discord.ext import commands
from utils import database as db, ytdl, embeds
from cogs.music import get_player


class Playlist(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _resolve_playlist_access(self, ctx: commands.Context, playlist_id: str,
                                        require_owner: bool = False) -> dict | None:
        pl = await db.get_playlist(playlist_id.upper())
        if not pl:
            await ctx.send(embed=embeds.error(f'No playlist found with ID `{playlist_id.upper()}`.'))
            return None
        if require_owner and pl['owner_id'] != ctx.author.id:
            await ctx.send(embed=embeds.error('You do not own this playlist.'))
            return None
        if not pl['is_public'] and pl['owner_id'] != ctx.author.id:
            await ctx.send(embed=embeds.error('This playlist is private.'))
            return None
        return pl

    @commands.group(name='playlist', aliases=['pl'], invoke_without_command=True)
    async def playlist(self, ctx: commands.Context):
        """Playlist management. Use `!playlist help` for subcommands."""
        await ctx.send_help(ctx.command)

    @playlist.command(name='create')
    async def pl_create(self, ctx: commands.Context, *, name: str):
        """Create a new playlist. `!playlist create My Cool Playlist`"""
        playlist_id = await db.create_playlist(name, ctx.author.id, ctx.author.display_name)
        e = discord.Embed(
            title='✅ Playlist Created',
            description=f'**{name}**\nID: `{playlist_id}`',
            color=embeds.GREEN
        )
        e.add_field(name='Share with', value=f'`!playlist play {playlist_id}`')
        e.set_footer(text='Anyone can play it since it\'s public by default. Use !playlist privacy to change.')
        await ctx.send(embed=e)

    @playlist.command(name='delete', aliases=['del', 'remove'])
    async def pl_delete(self, ctx: commands.Context, playlist_id: str):
        """Delete a playlist you own."""
        pl = await self._resolve_playlist_access(ctx, playlist_id, require_owner=True)
        if not pl:
            return
        await db.delete_playlist(playlist_id)
        await ctx.send(embed=embeds.success(f'Deleted playlist **{pl["name"]}**.'))

    @playlist.command(name='list', aliases=['ls', 'mine'])
    async def pl_list(self, ctx: commands.Context):
        """List your playlists."""
        playlists = await db.get_user_playlists(ctx.author.id)
        if not playlists:
            await ctx.send(embed=embeds.info("You don't have any playlists. Create one with `!playlist create <name>`."))
            return

        e = discord.Embed(title=f"📁 {ctx.author.display_name}'s Playlists", color=embeds.PURPLE)
        for pl in playlists:
            privacy = '🔒' if not pl['is_public'] else '🌐'
            e.add_field(
                name=f"{privacy} {pl['name']} `{pl['id']}`",
                value=f"{pl['track_count']} track(s)",
                inline=False
            )
        await ctx.send(embed=e)

    @playlist.command(name='public', aliases=['browse'])
    async def pl_public(self, ctx: commands.Context):
        """Browse public playlists."""
        playlists = await db.get_public_playlists()
        if not playlists:
            await ctx.send(embed=embeds.info('No public playlists yet.'))
            return

        e = discord.Embed(title='🌐 Public Playlists', color=embeds.BLUE)
        for pl in playlists:
            e.add_field(
                name=f"**{pl['name']}** `{pl['id']}`",
                value=f"by {pl['owner_name']} • {pl['track_count']} track(s)",
                inline=False
            )
        await ctx.send(embed=e)

    @playlist.command(name='show', aliases=['info', 'view'])
    async def pl_show(self, ctx: commands.Context, playlist_id: str):
        """Show tracks in a playlist."""
        pl = await self._resolve_playlist_access(ctx, playlist_id)
        if not pl:
            return
        tracks = await db.get_playlist_tracks(playlist_id)
        await ctx.send(embed=embeds.playlist_info(pl, list(tracks)))

    @playlist.command(name='add')
    async def pl_add(self, ctx: commands.Context, playlist_id: str, *, query: str):
        """Add a song to a playlist. `!playlist add ABCD1234 song name or URL`"""
        pl = await self._resolve_playlist_access(ctx, playlist_id, require_owner=True)
        if not pl:
            return

        async with ctx.typing():
            if query.startswith('http'):
                info = await ytdl.extract_info(query)
            else:
                results = await ytdl.search_ytmusic(query, limit=1)
                info = results[0] if results else None

        if not info:
            await ctx.send(embed=embeds.error(f'Nothing found for `{query}`.'))
            return

        pos = await db.add_track_to_playlist(
            playlist_id, info['title'], info['url'],
            info.get('duration', 0), info.get('thumbnail', ''),
            ctx.author.id, ctx.author.display_name
        )
        await ctx.send(embed=embeds.success(
            f'Added **{info["title"]}** to **{pl["name"]}** at position #{pos}.'
        ))

    @playlist.command(name='addcurrent', aliases=['addnp'])
    async def pl_addcurrent(self, ctx: commands.Context, playlist_id: str):
        """Add the currently playing song to a playlist."""
        pl = await self._resolve_playlist_access(ctx, playlist_id, require_owner=True)
        if not pl:
            return
        p = get_player(ctx.guild.id)
        if not p.current:
            await ctx.send(embed=embeds.error('Nothing is playing right now.'))
            return
        track = p.current
        pos = await db.add_track_to_playlist(
            playlist_id, track['title'], track['url'],
            track.get('duration', 0), track.get('thumbnail', ''),
            ctx.author.id, ctx.author.display_name
        )
        await ctx.send(embed=embeds.success(
            f'Added **{track["title"]}** to **{pl["name"]}** at position #{pos}.'
        ))

    @playlist.command(name='removesong', aliases=['rms'])
    async def pl_removesong(self, ctx: commands.Context, playlist_id: str, position: int):
        """Remove a track from a playlist by position."""
        pl = await self._resolve_playlist_access(ctx, playlist_id, require_owner=True)
        if not pl:
            return
        removed = await db.remove_track_from_playlist(playlist_id, position)
        if removed:
            await ctx.send(embed=embeds.success(f'Removed track #{position} from **{pl["name"]}**.'))
        else:
            await ctx.send(embed=embeds.error(f'No track at position #{position}.'))

    @playlist.command(name='play')
    async def pl_play(self, ctx: commands.Context, playlist_id: str, *, flags: str = ''):
        """Play a playlist. Add `shuffle` to shuffle it."""
        pl = await self._resolve_playlist_access(ctx, playlist_id)
        if not pl:
            return

        tracks = await db.get_playlist_tracks(playlist_id)
        if not tracks:
            await ctx.send(embed=embeds.error('This playlist is empty.'))
            return

        if not ctx.author.voice:
            await ctx.send(embed=embeds.error('You must be in a voice channel.'))
            return

        p = get_player(ctx.guild.id)
        if p.voice_client and p.voice_client.is_connected():
            if p.voice_client.channel != ctx.author.voice.channel:
                await p.voice_client.move_to(ctx.author.voice.channel)
        else:
            p.voice_client = await ctx.author.voice.channel.connect()

        track_list = [dict(t) for t in tracks]
        if 'shuffle' in flags.lower():
            random.shuffle(track_list)

        # Convert DB rows to player-compatible dicts
        for t in track_list:
            p.queue.append({
                'id': None,
                'title': t['title'],
                'url': t['url'],
                'duration': t.get('duration', 0),
                'thumbnail': t.get('thumbnail'),
                'uploader': None,
                'requester': ctx.author,
            })

        shuffled_note = ' (shuffled)' if 'shuffle' in flags.lower() else ''
        await ctx.send(embed=embeds.success(
            f'Added **{len(track_list)} tracks** from **{pl["name"]}** to the queue{shuffled_note}.'
        ))

        if not (p.voice_client.is_playing() or p.voice_client.is_paused()):
            from cogs.music import Music
            music_cog: Music = self.bot.get_cog('Music')
            if music_cog and p.queue:
                async with p._lock:
                    track = p.queue.popleft()
                    await music_cog._play_track(ctx, p, track)

    @playlist.command(name='privacy')
    async def pl_privacy(self, ctx: commands.Context, playlist_id: str, setting: str):
        """Set playlist privacy: `public` or `private`"""
        pl = await self._resolve_playlist_access(ctx, playlist_id, require_owner=True)
        if not pl:
            return
        setting = setting.lower()
        if setting not in ('public', 'private'):
            await ctx.send(embed=embeds.error('Setting must be `public` or `private`.'))
            return
        await db.set_playlist_privacy(playlist_id, setting == 'public')
        icon = '🌐' if setting == 'public' else '🔒'
        await ctx.send(embed=embeds.success(f'**{pl["name"]}** is now {icon} **{setting}**.'))

    @playlist.command(name='rename')
    async def pl_rename(self, ctx: commands.Context, playlist_id: str, *, new_name: str):
        """Rename a playlist you own."""
        pl = await self._resolve_playlist_access(ctx, playlist_id, require_owner=True)
        if not pl:
            return
        await db.rename_playlist(playlist_id, new_name)
        await ctx.send(embed=embeds.success(f'Renamed to **{new_name}**.'))

    @playlist.command(name='share')
    async def pl_share(self, ctx: commands.Context, playlist_id: str):
        """Get a shareable ID for a playlist."""
        pl = await self._resolve_playlist_access(ctx, playlist_id)
        if not pl:
            return
        e = discord.Embed(title='🔗 Share This Playlist', color=embeds.GOLD)
        e.add_field(name='Playlist', value=pl['name'], inline=False)
        e.add_field(name='ID', value=f'`{pl["id"]}`', inline=True)
        privacy = '🌐 Public' if pl['is_public'] else '🔒 Private'
        e.add_field(name='Privacy', value=privacy, inline=True)
        e.add_field(
            name='How to play',
            value=f'Anyone can play this with:\n`!playlist play {pl["id"]}`',
            inline=False
        )
        await ctx.send(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Playlist(bot))
