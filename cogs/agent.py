"""
PipSqueek Mk2 — Gemini-powered DJ agent (streaming ReAct).
"""
import asyncio
import json
import os
import re
from collections import deque
import discord
from discord.ext import commands
from google import genai
from google.genai import types

from utils.agent_tools import ToolContext, dispatch
from utils.agent_context import build_context
from utils import embeds

_PREFIX = os.getenv('BOT_PREFIX', 'pip').strip() + ' '

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), '..', 'system_prompt.md')
_SYSTEM_PROMPT = re.sub(r'\\([^a-zA-Z0-9])', r'\1', open(_PROMPT_PATH, encoding='utf-8').read())

MAX_ITERATIONS = 50
_GEMINI_MODEL = 'gemini-3.1-pro-preview'
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
    'schedule_wakeup': 'seconds',
    'sleep': 'seconds',
    'remove_from_queue': 'position',
    'set_loop_mode': 'mode',
    'get_liked_songs': 'user_name',
    'web_search': 'query',
    'poll': 'question',
}

_TOOL_RE = re.compile(r'^\$([a-z_]+)\((.*)\)\$$', re.DOTALL)

_READ_ONLY_TOOLS = {
    'done', 'get_queue', 'get_now_playing', 'search_songs',
    'get_recent_history', 'get_user_history', 'list_playlists',
    'retrieve_memory', 'list_memories', 'web_search', 'sleep',
    'get_liked_songs', 'get_recommendations',
}

_client: genai.Client | None = None


def _get_gemini_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv('GEMINI_API_KEY', '').strip()
        if not api_key:
            raise RuntimeError('GEMINI_API_KEY not set')
        _client = genai.Client(api_key=api_key)
    return _client


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

    def _get_conversation(self, guild_id: int) -> list:
        return self._conversations.setdefault(guild_id, [])

    def _trim_conversation(self, guild_id: int):
        conv = self._conversations.get(guild_id)
        if conv and len(conv) > _CONV_MAX_ENTRIES:
            self._conversations[guild_id] = conv[-_CONV_MAX_ENTRIES:]

    async def cog_load(self):
        from utils.database import load_all_guild_configs
        rows = await load_all_guild_configs()
        for row in rows:
            self._mode[row['guild_id']] = row['mode']
            if row['agent_channel_id']:
                self._agent_channel[row['guild_id']] = row['agent_channel_id']
        if rows:
            print(f'  [agent] Restored config for {len(rows)} guild(s)')

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
            if not os.getenv('GEMINI_API_KEY', '').strip():
                await ctx.send(embed=embeds.error(
                    'Mk2 needs a Gemini API key.\n'
                    'Add `GEMINI_API_KEY=your_key` to `.env` and restart.'
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
        print(f'[agent] msg from {message.author.display_name} | mode={mode} | channel={message.channel.id} | agent_ch={self._agent_channel.get(guild_id)}')

        if mode != 'mk2':
            return

        if not self._should_trigger(message, guild_id):
            print(f'[agent] no trigger for: {message.content[:50]!r}')
            return

        if guild_id in self._running:
            print(f'[agent] already running, queuing message')
            self._pending.setdefault(guild_id, deque()).append(message)
            return

        print(f'[agent] triggering for: {message.content[:50]!r}')
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
        loop = asyncio.get_event_loop()
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
        loop = asyncio.get_event_loop()
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

    # ─── Core agent loop ───────────────────────────────────────────────────────

    async def _run_agent(
        self,
        guild_id: int,
        message: discord.Message | None,
        is_wakeup: bool = False,
        channel_override: discord.TextChannel | None = None,
    ):
        if guild_id in self._running:
            return
        self._running.add(guild_id)

        try:
            await self._agent_loop(guild_id, message, is_wakeup, channel_override)
        except Exception as e:
            print(f'[agent] Unhandled error in guild {guild_id}: {type(e).__name__}: {e}')
        finally:
            self._running.discard(guild_id)
            pending = self._pending.get(guild_id)
            if pending:
                next_msg = pending.popleft()
                asyncio.create_task(self._run_agent(guild_id, next_msg))

    async def _agent_loop(
        self,
        guild_id: int,
        message: discord.Message | None,
        is_wakeup: bool,
        channel_override: discord.TextChannel | None,
    ):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        if message:
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

        if message and author:
            await upsert_user(guild_id, author.id, author.display_name, author.name)
            trigger = {
                'type': 'user_message',
                'username': author.display_name,
                'text': message.content,
                'uuid': str(message.id),
            }
            await save_conversation_turn(guild_id, 'user', message.content[:500], author.display_name, author.id)
        else:
            trigger = {'type': 'wakeup'}
            await save_conversation_turn(guild_id, 'system', '[wakeup]')

        conv.append(types.Content(role='user', parts=[types.Part(text=json.dumps(trigger))]))
        self._trim_conversation(guild_id)

        try:
            client = _get_gemini_client()
        except RuntimeError:
            return

        async with channel.typing():
            await self._agent_turns(guild_id, guild, channel, author, tc, client)

    # ─── Streaming ReAct loop ──────────────────────────────────────────────────

    async def _consume_until_tool(self, stream) -> tuple[str, str | None, dict]:
        """
        Read stream until a complete $tool(args)$ is found or stream ends.
        Returns (text_accumulated_including_tool_call, tool_name_or_None, args_dict).
        The tool call syntax is included at the end of the returned text.
        """
        buf = ""       # all text so far (before + tool call)
        tool_buf = ""  # current potential tool call being built
        in_tool = False

        try:
            async for chunk in stream:
                for ch in (chunk.text or ""):
                    if not in_tool:
                        if ch == '$':
                            in_tool = True
                            tool_buf = '$'
                        else:
                            buf += ch
                    else:
                        tool_buf += ch
                        # Complete tool call ends with )$
                        if ch == '$' and len(tool_buf) > 3 and tool_buf[-2] == ')':
                            parsed = _parse_tool_call(tool_buf)
                            if parsed:
                                name, args = parsed
                                return buf + tool_buf, name, args
                            else:
                                # Not a valid tool, treat as plain text
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
        client,
    ):
        action_log: list[str] = []
        message_sent = False
        pending_errors: list[str] = []

        conv = self._get_conversation(guild_id)
        assistant_text = ""  # grows into one big model message

        for _iter in range(MAX_ITERATIONS):
            status = await build_context(guild, author)
            system_instruction = _SYSTEM_PROMPT + '\n\n' + status

            # Pass conversation + current partial assistant turn as prefix
            contents = list(conv)
            if assistant_text:
                contents.append(types.Content(
                    role='model',
                    parts=[types.Part(text=assistant_text)]
                ))

            try:
                stream = await client.aio.models.generate_content_stream(
                    model=_GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.6,
                        max_output_tokens=8000,
                    )
                )
            except Exception as e:
                print(f'[agent] Gemini error: {type(e).__name__}: {e}')
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

        # Store the full turn as one model Content
        if assistant_text:
            conv.append(types.Content(role='model', parts=[types.Part(text=assistant_text)]))
            self._trim_conversation(guild_id)
            print(f'[agent] assistant turn: {assistant_text[:300]!r}')

        if action_log:
            from utils.database import save_conversation_turn
            await save_conversation_turn(guild_id, 'model', ' | '.join(action_log)[:500])


async def setup(bot: commands.Bot):
    await bot.add_cog(Agent(bot))
