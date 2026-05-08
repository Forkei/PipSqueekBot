"""
PipSqueek Mk2 — LLM-powered DJ agent (streaming ReAct).
"""
import asyncio
import json
import os
import re
from collections import deque
from dataclasses import dataclass
import aiohttp
import discord
from discord.ext import commands

from utils.agent_tools import ToolContext, dispatch
from utils.agent_context import build_context
from utils import embeds

_PREFIX = os.getenv('BOT_PREFIX', 'pip').strip() + ' '

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), '..', 'system_prompt.md')
with open(_PROMPT_PATH, encoding='utf-8') as _f:
    _SYSTEM_PROMPT = re.sub(r'\\([^a-zA-Z0-9])', r'\1', _f.read())

MAX_ITERATIONS = 50
_MODEL = 'deepseek/deepseek-chat-v3-0324'
_OPENROUTER_BASE = 'https://openrouter.ai/api/v1'
_CONV_MAX_ENTRIES = 240  # more entries now — each tool call is 2 turns

# Tools whose single positional arg maps to this kwarg name
_SINGLE_PARAM = {
    'play_song': 'query',
    'send_message': 'content',
    'add_reaction': 'emoji',
    'set_volume': 'volume',
    'search_songs': 'query',
    'create_playlist': 'name',
    'play_playlist': 'playlist_id',
    'add_to_playlist': 'query',
    'get_recent_history': 'limit',
    'get_user_history': 'user_name',
    'store_memory': 'key',
    'retrieve_memory': 'key',
    'delete_memory': 'key',
    'schedule_wakeup': 'seconds',
    'sleep': 'seconds',
    'remove_from_queue': 'position',
    'set_loop_mode': 'mode',
    'get_liked_songs': 'user_name',
    'web_search': 'query',
    'poll': 'question',
    'leave_note': 'content',
}

_TOOL_RE = re.compile(r'^\$([a-z_]+)\((.*)\)\$$', re.DOTALL)
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

_READ_ONLY_TOOLS = {
    'done', 'get_queue', 'get_now_playing', 'search_songs',
    'get_recent_history', 'get_user_history', 'list_playlists',
    'retrieve_memory', 'list_memories', 'web_search', 'sleep',
    'get_liked_songs', 'get_recommendations',
}

def _get_openrouter_key() -> str:
    key = os.getenv('OPENROUTER_API_KEY', '').strip()
    if not key:
        raise RuntimeError('OPENROUTER_API_KEY not set')
    return key


def _parse_tool_call(text: str) -> tuple[str, dict] | None:
    """Parse '$tool_name(args)$'. Returns (name, kwargs) or None."""
    m = _TOOL_RE.match(text)
    if not m:
        return None
    name = m.group(1)
    args_str = m.group(2).strip()
    if not args_str:
        return name, {}
    try:
        parsed = json.loads(args_str)
        if isinstance(parsed, dict):
            return name, parsed
        # Single primitive value — map to first param
        param = _SINGLE_PARAM.get(name)
        if param:
            return name, {param: parsed}
        return name, {}
    except json.JSONDecodeError:
        return None


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
        self._session: aiohttp.ClientSession | None = None

    def _get_conversation(self, guild_id: int) -> list:
        return self._conversations.setdefault(guild_id, [])

    def _trim_conversation(self, guild_id: int):
        conv = self._conversations.get(guild_id)
        if conv and len(conv) > _CONV_MAX_ENTRIES:
            self._conversations[guild_id] = conv[-_CONV_MAX_ENTRIES:]

    async def cog_load(self):
        key = os.getenv('OPENROUTER_API_KEY', '').strip()
        if key:
            self._session = aiohttp.ClientSession(headers={
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
            })
        from utils.database import load_all_guild_configs
        rows = await load_all_guild_configs()
        for row in rows:
            self._mode[row['guild_id']] = row['mode']
            if row['agent_channel_id']:
                self._agent_channel[row['guild_id']] = row['agent_channel_id']
        if rows:
            print(f'  [agent] Restored config for {len(rows)} guild(s)')

    async def cog_unload(self):
        if self._session:
            await self._session.close()

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
            if not os.getenv('OPENROUTER_API_KEY', '').strip():
                await ctx.send(embed=embeds.error(
                    'Mk2 needs an OpenRouter API key.\n'
                    'Add `OPENROUTER_API_KEY=your_key` to `.env` and restart.'
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
        asyncio.create_task(self._run_agent(guild_id, message))

    def _should_trigger(self, message: discord.Message, guild_id: int) -> bool:
        channel_id = self._agent_channel.get(guild_id)
        if channel_id and message.channel.id == channel_id:
            return True
        if self.bot.user in message.mentions:
            return True
        if message.content.lower().startswith(_PREFIX.lower()):
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
        if guild_id in self._running:
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
        if guild_id in self._running:
            return
        self._running.add(guild_id)

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

        from utils.database import save_conversation_turn, upsert_user
        conv = self._get_conversation(guild_id)

        if voice_trigger and author:
            await upsert_user(guild_id, author.id, author.display_name, author.name)
            display = author.display_name
            if author.name != display:
                display = f'{display} (@{author.name})'
            trigger = {
                'type': 'voice_message',
                'username': display,
                'text': transcript,
            }
            await save_conversation_turn(guild_id, 'user', transcript[:500], author.display_name, author.id)
        elif message and author:
            await upsert_user(guild_id, author.id, author.display_name, author.name)
            display = author.display_name
            if author.name != display:
                display = f'{display} (@{author.name})'
            text = _resolve_mentions(message.content, guild)
            trigger = {
                'type': 'user_message',
                'username': display,
                'text': text,
                'uuid': str(message.id),
            }
            await save_conversation_turn(guild_id, 'user', text[:500], author.display_name, author.id)
        else:
            trigger = {'type': 'wakeup'}
            await save_conversation_turn(guild_id, 'system', '[wakeup]')

        conv.append({'role': 'user', 'content': json.dumps(trigger)})
        self._trim_conversation(guild_id)

        if not self._session:
            try:
                _get_openrouter_key()
            except RuntimeError:
                return
            key = os.getenv('OPENROUTER_API_KEY', '').strip()
            self._session = aiohttp.ClientSession(headers={
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
            })

        try:
            async with channel.typing():
                await self._agent_turns(guild_id, guild, channel, author, tc)
        except discord.HTTPException as e:
            if e.status == 429:
                # Rate limited on typing indicator — proceed without it
                await self._agent_turns(guild_id, guild, channel, author, tc)
            else:
                raise

    # ─── Streaming ReAct loop ──────────────────────────────────────────────────

    async def _stream_openrouter(self, messages: list, system_prompt: str):
        """Async generator yielding text chunks from OpenRouter SSE."""
        payload = {
            'model': _MODEL,
            'messages': [{'role': 'system', 'content': system_prompt}] + messages,
            'stream': True,
            'temperature': 0.6,
            'max_tokens': 8000,
        }
        async with self._session.post(
            f'{_OPENROUTER_BASE}/chat/completions', json=payload
        ) as resp:
            async for raw in resp.content:
                line = raw.decode('utf-8').strip()
                if not line.startswith('data: ') or line == 'data: [DONE]':
                    continue
                try:
                    data = json.loads(line[6:])
                    delta = data['choices'][0]['delta'].get('content') or ''
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    async def _consume_until_tool(self, stream) -> tuple[str, str | None, dict]:
        """
        Read stream until a complete $tool(args)$ is found or stream ends.
        Returns (text_accumulated_including_tool_call, tool_name_or_None, args_dict).
        """
        buf = ""
        tool_buf = ""
        in_tool = False

        try:
            async for chunk in stream:
                for ch in chunk:
                    if not in_tool:
                        if ch == '$':
                            in_tool = True
                            tool_buf = '$'
                        else:
                            buf += ch
                    else:
                        tool_buf += ch
                        if ch == '$' and len(tool_buf) > 3 and tool_buf[-2] == ')':
                            parsed = _parse_tool_call(tool_buf)
                            if parsed:
                                name, args = parsed
                                return buf + tool_buf, name, args
                            else:
                                buf += tool_buf
                                tool_buf = ""
                                in_tool = False
        except Exception as e:
            print(f'[agent] stream error: {type(e).__name__}: {e}')

        return buf + tool_buf, None, {}

    async def _agent_turns(
        self,
        guild_id: int,
        guild: discord.Guild,
        channel,
        author,
        tc: ToolContext,
    ):
        action_log: list[str] = []
        message_sent = False
        pending_errors: list[str] = []

        conv = self._get_conversation(guild_id)
        assistant_text = ""  # grows into one big model message

        status = await build_context(guild, author)
        system_instruction = _SYSTEM_PROMPT + '\n\n' + status

        for _iter in range(MAX_ITERATIONS):
            messages = list(conv)
            if assistant_text:
                messages.append({'role': 'assistant', 'content': assistant_text})

            try:
                stream = self._stream_openrouter(messages, system_instruction)
            except Exception as e:
                print(f'[agent] OpenRouter error: {type(e).__name__}: {e}')
                if _iter == 0:
                    await channel.send(f'something went wrong ({type(e).__name__})')
                break

            new_text, tool_name, tool_args = await self._consume_until_tool(stream)
            assistant_text += new_text

            if not tool_name:
                print(f'[agent] iter {_iter} — stream ended without tool call')
                break

            print(f'[agent] iter {_iter} | {tool_name}({tool_args})')

            if tool_name == 'done':
                if pending_errors and not message_sent:
                    await channel.send(f'⚠️ {" | ".join(pending_errors)}')
                break

            result = await dispatch(tool_name, tool_args, tc)

            # Append result inline — stays part of the single model message
            assistant_text += f'[{result}]'

            # Handle wakeup flags
            if tc._wakeup_cancel:
                tc._wakeup_cancel = False
                self._cancel_wakeup(guild_id)
            elif tc._wakeup_scheduled:
                tc._wakeup_scheduled = False
                self._schedule_wakeup(guild_id, tc._wakeup_seconds)

            # Track actions
            if tool_name == 'send_message':
                message_sent = True
                pending_errors.clear()
                action_log.append(f'said: {tool_args.get("content", "")[:200]}')
            elif tool_name == 'add_reaction':
                action_log.append(f'reacted: {tool_args.get("emoji", "")}')
            elif tool_name not in _READ_ONLY_TOOLS:
                if isinstance(result, str) and result.startswith('error:'):
                    pending_errors.append(result[6:].strip())
                    action_log.append(f'error({tool_name}): {result[6:].strip()[:100]}')
                else:
                    action_log.append(result[:150] if isinstance(result, str) else tool_name)
            elif isinstance(result, str) and result.startswith('error:'):
                pending_errors.append(result[6:].strip())
        else:
            print(f'[agent] Hit max iterations ({MAX_ITERATIONS}) for guild {guild_id}')

        if assistant_text:
            conv.append({'role': 'assistant', 'content': assistant_text})
            self._trim_conversation(guild_id)
            print(f'[agent] assistant turn: {assistant_text[:300]!r}')

        if action_log:
            from utils.database import save_conversation_turn
            await save_conversation_turn(guild_id, 'model', ' | '.join(action_log)[:500])


async def setup(bot: commands.Bot):
    await bot.add_cog(Agent(bot))
