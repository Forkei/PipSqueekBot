"""
PipSqueek Mk2 — Gemini-powered DJ agent.
"""
import asyncio
import os
import discord
from discord.ext import commands
from google import genai
from google.genai import types

from utils.agent_tools import ToolContext, TOOLS, dispatch
from utils.agent_context import build_context
from utils import embeds

# Bot prefix (duplicated here to avoid circular imports with bot.py)
_PREFIX = os.getenv('BOT_PREFIX', 'pip').strip() + ' '

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), '..', 'system_prompt.md')
_SYSTEM_PROMPT = open(_PROMPT_PATH, encoding='utf-8').read().replace('\\#', '#').replace('\\_', '_')

MAX_TOOL_ROUNDS = 10
_GEMINI_MODEL = 'gemini-3.1-pro-preview'
_THINKING_BUDGET = 2000  # tokens Gemini uses to reason before acting

_client: genai.Client | None = None



def _get_gemini_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv('GEMINI_API_KEY', '').strip()
        if not api_key:
            raise RuntimeError('GEMINI_API_KEY not set')
        _client = genai.Client(api_key=api_key)
    return _client


class Agent(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._mode: dict[int, str] = {}           # guild_id -> 'mk1' | 'mk2'
        self._agent_channel: dict[int, int] = {}  # guild_id -> channel_id
        self._wakeup_handles: dict[int, asyncio.TimerHandle] = {}       # agent-requested
        self._auto_wakeup_handles: dict[int, asyncio.TimerHandle] = {}  # 95% song timer
        self._running: set[int] = set()           # guilds with agent loop active

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
            print(f'[agent] already running, skip')
            return

        print(f'[agent] triggering for: {message.content[:50]!r}')
        asyncio.create_task(self._run_agent(guild_id, message))

    def _should_trigger(self, message: discord.Message, guild_id: int) -> bool:
        # In configured agent channel
        channel_id = self._agent_channel.get(guild_id)
        if channel_id and message.channel.id == channel_id:
            return True
        # Bot mentioned
        if self.bot.user in message.mentions:
            return True
        # Pip-prefixed
        if message.content.lower().startswith(_PREFIX.lower()):
            return True
        return False

    # ─── Wakeup scheduling ─────────────────────────────────────────────────────

    def _cancel_wakeup(self, guild_id: int):
        handle = self._wakeup_handles.pop(guild_id, None)
        if handle:
            handle.cancel()

    def _schedule_wakeup(self, guild_id: int, seconds: int, reason: str):
        self._cancel_wakeup(guild_id)
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            seconds,
            lambda: asyncio.create_task(self._wakeup_fire(guild_id, reason))
        )
        self._wakeup_handles[guild_id] = handle

    def schedule_auto_wakeup(self, guild_id: int, seconds: int, reason: str):
        """Called by music cog for the 95% song timer — separate from agent wakeups."""
        handle = self._auto_wakeup_handles.pop(guild_id, None)
        if handle:
            handle.cancel()
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            seconds,
            lambda: asyncio.create_task(self._wakeup_fire(guild_id, reason))
        )
        self._auto_wakeup_handles[guild_id] = handle

    async def _wakeup_fire(self, guild_id: int, reason: str):
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
        await self._run_agent(guild_id, message=None, wakeup_reason=reason, channel_override=channel)

    # ─── Core agent loop ───────────────────────────────────────────────────────

    async def _run_agent(
        self,
        guild_id: int,
        message: discord.Message | None,
        wakeup_reason: str | None = None,
        channel_override: discord.TextChannel | None = None,
    ):
        if guild_id in self._running:
            return
        self._running.add(guild_id)

        try:
            await self._agent_loop(guild_id, message, wakeup_reason, channel_override)
        except Exception as e:
            print(f'[agent] Unhandled error in guild {guild_id}: {type(e).__name__}: {e}')
        finally:
            self._running.discard(guild_id)

    async def _agent_loop(
        self,
        guild_id: int,
        message: discord.Message | None,
        wakeup_reason: str | None,
        channel_override: discord.TextChannel | None,
    ):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        # Determine channel and author
        if message:
            channel = message.channel
            author = message.author
        elif channel_override:
            channel = channel_override
            author = None
        else:
            return

        # Build ToolContext
        tc = ToolContext(
            guild=guild,
            channel=channel,
            author=author,
            bot=self.bot,
            trigger_message=message,
        )

        # Build context string
        ctx_str = await build_context(guild, author, message, wakeup_reason)

        # Save user message to conversation history and update user name mapping
        if message and author:
            from utils.database import save_conversation_turn, upsert_user
            await upsert_user(guild_id, author.id, author.display_name)
            await save_conversation_turn(
                guild_id, 'user',
                message.content[:500],
                author.display_name,
                author.id,
            )

        try:
            client = _get_gemini_client()
        except RuntimeError:
            return

        async with channel.typing():
            await self._agent_turns(
                guild_id, guild, channel, author, message, wakeup_reason, tc, ctx_str, client
            )

    async def _agent_turns(
        self,
        guild_id: int,
        guild: discord.Guild,
        channel,
        author,
        message,
        wakeup_reason,
        tc: 'ToolContext',
        ctx_str: str,
        client,
    ):
        contents = [types.Content(role='user', parts=[types.Part(text=ctx_str)])]

        pending_errors: list[str] = []  # errors from tools not yet surfaced to user
        message_sent = False            # tracks whether send_message was called this turn

        for _round in range(MAX_TOOL_ROUNDS):
            try:
                response = await client.aio.models.generate_content(
                    model=_GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=_SYSTEM_PROMPT,
                        tools=TOOLS,
                        tool_config=types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(mode='AUTO')
                        ),
                        thinking_config=types.ThinkingConfig(thinking_budget=_THINKING_BUDGET),
                        temperature=1.0,
                        max_output_tokens=8000,
                    )
                )
            except Exception as e:
                print(f'[agent] Gemini error: {type(e).__name__}: {e}')
                if _round == 0 and message:
                    await channel.send(f'something went wrong on my end ({type(e).__name__})')
                break

            candidate = response.candidates[0] if response.candidates else None
            if not candidate:
                print(f'[agent] no candidate in response')
                if _round == 0 and message:
                    await channel.send('got a blank response, try again')
                break

            parts = candidate.content.parts if candidate.content else []

            # Collect function calls and text from this response
            func_calls = [p for p in parts if p.function_call]
            text_parts = [p.text for p in parts if p.text and p.text.strip()]
            print(f'[agent] round {_round}: {len(func_calls)} func_calls, {len(text_parts)} text_parts, finish={candidate.finish_reason}')
            for fc in func_calls:
                print(f'  -> call: {fc.function_call.name}({dict(fc.function_call.args) if fc.function_call.args else {}})')
            for t in text_parts:
                print(f'  -> text: {t[:80]!r}')

            if not func_calls and not text_parts:
                break

            # If no function calls, agent is just sending text
            if not func_calls:
                text = ' '.join(text_parts)
                if text:
                    await channel.send(text)
                    from utils.database import save_conversation_turn
                    await save_conversation_turn(guild_id, 'model', text[:500])
                break

            # Execute all function calls
            function_responses = []
            is_done = False

            for fc in func_calls:
                name = fc.function_call.name
                args = dict(fc.function_call.args) if fc.function_call.args else {}
                result = await dispatch(name, args, tc)

                if name == 'done':
                    is_done = True
                elif name == 'send_message':
                    message_sent = True
                    pending_errors.clear()
                elif isinstance(result, str) and result.startswith('error:'):
                    pending_errors.append(result[6:].strip())

                function_responses.append(
                    types.Part.from_function_response(
                        name=name, response={'result': result}
                    )
                )

            # Append model turn + tool results for next round
            contents.append(candidate.content)
            contents.append(types.Content(role='user', parts=function_responses))

            # Handle wakeup scheduling/cancellation from this round
            if tc._wakeup_cancel:
                tc._wakeup_cancel = False
                self._cancel_wakeup(guild_id)
            elif tc._wakeup_scheduled:
                tc._wakeup_scheduled = False
                self._schedule_wakeup(guild_id, tc._wakeup_seconds, tc._wakeup_reason)

            if is_done:
                if text_parts:
                    text = ' '.join(text_parts)
                    await channel.send(text)
                    from utils.database import save_conversation_turn
                    await save_conversation_turn(guild_id, 'model', text[:500])
                elif pending_errors and not message_sent:
                    # Agent called done() without surfacing tool errors — post them as fallback
                    err_text = ' | '.join(pending_errors)
                    await channel.send(f'⚠️ {err_text}')
                break
        else:
            print(f'[agent] Hit max tool rounds ({MAX_TOOL_ROUNDS}) for guild {guild_id}')


async def setup(bot: commands.Bot):
    await bot.add_cog(Agent(bot))
