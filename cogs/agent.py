"""
PipSqueek Mk2 — LLM-powered DJ agent (function-calling).
"""
import asyncio
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass
import discord
from discord.ext import commands

from utils.agent_tools import ToolContext, dispatch, resolve_track
from utils.agent_context import build_context
from utils import embeds, llm, persona

_PREFIX = os.getenv('BOT_PREFIX', 'pip').strip() + ' '

_AGENT_RULES_PATH = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'agent.md')
with open(_AGENT_RULES_PATH, encoding='utf-8') as _f:
    _SYSTEM_PROMPT = persona.CHARACTER + '\n\n' + _f.read()

MAX_ITERATIONS = 50
_CONV_MAX_ENTRIES = 240

# Text like "# (silent check-in, queue is healthy)" or "[no action needed]"
_INTERNAL_NOTE_RE = re.compile(r'^[\s#*_>-]*[\(\[].*[\)\]][\s*_.]*$', re.DOTALL)

_MENTION_USER_RE = re.compile(r'<@!?(\d+)>')
_MENTION_ROLE_RE = re.compile(r'<@&(\d+)>')
_MENTION_CHAN_RE = re.compile(r'<#(\d+)>')


@dataclass
class _VoiceMessage:
    """Stands in for a discord.Message when the trigger came from voice STT."""
    guild_id: int
    channel: discord.TextChannel
    user: discord.Member
    transcript: str


def _resolve_mentions(content: str, guild: discord.Guild) -> str:
    def _user(m):
        member = guild.get_member(int(m.group(1)))
        return f'@{member.name}' if member else m.group(0)

    def _role(m):
        role = guild.get_role(int(m.group(1)))
        return f'@{role.name}' if role else m.group(0)

    def _chan(m):
        ch = guild.get_channel(int(m.group(1)))
        return f'#{ch.name}' if ch else m.group(0)

    content = _MENTION_USER_RE.sub(_user, content)
    content = _MENTION_ROLE_RE.sub(_role, content)
    content = _MENTION_CHAN_RE.sub(_chan, content)
    return content


_TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "play_song", "description": "Queue a song by title/artist or URL. Any length works, including long mixes and full albums.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "start": {"type": "string", "description": "Optional start time, e.g. '1:30' or '90'. YouTube links with ?t= are respected automatically."}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "seek", "description": "Jump within the song that's playing now. Absolute ('1:30', '90') or relative ('+30' forward, '-15' back).", "parameters": {"type": "object", "properties": {"position": {"type": "string"}}, "required": ["position"]}}},
    {"type": "function", "function": {"name": "skip_song", "description": "Skip the current song.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "pause_playback", "description": "Pause playback.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "resume_playback", "description": "Resume playback.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "stop_and_leave", "description": "Stop playback, clear queue, disconnect from voice.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_volume", "description": "Set volume 0-100.", "parameters": {"type": "object", "properties": {"volume": {"type": "integer"}}, "required": ["volume"]}}},
    {"type": "function", "function": {"name": "shuffle_queue", "description": "Shuffle the queue.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_loop_mode", "description": "Set loop mode.", "parameters": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["off", "one", "all"]}}, "required": ["mode"]}}},
    {"type": "function", "function": {"name": "toggle_autoplay", "description": "Toggle autoplay when queue ends.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "clear_queue", "description": "Clear queue without stopping current song.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "remove_from_queue", "description": "Remove track by 1-based position.", "parameters": {"type": "object", "properties": {"position": {"type": "integer"}}, "required": ["position"]}}},
    {"type": "function", "function": {"name": "move_in_queue", "description": "Move a track within the queue.", "parameters": {"type": "object", "properties": {"from_pos": {"type": "integer"}, "to_pos": {"type": "integer"}}, "required": ["from_pos", "to_pos"]}}},
    {"type": "function", "function": {"name": "join_voice", "description": "Join the requester's voice channel.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "leave_voice", "description": "Leave voice channel.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_queue", "description": "Get current queue contents.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_now_playing", "description": "Get current song info.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "search_songs", "description": "Search YouTube without playing. Returns titles + URLs. Always use the URL (not the title) when calling play_song.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "description": "1-5"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "create_playlist", "description": "Create a server playlist.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "list_playlists", "description": "List all server playlists with IDs.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "play_playlist", "description": "Queue all songs from a playlist. Only use IDs from list_playlists.", "parameters": {"type": "object", "properties": {"playlist_id": {"type": "string"}, "shuffle": {"type": "boolean"}}, "required": ["playlist_id"]}}},
    {"type": "function", "function": {"name": "add_to_playlist", "description": "Add a song to a playlist.", "parameters": {"type": "object", "properties": {"playlist_id": {"type": "string"}, "query": {"type": "string"}}, "required": ["playlist_id", "query"]}}},
    {"type": "function", "function": {"name": "get_recent_history", "description": "Recent server play history.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "get_user_history", "description": "A specific user's play history.", "parameters": {"type": "object", "properties": {"user_name": {"type": "string"}}, "required": ["user_name"]}}},
    {"type": "function", "function": {"name": "store_memory", "description": "Persist a note about a user or preference.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "retrieve_memory", "description": "Get a stored memory by key.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "list_memories", "description": "List all stored memory keys.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "delete_memory", "description": "Delete a stored memory by key.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "schedule_wakeup", "description": "Wake yourself up in N seconds (5-3600) to check in proactively.", "parameters": {"type": "object", "properties": {"seconds": {"type": "integer"}}, "required": ["seconds"]}}},
    {"type": "function", "function": {"name": "cancel_wakeup", "description": "Cancel any pending wakeup.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "send_message", "description": "Send a text message to the Discord channel.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "add_reaction", "description": "React to the triggering message with an emoji.", "parameters": {"type": "object", "properties": {"emoji": {"type": "string"}}, "required": ["emoji"]}}},
    {"type": "function", "function": {"name": "poll", "description": "Post a vote poll. 2-3 options max.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "string", "description": "Comma-separated options"}}, "required": ["question", "options"]}}},
    {"type": "function", "function": {"name": "get_liked_songs", "description": "Get liked songs (❤️ reactions on now-playing). Omit username for everyone's.", "parameters": {"type": "object", "properties": {"user_name": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_recommendations", "description": "Get 8 song suggestions based on who's in voice and their taste. Use before queuing on wakeup.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "web_search", "description": "Search the web for real-time info: release dates, artist bios, lyrics, samples, tour dates. Always reply with what you find.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "sleep", "description": "Pause for N seconds (1-30) before continuing.", "parameters": {"type": "object", "properties": {"seconds": {"type": "integer"}}, "required": ["seconds"]}}},
    {"type": "function", "function": {"name": "leave_note", "description": "Leave a note for the developer about operational observations.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "add_lore", "description": "Save a memorable server moment (inside joke, running bit, feud, something funny someone did) to call back to later.", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "One sentence, with names"}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "shift_mood", "description": "Nudge your own mood when someone is genuinely nice or genuinely mean to you.", "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down"]}, "reason": {"type": "string", "description": "Short, e.g. 'sam called you the best dj'"}}, "required": ["direction", "reason"]}}},
    {"type": "function", "function": {"name": "start_guess_game", "description": "Start a guess-the-song game in voice. You pick the songs from the server's taste; the game runs itself.", "parameters": {"type": "object", "properties": {"rounds": {"type": "integer", "description": "3-10, default 5"}}}}},
]


def _now_str() -> str:
    return time.strftime('%a %Y-%m-%d %H:%M')


class Agent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._mode: dict[int, str] = {}
        self._agent_channel: dict[int, int] = {}
        self._wakeup_handles: dict[int, asyncio.TimerHandle] = {}
        self._auto_wakeup_handles: dict[int, asyncio.TimerHandle] = {}
        self._running: set[int] = set()
        self._pending: dict[int, deque] = {}
        self._conversations: dict[int, list] = {}

    def _get_conversation(self, guild_id: int) -> list:
        return self._conversations.setdefault(guild_id, [])

    def _trim_conversation(self, guild_id: int):
        conv = self._conversations.get(guild_id)
        if not conv or len(conv) <= _CONV_MAX_ENTRIES:
            return
        # Only trim at a user-message boundary to avoid orphaning tool results
        target = len(conv) - _CONV_MAX_ENTRIES
        while target < len(conv) and conv[target].get('role') != 'user':
            target += 1
        if target < len(conv):
            self._conversations[guild_id] = conv[target:]

    async def _save_conversation(self, guild_id: int):
        from utils.database import save_agent_conversation
        try:
            await save_agent_conversation(guild_id, json.dumps(self._get_conversation(guild_id)))
        except Exception as e:
            print(f'[agent] failed to persist conversation: {type(e).__name__}: {e}')

    async def cog_load(self):
        from utils.database import load_all_guild_configs, load_agent_conversations
        rows = await load_all_guild_configs()
        for row in rows:
            self._mode[row['guild_id']] = row['mode']
            if row['agent_channel_id']:
                self._agent_channel[row['guild_id']] = row['agent_channel_id']
        if rows:
            print(f'  [agent] Restored config for {len(rows)} guild(s)')
        for row in await load_agent_conversations():
            try:
                self._conversations[row['guild_id']] = json.loads(row['messages_json'])
            except json.JSONDecodeError:
                pass
        if self._conversations:
            print(f'  [agent] Restored conversation memory for {len(self._conversations)} guild(s)')

    async def cog_unload(self):
        await llm.close()

    def get_mode(self, guild_id: int) -> str:
        return self._mode.get(guild_id, 'mk1')

    # ─── Mode command ──────────────────────────────────────────────────────────

    @commands.command(name='mode')
    async def mode_cmd(self, ctx: commands.Context, target: str = None):
        """Switch between Mk1 (classic) and Mk2 (AI agent) mode."""
        guild_id = ctx.guild.id
        current = self.get_mode(guild_id)

        if target is None:
            mk = '🤖 **Mk2** (AI agent)' if current == 'mk2' else '🎛️ **Mk1** (classic)'
            channel_id = self._agent_channel.get(guild_id)
            channel_info = f' | channel: <#{channel_id}>' if channel_id else ''
            await ctx.send(embed=embeds.info(f'Current mode: {mk}{channel_info}'))
            return

        target = target.lower()
        if target == 'mk1':
            self._mode[guild_id] = 'mk1'
            self._cancel_wakeup(guild_id)
            from utils.database import save_guild_config
            await save_guild_config(guild_id, 'mk1', self._agent_channel.get(guild_id))
            await ctx.send(embed=embeds.success('Switched to **Mk1** — classic command mode.'))
        elif target == 'mk2':
            if not llm.is_configured():
                await ctx.send(embed=embeds.error(
                    'Mk2 needs an LLM API key.\n'
                    'Add `GEMINI_API_KEY` (or `OPENROUTER_API_KEY`) to `.env` and restart.'
                ))
                return
            self._mode[guild_id] = 'mk2'
            self._agent_channel[guild_id] = ctx.channel.id
            from utils.database import save_guild_config
            await save_guild_config(guild_id, 'mk2', ctx.channel.id)
            await ctx.send(embed=embeds.success(
                f'🤖 Switched to **Mk2** — AI agent mode.\n'
                f'PipSqueek will respond in this channel.\n'
                f'Talk naturally, @mention me, or use `pip` commands.\n'
                f'Say `pip mode mk1` to switch back.'
            ))
        else:
            await ctx.send(embed=embeds.error('Use `pip mode mk1` or `pip mode mk2`.'))

    # ─── Agent channel config ──────────────────────────────────────────────────

    @commands.command(name='notes')
    async def notes_cmd(self, ctx: commands.Context, *, args: str = ''):
        """Read or clear PipSqueek's developer notes. Usage: pip notes [clear]"""
        from utils.database import get_dev_notes, clear_dev_notes
        import datetime
        guild_id = ctx.guild.id
        if args.strip().lower() == 'clear':
            await clear_dev_notes(guild_id)
            await ctx.send(embed=embeds.success('Dev notes cleared.'))
            return
        notes = await get_dev_notes(guild_id, limit=20)
        if not notes:
            await ctx.send(embed=embeds.info('No dev notes yet.'))
            return
        lines = []
        for n in reversed(notes):
            ts = datetime.datetime.fromtimestamp(n['created_at']).strftime('%m/%d %H:%M')
            lines.append(f'`{ts}` {n["content"]}')
        e = discord.Embed(title='📝 Dev Notes', description='\n'.join(lines), color=embeds.GOLD)
        e.set_footer(text=f'{len(notes)} note(s) · pip notes clear to wipe')
        await ctx.send(embed=e)

    @commands.command(name='setchannel')
    async def setchannel(self, ctx: commands.Context):
        """Set the current channel as the Mk2 agent channel."""
        guild_id = ctx.guild.id
        self._agent_channel[guild_id] = ctx.channel.id
        from utils.database import save_guild_config
        await save_guild_config(guild_id, self.get_mode(guild_id), ctx.channel.id)
        await ctx.send(embed=embeds.success(f'Agent channel set to <#{ctx.channel.id}>.'))

    @commands.command(name='forget')
    async def forget_cmd(self, ctx: commands.Context):
        """Wipe Pip's conversation memory for this server (lore, memories and mood stay)."""
        from utils.database import clear_agent_conversation
        self._conversations.pop(ctx.guild.id, None)
        await clear_agent_conversation(ctx.guild.id)
        await ctx.send(embed=embeds.success('conversation wiped. who are you people'))

    # ─── Message listener ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id
        mode = self.get_mode(guild_id)

        if mode != 'mk2':
            return

        if not self._should_trigger(message, guild_id):
            return

        if guild_id in self._running:
            self._pending.setdefault(guild_id, deque()).append(message)
            return
        self._running.add(guild_id)
        asyncio.create_task(self._run_agent(guild_id, message))

    def _should_trigger(self, message: discord.Message, guild_id: int) -> bool:
        # pip-prefixed messages are handled as commands, not agent input
        if message.content.lower().startswith(_PREFIX.lower()):
            return False
        # Guesses during a game belong to the game, not the agent
        from cogs.music import get_player
        if get_player(guild_id).game_channel_id == message.channel.id:
            return False
        channel_id = self._agent_channel.get(guild_id)
        if channel_id and message.channel.id == channel_id:
            return True
        if self.bot.user in message.mentions:
            return True
        return False

    # ─── Wakeup scheduling ─────────────────────────────────────────────────────

    def _cancel_wakeup(self, guild_id: int):
        handle = self._wakeup_handles.pop(guild_id, None)
        if handle:
            handle.cancel()

    def _schedule_wakeup(self, guild_id: int, seconds: int):
        self._cancel_wakeup(guild_id)
        loop = asyncio.get_running_loop()
        handle = loop.call_later(
            seconds,
            lambda: asyncio.create_task(self._wakeup_fire(guild_id))
        )
        self._wakeup_handles[guild_id] = handle

    def schedule_auto_wakeup(self, guild_id: int, seconds: int):
        """Called by music cog for the 95% song timer."""
        handle = self._auto_wakeup_handles.pop(guild_id, None)
        if handle:
            handle.cancel()
        loop = asyncio.get_running_loop()
        handle = loop.call_later(
            seconds,
            lambda: asyncio.create_task(self._wakeup_fire(guild_id))
        )
        self._auto_wakeup_handles[guild_id] = handle

    async def _wakeup_fire(self, guild_id: int):
        self._wakeup_handles.pop(guild_id, None)
        if self.get_mode(guild_id) != 'mk2':
            return
        from cogs.music import get_player
        if get_player(guild_id).game_channel_id:
            return
        if guild_id in self._running:
            # Busy with someone's message — try again shortly instead of dropping it
            self._schedule_wakeup(guild_id, 10)
            return
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel_id = self._agent_channel.get(guild_id)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        self._running.add(guild_id)
        await self._run_agent(guild_id, message=None, is_wakeup=True, channel_override=channel)

    # ─── Voice assistant entry point ───────────────────────────────────────────

    async def run_from_voice(
        self,
        guild_id: int,
        channel: discord.TextChannel,
        user: discord.Member,
        transcript: str,
    ):
        """Called by the voice assistant with a transcribed utterance."""
        if guild_id in self._running:
            # Queue it so it doesn't get dropped
            fake_msg = _VoiceMessage(guild_id, channel, user, transcript)
            self._pending.setdefault(guild_id, deque()).append(fake_msg)
            return
        self._running.add(guild_id)
        asyncio.create_task(self._run_agent(guild_id, message=None,
                                             voice_trigger=(channel, user, transcript)))

    # ─── Core agent loop ───────────────────────────────────────────────────────

    async def _run_agent(
        self,
        guild_id: int,
        message,  # discord.Message | _VoiceMessage | None
        is_wakeup: bool = False,
        channel_override: discord.TextChannel | None = None,
        voice_trigger: tuple | None = None,
    ):
        # Unwrap voice messages stored in the pending queue
        if isinstance(message, _VoiceMessage):
            voice_trigger = (message.channel, message.user, message.transcript)
            message = None

        try:
            await self._agent_loop(guild_id, message, is_wakeup, channel_override, voice_trigger)
        except Exception as e:
            print(f'[agent] Unhandled error in guild {guild_id}: {type(e).__name__}: {e}')
        finally:
            self._running.discard(guild_id)
            pending = self._pending.get(guild_id)
            if pending:
                next_item = pending.popleft()
                self._running.add(guild_id)
                asyncio.create_task(self._run_agent(guild_id, next_item))

    async def _agent_loop(
        self,
        guild_id: int,
        message: discord.Message | None,
        is_wakeup: bool,
        channel_override: discord.TextChannel | None,
        voice_trigger: tuple | None = None,
    ):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        if voice_trigger:
            channel, author, transcript = voice_trigger
        elif message:
            channel = message.channel
            author = message.author
        elif channel_override:
            channel = channel_override
            author = None
        else:
            return

        tc = ToolContext(
            guild=guild,
            channel=channel,
            author=author,
            bot=self.bot,
            trigger_message=message,
        )

        if not llm.is_configured():
            return

        from utils.database import upsert_user
        conv = self._get_conversation(guild_id)

        if voice_trigger and author:
            await upsert_user(guild_id, author.id, author.display_name, author.name)
            display = author.display_name
            if author.name != display:
                display = f'{display} (@{author.name})'
            trigger = {
                'type': 'voice_message',
                'time': _now_str(),
                'username': display,
                'text': transcript,
            }
        elif message and author:
            await upsert_user(guild_id, author.id, author.display_name, author.name)
            display = author.display_name
            if author.name != display:
                display = f'{display} (@{author.name})'
            trigger = {
                'type': 'user_message',
                'time': _now_str(),
                'username': display,
                'text': _resolve_mentions(message.content, guild),
                'uuid': str(message.id),
            }
        else:
            trigger = {'type': 'wakeup', 'time': _now_str()}

        conv.append({'role': 'user', 'content': json.dumps(trigger, ensure_ascii=False)})
        self._trim_conversation(guild_id)

        try:
            async with channel.typing():
                await self._agent_turns(guild_id, guild, channel, author, tc)
        except discord.HTTPException as e:
            if e.status == 429:
                # Rate limited on typing indicator — proceed without it
                await self._agent_turns(guild_id, guild, channel, author, tc)
            else:
                raise

    # ─── Function-calling loop ─────────────────────────────────────────────────

    async def _call_model(self, messages: list, system_prompt: str) -> dict:
        """One model call. Returns the assistant message (content, tool_calls, reasoning_details)."""
        return await llm.complete(
            [{'role': 'system', 'content': system_prompt}] + messages,
            tools=_TOOL_SCHEMAS,
            max_tokens=2000,
            temperature=1.0,  # Gemini 3 is tuned for 1.0; lower can cause loops
            effort='low',
        )

    async def _agent_turns(
        self,
        guild_id: int,
        guild: discord.Guild,
        channel,
        author,
        tc: ToolContext,
    ):
        message_sent = False
        pending_errors: list[str] = []

        conv = self._get_conversation(guild_id)
        status = await build_context(guild, author)
        system_instruction = _SYSTEM_PROMPT + '\n\n' + status

        for _iter in range(MAX_ITERATIONS):
            try:
                reply = await self._call_model(list(conv), system_instruction)
            except Exception as e:
                print(f'[agent] OpenRouter error: {type(e).__name__}: {e}')
                if _iter == 0:
                    await channel.send(persona.line('crash'))
                break

            content_text = (reply.get('content') or '').strip()
            tool_calls = reply.get('tool_calls') or []
            print(f'[agent] iter {_iter} | tools={[c["function"]["name"] for c in tool_calls]}')

            if not tool_calls:
                # Model is done. Post its final text only if a person triggered this turn
                # and nothing was sent yet. Otherwise it's a throwaway recap ("I've queued...")
                # or an internal note ("(silent check-in, queue is healthy)") — never post those.
                if content_text and not message_sent and author is not None \
                        and not _INTERNAL_NOTE_RE.match(content_text):
                    conv.append({'role': 'assistant', 'content': content_text})
                    await channel.send(content_text)
                    persona.remember_remark(guild_id, content_text)
                break

            # Pass tool calls back verbatim: Gemini attaches a thought_signature
            # (extra_content) to them and rejects the next turn without it
            assistant_msg = {
                'role': 'assistant',
                'content': content_text or None,
                'tool_calls': [{**c, 'type': 'function'} for c in tool_calls],
            }
            # Same idea via OpenRouter, which returns it as reasoning_details
            if reply.get('reasoning_details'):
                assistant_msg['reasoning_details'] = reply['reasoning_details']
            conv.append(assistant_msg)

            calls = []
            for c in tool_calls:
                try:
                    args = json.loads(c['function'].get('arguments') or '{}')
                except json.JSONDecodeError:
                    args = {}
                calls.append((c['id'], c['function']['name'], args if isinstance(args, dict) else {}))

            # Look up every song in this batch at once; play_song then queues them in order
            for _, name, args in calls:
                q = args.get('query')
                if name == 'play_song' and isinstance(q, str) and q not in tc.prefetch:
                    tc.prefetch[q] = asyncio.create_task(resolve_track(q))

            for call_id, name, args in calls:
                print(f'[agent]   {name}({args})')
                result = await dispatch(name, args, tc)

                conv.append({
                    'role': 'tool',
                    'tool_call_id': call_id,
                    'content': str(result),
                })

                # Handle wakeup flags
                if tc._wakeup_cancel:
                    tc._wakeup_cancel = False
                    self._cancel_wakeup(guild_id)
                elif tc._wakeup_scheduled:
                    tc._wakeup_scheduled = False
                    self._schedule_wakeup(guild_id, tc._wakeup_seconds)

                # Track actions
                if name == 'send_message':
                    message_sent = True
                    pending_errors.clear()
                elif isinstance(result, str) and result.startswith('error:'):
                    pending_errors.append(result[6:].strip())
        else:
            print(f'[agent] Hit max iterations ({MAX_ITERATIONS}) for guild {guild_id}')

        for task in tc.prefetch.values():
            task.cancel()

        if pending_errors and not message_sent:
            await channel.send(f'⚠️ {" | ".join(pending_errors)}')

        self._trim_conversation(guild_id)
        await self._save_conversation(guild_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Agent(bot))
