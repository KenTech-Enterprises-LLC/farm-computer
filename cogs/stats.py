"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at http://mozilla.org/MPL/2.0/.

This file was sourced from [RoboDanny](https://github.com/Rapptz/RoboDanny).

Written by @danny on Discord
Taken from https://github.com/Rapptz/RoboDanny/blob/rewrite/cogs/stats.py
"""


from __future__ import annotations
import asyncio
from collections import Counter
import csv
import datetime
import gc
from gettext import gettext as _
import io
import itertools
import json
import logging
import os
import re
import sys
import textwrap
import traceback
from typing import Any, Dict, List, Optional, Tuple, TypedDict, Union

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks
import pkg_resources
import psutil
import pygit2
from tortoise import Tortoise
from tortoise.functions import Count
from typing_extensions import Annotated

from cogs.models import Blacklist, Commands
from cogs.translations import get_translation_callable, intcomma
from main import currentdate
from utils import (
    BotU,
    CogU,
    ContextU,
    Cooldown,
    FiveButtonPaginator,
    GITHUB_URL,
    GUILDS,
    STATS_WEBHOOK_URL,
    SUPPORT_SERVER,
    command,
    create_paginator,
    danny_formats,
    danny_time,
    dchyperlink,
    dctimestamp,
    emojidict,
    generate_pages,
    generate_transaction_id,
    group,
    hybrid_command,
    makeembed,
    makeembed_bot,
    makeembed_failedaction,
    misc_flag_descriptions,
    oauth_url,
)

log = logging.getLogger(__name__)
#log.addHandler(handler) # we add this handler twice?

LOGGING_CHANNEL = 1277467073810923561

badges_to_emoji = {
    'partner': emojidict.get('partner'),
    'verified_bot_developer': emojidict.get('verified_bot_developer'),
    'hypesquad_balance': emojidict.get('hypesquad_balance'),
    'hypesquad_bravery': emojidict.get('hypesquad_bravery'),
    'hypesquad_brilliance': emojidict.get('hypesquad_brilliance'),
    'bug_hunter': emojidict.get('bug_hunter'),
    'hypesquad': emojidict.get('hypesquad'),
    'early_supporter': emojidict.get('early_supporter'),
    'bug_hunter_level_2': emojidict.get('bug_hunter_level_2'),
    'staff': emojidict.get('staff'),
    'discord_certified_moderator': emojidict.get('discord_certified_moderator'),
    'active_developer': emojidict.get('active_developer'),
}

class DataBatchEntry(TypedDict):
    guild: Optional[int]
    channel: int
    author: int
    used: str
    prefix: str
    command: str
    command_id: int
    failed: bool
    app_command: bool
    args: list[str]
    kwargs: dict[str, Any]
    transaction_id: str
    is_user_install: bool
    is_guild_install: bool


class LoggingHandler(logging.Handler):
    def __init__(self, cog: Stats):
        self.cog: Stats = cog
        super().__init__(logging.INFO)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name in ('discord.gateway', 'cogs.splatoon')

    def emit(self, record: logging.LogRecord) -> None:
        self.cog.add_record(record)


_INVITE_REGEX = re.compile(r'(?:https?:\/\/)?discord(?:\.gg|\.com|app\.com\/invite)?\/[A-Za-z0-9]+')


def censor_invite(obj: Any, *, _regex=_INVITE_REGEX) -> str:
    return _regex.sub('[censored-invite]', str(obj))


def hex_value(arg: str) -> int:
    return int(arg, base=16)

def object_at(addr: int) -> Optional[Any]:
    for o in gc.get_objects():
        if id(o) == addr:
            return o
    return None

class Stats(CogU, name="Statistics", hidden=True):
    """Bot usage statistics."""
    is_first_startup: bool = True

    def __init__(self, bot: BotU):
        self.bot: BotU = bot
        self.process = psutil.Process()
        self._batch_lock = asyncio.Lock()
        self._data_batch: list[DataBatchEntry] = []
        self.bulk_insert_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert_loop.start()
        self._logging_queue = asyncio.Queue()
        self.logging_worker.start()
        #self.log_new_authorized_users.start()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{BAR CHART}')

    async def bulk_insert(self) -> None:
        # query = """INSERT INTO commands (guild_id, channel_id, author_id, used, prefix, command, failed, app_command)
        #            SELECT x.guild, x.channel, x.author, x.used, x.prefix, x.command, x.failed, x.app_command
        #            FROM jsonb_to_recordset($1::jsonb) AS
        #            x(
        #                 guild BIGINT,
        #                 channel BIGINT,
        #                 author BIGINT,
        #                 used TIMESTAMP,
        #                 prefix TEXT,
        #                 command TEXT,
        #                 failed BOOLEAN,
        #                 app_command BOOLEAN
        #             )
        #         """

        # if self._data_batch:
        #     await self.bot.pool.execute(query, self._data_batch)
        #     total = len(self._data_batch)
        #     if total > 1:
        #         log.info('Registered %s commands to the database.', total)
        #     self._data_batch.clear()
        if self._data_batch:
            await Commands.bulk_insert(self._data_batch) # type: ignore
            total = len(self._data_batch)
            if total > 1:
                log.info('Registered %s commands to the database.', total)
            self._data_batch.clear()
        else:
            log.debug('No commands to insert.')

    async def cog_unload(self):
        self.bulk_insert_loop.stop()
        self.logging_worker.cancel()
        #self.log_new_authorized_users.stop()

    @tasks.loop(seconds=10.0)
    async def bulk_insert_loop(self):
        if not self._data_batch:
            return
        await self.do_bulk_insert()
        
    async def do_bulk_insert(self):
        async with self._batch_lock:
            await self.bulk_insert()

    @tasks.loop(seconds=0.0)
    async def logging_worker(self):
        record = await self._logging_queue.get()
        await self.send_log_record(record)

    async def register_command(self, ctx: ContextU) -> None:
        if ctx.command is None:
            return

        command = ctx.command.qualified_name
        is_app_command = ctx.interaction is not None
        self.bot.command_stats[command] += 1
        self.bot.command_types_used[is_app_command] += 1
        message = ctx.message
        destination = None
        if ctx.guild is None:
            destination = _('Private Message/User Application')
            guild_id = None
        else:
            destination = f'#{message.channel} ({message.guild})'
            guild_id = ctx.guild.id

        if ctx.interaction and ctx.interaction.command:
            content = f'/{ctx.interaction.command.qualified_name}'
        else:
            content = message.content

        log.info(f'{message.created_at}: {message.author} in {destination}: {content}')
        args = []
        kwargs = {}

        for key, value in ctx.kwargs.items():
            try:
                #if isinstance(value, (PlatformV2, Platform)):
                if value.__class__.__name__ == "Platform":
                    value = value.route
                else:
                    json.dumps(value) # ensure it's json serializable
                kwargs[key] = value
            except TypeError:
                if isinstance(value, (PlatformV2, Platform)):
                    kwargs[key] = value.route
                continue
    
        for entry in ctx.args:            
            try:
                #if isinstance(value, (PlatformV2, Platform)):
                if entry.__class__.__name__ == "Platform":
                    entry = entry.route
                json.dumps(entry) # ensure it's json serializable
                args.append(entry)
            except TypeError:
                continue
        
        # while True:
        #     transaction = (await CommandInvocation.filter(command_id=ctx.interaction.id if ctx.interaction else ctx.message.id, user_id=ctx.author.id, timestamp=message.created_at).first())
        #     if transaction:
        #         transaction_id = transaction.transaction_id
        #         break
        #     await asyncio.sleep(5)

        transaction_id = None

        async with self._batch_lock:
            for data in self._data_batch:
                if data['command_id'] == ctx.interaction.id if ctx.interaction else ctx.message.id and data['used'] == message.created_at:
                    transaction_id = data['transaction_id']
                    self._data_batch.remove(data)
                    if is_app_command:
                        assert ctx.interaction is not None
                        guild_install = ctx.interaction.is_guild_integration()
                        user_install = ctx.interaction.is_user_integration()
                    else:
                        guild_install = ctx.guild is not None
                        user_install = False
                    self._data_batch.append(
                        {
                            'guild': guild_id,
                            'channel': ctx.channel.id,
                            'author': ctx.author.id,
                            'used': message.created_at, # created_at 
                            'prefix': ctx.prefix,
                            'command': command,
                            'failed': ctx.command_failed,
                            'app_command': is_app_command,
                            'is_guild_install': guild_install,
                            'is_user_install': user_install,
                            'args': args,
                            'kwargs': kwargs,
                            'command_id': ctx.interaction.id if ctx.interaction else ctx.message.id,
                            'transaction_id': transaction_id,
                        } # type: ignore
                    )
                    return
    
        if not transaction_id:
            transaction_id = generate_transaction_id(guild_id=guild_id, user_id=ctx.author.id)

        async with self._batch_lock:
            if is_app_command:
                assert ctx.interaction is not None
                guild_install = ctx.interaction.is_guild_integration()
                user_install = ctx.interaction.is_user_integration()
            else:
                guild_install = ctx.guild is not None
                user_install = False
            self._data_batch.append(
                {
                    'guild': guild_id,
                    'channel': ctx.channel.id,
                    'author': ctx.author.id,
                    'used': message.created_at, # created_at 
                    'prefix': ctx.prefix,
                    'command': command,
                    'failed': ctx.command_failed,
                    'app_command': is_app_command,
                    'is_guild_install': guild_install,
                    'is_user_install': user_install,
                    'args': args,
                    'kwargs': kwargs,
                    'command_id': ctx.interaction.id if ctx.interaction else ctx.message.id,
                    'transaction_id': transaction_id,
                } # type: ignore
            )
        # await Commands.create(
        #     guild_id=guild_id,
        #     channel=ctx.channel.id,
        #     author_id=ctx.author.id,
        #     used=message.created_at,
        #     prefix=ctx.prefix,
        #     command=command,
        #     failed=ctx.command_failed,
        #     app_command=is_app_command,
        #     args=ctx.args,
        #     kwargs=ctx.kwargs,
        # )

    @commands.Cog.listener()
    async def on_command(self, ctx: ContextU):
        await self.register_command(ctx)

    # @commands.Cog.listener()
    # async def on_command_completion(self, ctx: ContextU):
    #     await self.register_command(ctx)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        command = interaction.command
        # Check if a command is found and it's not a hybrid command
        # Hybrid commands are already counted via on_command_completion
        if (
            command is not None
            and interaction.type is discord.InteractionType.application_command
            and not command.__class__.__name__.startswith('Hybrid')  # Kind of awful, but it'll do
        ):
            # This is technically bad, but since we only access Command.qualified_name and it's
            # available on all types of commands then it's fine
            ctx = await ContextU.from_interaction(interaction)
            ctx.command_failed = interaction.command_failed or ctx.command_failed
            await self.register_command(ctx)

    @commands.Cog.listener()
    async def on_socket_event_type(self, event_type: str):
        self.bot.socket_stats[event_type] += 1

    @commands.Cog.listener()
    async def on_ready(self):
        # if not hasattr(self, _('uptime')):
        #     self.uptime = discord.utils.utcnow()

        # if not self.log_new_authorized_users.is_running():
        #     self.log_new_authorized_users.start()

        if not hasattr(self, 'uptime'):
            self.uptime = discord.utils.utcnow()

        log.info('Ready: %s (ID: %s)', self.bot.user, self.bot.user.id)

        started_at = getattr(self.bot, 'started_at', currentdate)
        if started_at.tzinfo is None:
            #started_at = started_at.replace(tzinfo=datetime.timezone.utc)
            started_at = started_at.astimezone(datetime.timezone.utc)

        if self.is_first_startup:
            st = danny_time.human_timedelta(started_at, accuracy=None, brief=True, suffix=False)
            #desc += f"\n\nThis is the first time the bot has started up since the last reboot. Took `{st2}` (`{round((self.uptime- started_at).total_seconds(), 5)}s`)."
            desc = "Bot just started up."
            desc += f"\n\nStartup took `{st}`."
            self.is_first_startup = False
        else:
            st = danny_time.human_timedelta(self.uptime, accuracy=None, brief=True, suffix=False)
            desc = f"Bot has been up for `{st}`."
        
        appinfo = await self.bot.application_info()
        
        embed = makeembed_bot(title='Bot is ready!', description=desc, color=discord.Colour.blurple(), footer="Bot started", bot=self.bot, app_info=appinfo, timestamp=self.uptime)
        await self.webhook.send(embed=embed)

    async def on_shard_resumed(self, shard_id: int):
        log.info('Shard ID %s has resumed...', shard_id)
        self.bot.resumes[shard_id].append(discord.utils.utcnow())

    @discord.utils.cached_property
    def webhook(self) -> discord.Webhook:
        #wh_id, wh_token = self.bot.config.stat_webhook
        #hook = discord.Webhook.partial(id=wh_id, token=wh_token, session=self.bot.session)
        hook = discord.Webhook.from_url(STATS_WEBHOOK_URL, client=self.bot)
        return hook

    async def get_command_mention(self, command: Union[str, commands.Command]):
        old_command_mention = await super().get_command_mention(command)

        if '`' not in old_command_mention:
            return old_command_mention
        return f'{old_command_mention.replace("/", "")}'
        
    @command(name=_('commandstats'), hidden=True)
    @commands.is_owner()
    async def commandstats(self, ctx: ContextU, limit: int = 12):
        """Shows command stats.

        Use a negative number for bottom instead of top.
        This is only for the current session.
        """
        await ctx.defer()

        counter = self.bot.command_stats
        total = sum(counter.values())
        slash_commands = self.bot.command_types_used[True]

        delta = discord.utils.utcnow() - self.uptime
        minutes = delta.total_seconds() / 60
        cpm = total / minutes

        if limit > 0:
            common = counter.most_common(limit)
            title = _("Top {} Commands").format(limit)
        else:
            common = counter.most_common()[limit:]
            title = _("Bottom {} Commands").format(limit)

        lines = []
        for index, (command, value) in enumerate(common, 1):
            lines.append(_("{}. {}: `{}` uses").format(index, command, value))
        pages = generate_pages(lines, title=title)
        for page in pages:
            if page.description:
                page.description += _("\n\n`{}` total commands used (`{}` slash command uses) (`{}`/minute)").format(total, slash_commands, f"{cpm:.2f}")
        
        return await create_paginator(ctx, pages, FiveButtonPaginator, go_to_button=True)
        # pages = generate_pages(common, title=title, )
        # source = FieldPageSource(common, inline=True, clear_description=False)
        # source.embed.title = title
        # source.embed.description = f'{total} total commands used ({slash_commands} slash command uses) ({cpm:.2f}/minute)'

        # pages = RoboPages(source, ctx=ctx, compact=True)
        # await pages.start()



    @command(name='socketstats', hidden=True)
    async def socketstats(self, ctx: ContextU):
        delta = discord.utils.utcnow() - self.uptime
        minutes = delta.total_seconds() / 60
        total = sum(self.bot.socket_stats.values())
        cpm = total / minutes
        await ctx.reply(f'{total} socket events observed ({cpm:.2f}/minute):\n{self.bot.socket_stats}')

    def get_bot_uptime(self, *, brief: bool = False) -> str:
        return danny_time.human_timedelta(self.uptime, accuracy=None, brief=brief, suffix=False)

    @hybrid_command(name='uptime')
    async def uptime_cmd(self, ctx: ContextU):
        """Tells you how long the bot has been up for."""
        await ctx.defer(ephemeral=True)

        if not hasattr(self, 'uptime'):
            return await ctx.reply(embed=makeembed_failedaction(description='Bot has not connected to the gateway yet.'), delete_after=10, ephemeral=True)
        await ctx.reply(f'Bot has been up since {dctimestamp(self.uptime,"R")}.', ephemeral=True)
        #await ctx.reply(f'Uptime: **{self.get_bot_uptime()}** (since {dctimestamp(self.uptime,"R")})')

    def format_commit(self, commit: pygit2.Commit) -> str:
        short, _, _ = commit.message.partition('\n')
        short_sha2 = commit.hex[0:6]
        commit_tz = datetime.timezone(datetime.timedelta(minutes=commit.commit_time_offset))
        commit_time = datetime.datetime.fromtimestamp(commit.commit_time).astimezone(commit_tz)

        # [`hash`](url) message (offset)
        offset = danny_time.format_relative(commit_time.astimezone(datetime.timezone.utc))
        return f"{dchyperlink(f'{GITHUB_URL}/commit/{commit.hex}',f'`{short_sha2}`', )} {short} ({offset})"
        #return f'[`{short_sha2}`](https://github.com/Rapptz/RoboDanny/commit/{commit.hex}) {short} ({offset})'

    def get_last_commits(self, count=3):
        repo = pygit2.Repository('.git')
        commits = list(itertools.islice(repo.walk(repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL), count))
        return '\n'.join(self.format_commit(c) for c in commits)

    @hybrid_command(name=_('about'), description=_('Tells you information about the bot itself.'))
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def about(self, ctx: ContextU):
        """Tells you information about the bot itself."""
        await ctx.defer()

        __ = await get_translation_callable(ctx.interaction)

        if not hasattr(self, 'uptime'):
            return await ctx.reply(embed=makeembed_failedaction(description='Bot has not connected to the gateway yet.'),ephemeral=True, delete_after=10)

        revision = self.get_last_commits()
        #embed = makeembed_bot(description='Latest Changes:' + revision, footer_icon_url=self.bot.user.display_avatar.url)
        #embed.title = 'Official Bot Server Invite'
        #embed.url = f'{SUPPORT_SERVER}'
        #embed.colour = discord.Colour.blurple()\

        if getattr(self.bot, 'team', None):
            owner = discord.utils.find(lambda m: m.name == 'aidenpearce3066', self.bot.team.members)
            if not owner:
                owner = self.bot.owner
        else:
            owner = self.bot.owner

        embed = makeembed_bot(
            title="Official Bot Server Invite", 
            description=('Latest Changes:\n') + revision, 
            url=str(SUPPORT_SERVER), 
            color=discord.Colour.blurple(),
            author=str(owner),
            author_icon_url=owner.display_avatar.url,
            timestamp=discord.utils.utcnow()
        )

        # statistics
        total_members = 0
        total_unique = len(self.bot.users)

        text = 0
        voice = 0
        guilds = 0
        for guild in self.bot.guilds:
            guilds += 1
            if guild.unavailable:
                continue

            total_members += guild.member_count or 0
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel):
                    text += 1
                elif isinstance(channel, discord.VoiceChannel):
                    voice += 1

        embed.add_field(name='Members', value="`{}` total\n`{}` unique".format(intcomma(total_members), intcomma(total_unique)))
        embed.add_field(name='Channels', value="`{}` total\n`{}` text\n`{}` voice".format(intcomma(text + voice), intcomma(text), intcomma(voice)))

        memory_usage = self.process.memory_full_info().uss / 1024**2
        cpu_usage = self.process.cpu_percent() / psutil.cpu_count()
        embed.add_field(name='Process', value=f'`{memory_usage:.2f}` MiB\n`{cpu_usage:.2f}`% CPU')
 
        version = pkg_resources.get_distribution('discord.py').version
        embed.add_field(name='Guilds', value=f"`{intcomma(guilds)}`")
        embed.add_field(name='Commands Run', value=f"`{intcomma(sum(self.bot.command_stats.values()))}`")
        embed.add_field(name='Uptime', value=self.get_bot_uptime(brief=True))

        #embed.add_field(name=_('Support Server'), value="[Click]({SUPPORT_SERVER})"
        #embed.add_field(name=_("Trello Board"), value=dchyperlink("https://trello.com/b/RnEMKuA6/rainbow-six-stats", _("Click here!")))

        embed.set_footer(text="Made with discord.py v{}".format(version), icon_url='http://i.imgur.com/5BFecvA.png')

        view = discord.ui.View()

        view.add_item(
            discord.ui.Button(
                label="Install (Server)",
                style=discord.ButtonStyle.link,
                url=oauth_url(self.bot.user.id, permissions=discord.Permissions(415068712000), scopes=['bot', 'applications.commands']),
            )
        )

        view.add_item(
            discord.ui.Button(
                label="Install (User)",
                style=discord.ButtonStyle.link,
                url=oauth_url(self.bot.user.id, scopes=['applications.commands',], integration_type=1),
            )
        )

        view.add_item(
            discord.ui.Button(
                label="Support Server",
                style=discord.ButtonStyle.link,
                url=str(SUPPORT_SERVER),
            )
        )

        await ctx.reply(embed=embed, view=view)

    async def censor_object(self, obj: str | discord.abc.Snowflake) -> str:
        if not isinstance(obj, str) and await Blacklist.is_blacklisted(obj.id):
            return '[censored]'
        
        if isinstance(obj, discord.abc.Snowflake):
            if obj.id in GUILDS:
                return obj
        return censor_invite(obj)

    async def show_guild_stats(self, ctx: ContextU) -> None:
        lookup = (
            '\N{FIRST PLACE MEDAL}',
            '\N{SECOND PLACE MEDAL}',
            '\N{THIRD PLACE MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
        )

        __ = await get_translation_callable(ctx.interaction)

        embed = makeembed_bot(title=await __('Server Command Stats'), color=discord.Colour.blurple(), footer_icon_url=self.bot.user.display_avatar.url)

        # total command uses
        # query = "SELECT COUNT(*), MIN(used) FROM _("Commands") WHERE guild_id=$1;"
        # count: tuple[int, datetime.datetime] = await ctx.db.fetchrow(query, ctx.guild.id)  # type: ignore

        qs = Commands.filter(guild_id=ctx.guild.id).order_by('used')

        count = await qs.count(), getattr((await qs.first()), 'used', None)

        embed.description = _("`{}` commands used.").format(intcomma(count[0]))
        if count[1]:
            timestamp = count[1].replace(tzinfo=datetime.timezone.utc)
        else:
            timestamp = discord.utils.utcnow()
        

        embed.set_footer(text=await __('Tracking command usage since'), icon_url=self.bot.user.display_avatar.url).timestamp = timestamp

        query = """SELECT command,
                          COUNT(*) as "uses"
                   FROM "Commands"
                   WHERE guild_id=$1
                   GROUP BY command
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [ctx.guild.id])
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('command'), row.get('uses')))

        # records = await ctx.db.fetch(query, ctx.guild.id)

        
        #records = await Commands.filter(guild_id=ctx.guild.id).group_by('command').order_by('-used')

        
        # records_dict: Dict[Commands, int] = {
        #     record: await Commands.filter(guild_id=ctx.guild.id, command=record.command).count() for record in records
        # }

        command_mentions = [f"{await self.get_command_mention(command)}" for command, _ in results]
        value = (
            '\n'.join(f'{lookup[index]}: {command_mentions[index]} (`{intcomma(uses)}` uses)' for (index, (command, uses)) in enumerate(results))
            or _('No Commands')
        )

        embed.add_field(name=await __("Top Commands"), value=value, inline=True)

        query = """SELECT command,
                          COUNT(*) as "uses"
                   FROM "Commands"
                   WHERE guild_id=$1
                   AND used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY command
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [ctx.guild.id])
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('command'), row.get('uses')))

        # records = await ctx.db.fetch(query, ctx.guild.id)
        
        #records = await Commands.filter(guild_id=ctx.guild.id, used__gt=(discord.utils.utcnow() - datetime.timedelta(days=1))).group_by('command').order_by('-used')

        command_mentions = [f"{await self.get_command_mention(command)}" for command, _ in results]
        value = (
            '\n'.join(_("{}: {} (`{}` use{})").format(lookup[index], command_mentions[index], intcomma(uses), plural(uses)) for (index, (command, uses)) in enumerate(results))
            or _('No Commands.')
        )
        embed.add_field(name=await __('Top Commands Today'), value=value, inline=True)
        embed.add_field(name='\u200b', value='\u200b', inline=True)

        query = """SELECT author_id,
                          COUNT(*) AS "uses"
                   FROM "Commands"
                   WHERE guild_id=$1
                   GROUP BY author_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [ctx.guild.id])
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('author_id'), row.get('uses')))

        # records = await ctx.db.fetch(query, ctx.guild.id)

        # records = await Commands.filter(guild_id=ctx.guild.id).group_by('author').order_by('-used')
        # records_dict = {}
        # for record in records:
        #     if record.author_id not in records_dict.keys():
        #         records_dict[record.author_id] = 1
        #     else:
        #         records_dict[record.author_id] += 1
        #     if len(records_dict) >= 5:
        #         break
        

        value = (
            '\n'.join(
                f'{lookup[index]}: <@{author_id}> (`{intcomma(uses)}` bot use{plural(uses)})' for (index, (author_id, uses)) in enumerate(results)
            )
            or await __('No bot users.'),
        )

        embed.add_field(name=await __('Top Command Users'), value=value, inline=True)

        query = """SELECT author_id,
                          COUNT(*) AS "uses"
                   FROM "Commands"
                   WHERE guild_id=$1
                   AND used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY author_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        # records = await ctx.db.fetch(query, ctx.guild.id)

        #records = await Commands.filter(guild_id=ctx.guild.id, used__gt=(discord.utils.utcnow() - datetime.timedelta(days=1))).group_by('author').order_by('-used')

        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [ctx.guild.id])
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('author_id'), row.get('uses')))

        value = (
            '\n'.join(
                _("{}: <@{}> (`{}` bot use{})").format(lookup[index], author_id, intcomma(uses), plural(uses)) for (index, (author_id, uses)) in enumerate(results)
            )
            or _('No command users.')
        )

        embed.add_field(name=await __('Top Command Users Today'), value=value, inline=True)
        await ctx.reply(embed=embed)

    async def show_member_stats(self, ctx: ContextU, member: discord.Member) -> None:
        __ = await get_translation_callable(ctx.interaction)
        
        lookup = (
            '\N{FIRST PLACE MEDAL}',
            '\N{SECOND PLACE MEDAL}',
            '\N{THIRD PLACE MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
        )

        embed = makeembed_bot(title=_("Command Stats"), color=member.colour, footer_icon_url=self.bot.user.display_avatar.url)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)

        # total command uses
        query = "SELECT COUNT(*), MIN(used) FROM \"Commands\" WHERE guild_id=$1 AND author_id=$2;"
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [ctx.guild.id, member.id])
        await conn.close()

        count = query[1][0].get('count'), query[1][0].get('min')
        # count: tuple[int, datetime.datetime] = await ctx.db.fetchrow(query, ctx.guild.id, member.id)  # type: ignore


        # qs = Commands.filter(guild_id=ctx.guild.id, author_id=member.id).order_by('used')
        # count = await qs.count(), getattr((await qs.first()), 'used', None)

        embed.description = await __("`{}` commands used.").format(intcomma(count[0]))
        if count[1]:
            timestamp = count[1].replace(tzinfo=datetime.timezone.utc)
        else:
            timestamp = discord.utils.utcnow()

        embed.set_footer(text=await __('First command used')).timestamp = timestamp

        query = """SELECT command,
                          COUNT(*) as "uses"
                   FROM "Commands"
                   WHERE guild_id=$1 AND author_id=$2
                   GROUP BY command
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        # records = await ctx.db.fetch(query, ctx.guild.id, member.id)

        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [ctx.guild.id, member.id])
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('command'), row.get('uses')))

        value = (
            '\n'.join(_("{}: {} (`{}` uses)").format(lookup[index], record, intcomma(uses)) for (index, (record, uses)) in enumerate(results))
            or _('No Commands')
        )

        embed.add_field(name=await __('Most Used Commands'), value=value, inline=False)

        query = """SELECT command,
                          COUNT(*) as "uses"
                   FROM "Commands"
                   WHERE guild_id=$1
                   AND author_id=$2
                   AND used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY command
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
            
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [ctx.guild.id, member.id])
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('command'), row.get('uses')))

        # records = await ctx.db.fetch(query, ctx.guild.id, member.id)

        #records = await Commands.filter(guild_id=ctx.guild.id, author_id=member.id, used__gt=(discord.utils.utcnow() - datetime.timedelta(days=1))).group_by('command').order_by('-used')

        command_mentions = [f"{await self.get_command_mention(command)}" for command, _ in results]
        value = (
            '\n'.join(_("{}: {} (`{}` uses)").format(lookup[index], command_mentions[index], intcomma(uses)) for (index, (command, uses)) in enumerate(results))
            or _('No Commands')
        )

        embed.add_field(name=await __('Most Used Commands Today'), value=value, inline=False)
        await ctx.reply(embed=embed)

    @group(invoke_without_command=True)
    @commands.guild_only()
    #@commands.cooldown(1, 30.0, type=commands.BucketType.member)
    @Cooldown(1, 30, commands.BucketType.member)
    async def stats(self, ctx: ContextU, *, member: Optional[discord.Member] = None):
        """Tells you command usage stats for the server or a member."""
        await ctx.defer()
        if member is None:
            await self.show_guild_stats(ctx)
        else:
            await self.show_member_stats(ctx, member)

    @stats.command(name='global')
    @commands.is_owner()
    async def stats_global(self, ctx: ContextU):
        """Global all time command statistics."""
        await ctx.defer()

        __ = await get_translation_callable(ctx.interaction)
        # query = "SELECT COUNT(*) FROM "Commands";"
        # total: tuple[int] = await ctx.db.fetchrow(query)  # type: ignore
        total = await Commands.all().count()

        e = makeembed_bot(title=await __("Command Stats"), color=discord.Colour.blurple(), footer_icon_url=self.bot.user.display_avatar.url)
        e.description = _("`{}` commands used.").format(intcomma(total))

        lookup = (
            '\N{FIRST PLACE MEDAL}',
            '\N{SECOND PLACE MEDAL}',
            '\N{THIRD PLACE MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
        )

        query = """SELECT command, COUNT(*) AS "uses"
                   FROM "Commands"
                   GROUP BY command
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query)
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('command'), row.get('uses')))

        # records = await ctx.db.fetch(query)
        # records = await Commands.all().group_by('command').order_by('-used')
        # records_dict = {}
        # for record in records:
        #     if record.command not in records_dict.keys():
        #         records_dict[record.command] = 1
        #     else:
        #         records_dict[record.command] += 1
        #     if len(records_dict) >= 5:
        #         break

        command_mentions = [f"{await self.get_command_mention(command)}" for command, _ in results]
        value = '\n'.join(await __("{}: {} (`{}` uses)").format(lookup[index], command_mentions[index], intcomma(uses)) for (index, (command, uses)) in enumerate(results))
        e.add_field(name=await __("Top Commands"), value=value, inline=False)

        query = """SELECT guild_id, COUNT(*) AS "uses"
                   FROM "Commands"
                   GROUP BY guild_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query)
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('guild_id'), row.get('uses')))
        
        query = """SELECT is_user_install, COUNT(*) AS "uses"
                   FROM "Commands"
                   WHERE guild_id IS NULL
                   GROUP BY is_user_install
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query)
        await conn.close()

        installation_results = []
        for row in query[1]:
            installation_results.append((row.get('is_user_install'), row.get('uses')))

        installation_results = {
            True: installation_results[1][0],
            False: installation_results[0][1],
        }

        results.append((0, installation_results.get(True))) # guildid 0 is for user installs

        r = discord.utils.find(lambda x: x[0] is None, results)
        if r:
            results.remove(r)
            results.append((None, r[1] - installation_results.get(True)))
        
        results.sort(key=lambda x: x[1], reverse=True)

        results = results[:5]

        # records = await ctx.db.fetch(query)

        #records = await Commands.all().group_by('guild').order_by('-used')

        # records_dict = {}
        # for record in records:
        #     if record.guild_id not in records_dict.keys():
        #         records_dict[record.guild_id] = 1
        #     else:
        #         records_dict[record.guild_id] += 1
        #     if len(records_dict) >= 5:
        #         break

        value = []
        for (index, (guild_id, uses)) in enumerate(results):
            if guild_id is None:
                guild = await __("Private Messages/DMs/Group DMs")
            elif guild_id == 0:
                guild = await __("User-installed Application (Unknown Server)")
            else:
                try:
                    g = await self.bot.getorfetch_guild(guild_id)
                    guild = f"`{await self.censor_object(g)}`"
                except discord.NotFound:
                    guild = f"<Unknown {guild_id}>"

            emoji = lookup[index]
            value.append(await __("{}: {} (`{}` uses)").format(emoji, guild, intcomma(uses)))

        e.add_field(name=await __("Top Guilds"), value='\n'.join(value), inline=False)

        query = """SELECT author_id, COUNT(*) AS "uses"
                   FROM "Commands"
                   GROUP BY author_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        # records = await ctx.db.fetch(query)
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query)
        await conn.close()

        results = []
        for row in query[1]:
            guild_id = (await Commands.filter(author_id=row.get('author_id'), guild_id__isnull=False).order_by('-created_at').first()).guild_id
            results.append((row.get('author_id'), row.get('uses'), guild_id))

        # records = await Commands.all().group_by('author').order_by('-used')
        
        # records_dict = {}
        # for record in records:
        #     if record.author_id not in records_dict.keys():
        #         records_dict[record.author_id] = 1
        #     else:
        #         records_dict[record.author_id] += 1
        #     if len(records_dict) >= 5:
        #         break
        
        value = []
        for (index, (author_id, uses, guild_id)) in enumerate(results):
            if guild_id:
                try:
                    g = await self.bot.getorfetch_guild(guild_id)
                except discord.NotFound:
                    g = None
            else:
                g = None
            
            try:
                u = await self.bot.getorfetch_user(author_id, g)
                user = f"@{u} ({u.mention})"
            except discord.NotFound:
                user = f'<Unknown {author_id}>'
            emoji = lookup[index]
            value.append(f'{emoji}: {user} (`{intcomma(uses)}` uses)')

        e.add_field(name=_("Top Users"), value='\n'.join(value), inline=False)
        await ctx.reply(embed=e)

    @stats.command(name='today')
    @commands.is_owner()
    async def stats_today(self, ctx: ContextU):
        """Global command statistics for the day."""

        # query = "SELECT failed, COUNT(*) FROM "Commands" WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day') GROUP BY failed;"
        # total = await ctx.db.fetch(query)
        __  = await get_translation_callable(ctx.interaction)

        records = await Commands.filter(used__gt=(discord.utils.utcnow() - datetime.timedelta(days=1))).group_by('failed').annotate(count=Count('failed')).values('failed', 'count')
        total = [(record.get('failed'), int(record.get('count'))) for record in records]
        failed = 0
        success = 0
        question = 0
        for state, count in total:
            if state is False:
                success += count
            elif state is True:
                failed += count
            else:
                question += count

        e = makeembed_bot(title=_('Last 24 Hour Command Stats'), color=discord.Colour.blurple(), footer_icon_url=self.bot.user.display_avatar.url)
        e.description = (
            await __(f'{failed + success + question} commands used today. '
            f'(`{intcomma(success)}` succeeded, `{intcomma(failed)}` failed, `{intcomma(question)}` unknown)')
        )

        lookup = (
            '\N{FIRST PLACE MEDAL}',
            '\N{SECOND PLACE MEDAL}',
            '\N{THIRD PLACE MEDAL}',
            '\N{SPORTS MEDAL}',
            '\N{SPORTS MEDAL}',
        )

        query = """SELECT command, COUNT(*) AS "uses"
                   FROM "Commands"
                   WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY command
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query)
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('command'), row.get('uses')))
        
        # records = await ctx.db.fetch(query)
        #records = await Commands.filter(used__gt=(discord.utils.utcnow() - datetime.timedelta(days=1))).group_by('command').order_by('-used')

        # records_dict: Dict[str, int] = {}
        # for record in records:
        #     if record.command not in records_dict.keys():
        #         records_dict[record.command] = 1
        #     else:
        #         records_dict[record.command] += 1
        #     if len(records_dict) >= 5:
        #         break

        command_mentions = [f"{await self.get_command_mention(command)}" for command, _ in results]
        value = '\n'.join(f'{lookup[index]}: {command_mentions[index]} (`{intcomma(uses)}` uses)' for (index, (command, uses)) in enumerate(results))
        e.add_field(name=await __("Top Commands"), value=value, inline=False)

        query = """SELECT guild_id, COUNT(*) AS "uses"
                   FROM "Commands"
                   WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY guild_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query)
        await conn.close()

        results = []
        for row in query[1]:
            results.append((row.get('guild_id'), row.get('uses')))

        # records = await ctx.db.fetch(query)

        # records_dict = {}
        # for record in records:
        #     if record.guild_id not in records_dict.keys():
        #         records_dict[record.guild_id] = 1
        #     else:
        #         records_dict[record.guild_id] += 1
        
        query = """SELECT is_user_install, COUNT(*) AS "uses"
                   FROM "Commands"
                   WHERE guild_id IS NULL
                   GROUP BY is_user_install
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query)
        await conn.close()

        installation_results = []
        for row in query[1]:
            installation_results.append((row.get('is_user_install'), row.get('uses')))

        installation_results = {
            True: installation_results[1][0],
            False: installation_results[0][1],
        }

        results.append((0, installation_results.get(True))) # guildid 0 is for user installs

        r = discord.utils.find(lambda x: x[0] is None, results)
        if r:
            results.remove(r)
            results.append((None, r[1] - installation_results.get(True)))
        
        results.sort(key=lambda x: x[1], reverse=True)

        results = results[:5]

        # records = await ctx.db.fetch(query)

        #records = await Commands.all().group_by('guild').order_by('-used')

        # records_dict = {}
        # for record in records:
        #     if record.guild_id not in records_dict.keys():
        #         records_dict[record.guild_id] = 1
        #     else:
        #         records_dict[record.guild_id] += 1
        #     if len(records_dict) >= 5:
        #         break

        value = []
        for (index, (guild_id, uses)) in enumerate(results):
            if guild_id is None:
                guild = await __("Private Messages/DMs/Group DMs")
            elif guild_id == 0:
                guild = await __("User-installed Application (Unknown Server)")
            else:
                try:
                    g = await self.bot.getorfetch_guild(guild_id)
                    guild = f"`{await self.censor_object(g)}`"
                except discord.NotFound:
                    guild = f"<Unknown {guild_id}>"

            emoji = lookup[index]
            value.append(await __("{}: {} (`{}` uses)").format(emoji, guild, intcomma(uses)))

        e.add_field(name=await __("Top Guilds"), value='\n'.join(value), inline=False)

        query = """SELECT author_id, COUNT(*) AS "uses"
                   FROM "Commands"
                   WHERE used > (CURRENT_TIMESTAMP - INTERVAL '1 day')
                   GROUP BY author_id
                   ORDER BY "uses" DESC
                   LIMIT 5;
                """

        # records = await ctx.db.fetch(query)

        #records = await Commands.filter(used__gt=(discord.utils.utcnow() - datetime.timedelta(days=1))).group_by('author').order_by('-used')
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query)
        await conn.close()

        # records_dict = {}
        # for record in records:
        #     if record.author_id not in records_dict.keys():
        #         records_dict[record.author_id] = 1
        #     else:
        #         records_dict[record.author_id] += 1
        #     if len(records_dict) >= 5:
        #         break

        results = []
        for row in query[1]:
            guild_id = (await Commands.filter(author_id=row.get('author_id'), guild_id__isnull=False).order_by('-created_at').first()).guild_id
            results.append((row.get('author_id'), row.get('uses'), guild_id))

        value = []
        for (index, (author_id, uses, guild_id)) in enumerate(results):
            try:
                g = await self.bot.getorfetch_guild(guild_id)
            except discord.NotFound:
                g = None

            try:
                u = await self.bot.getorfetch_user(author_id, g)
                user = f"@{u} ({u.mention})"
            except discord.NotFound:
                user = f'<Unknown {author_id}>'
            emoji = lookup[index]
            value.append(f'{emoji}: {user} (`{intcomma(uses)}` uses)')

        e.add_field(name=await __("Top Users"), value='\n'.join(value), inline=False)
        await ctx.reply(embed=e)

    async def send_guild_stats(self, e: discord.Embed, guild: discord.Guild):
        e.add_field(name='Name', value=guild.name)
        e.add_field(name='ID', value=guild.id)
        e.add_field(name='Shard ID', value=guild.shard_id or 'N/A')
        if not guild.owner and guild.owner_id:
            try:
                owner = await self.bot.getorfetch_user(guild.owner_id, None)
            except discord.NotFound:
                owner = None
        else:
            owner = guild.owner
        
        e.add_field(name='Owner', value=f'{owner} (ID: `{guild.owner_id}`)')

        bots = sum(m.bot for m in guild.members)
        total = guild.member_count or 1
        e.add_field(name='Members', value=str(intcomma(total)))
        e.add_field(name='Bots', value=f'{intcomma(bots)} ({bots/total:.2%})')

        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)

        if guild.me:
            e.timestamp = guild.me.joined_at
        
        e.set_footer(text=f'Server count is now {intcomma(len(self.bot.guilds))}.',icon_url=self.bot.user.display_avatar.url)
        
        await self.webhook.send(embed=e)
    
    async def send_user_stats(self, e: discord.Embed, user: discord.abc.User):
        e.add_field(name='Name', value=user.name)
        e.add_field(name='ID', value=user.id)
        #e.add_field(name='Shard ID', value=guild.shard_id or 'N/A')


        if not isinstance(user, discord.User):
            try:
                user_obj = await self.bot.getorfetch_user(user.id, None)
            except discord.NotFound:
                user_obj = user
        else:
            user_obj = user
                  
        #e.add_field(name='Owner', value=f'{owner} (ID: `{guild.owner_id}`)')
        e.add_field(name='Global Nickname', value=user_obj.display_name)
        
        e.add_field(name='Account Created', value=dctimestamp(user_obj.created_at, "R"))

        set_flags = {flag for flag, value in user.public_flags if value}
        subset_flags = set_flags & badges_to_emoji.keys()
        badges: List[str] = [badges_to_emoji[flag] for flag in subset_flags] # type: ignore

        # if ctx.guild is not None and ctx.guild.owner_id == user.id:
        #     badges.append('<:owner:585789630800986114>')  # Discord Bots

        # if ctx.guild is not None and isinstance(user, discord.Member) and user.premium_since is not None:
        #     e.add_field(name='Boosted', value=format_date(user.premium_since), inline=False)
        #     badges.append('<:booster:1087022965775925288>')  # R. Danny

        if badges:
            e.add_field(name="Badges", value=' '.join(badges))

        remaining_flags = (set_flags - subset_flags) & misc_flag_descriptions.keys()
        if remaining_flags:
            e.add_field(
                name='Public Flags',
                value='\n'.join(misc_flag_descriptions[flag] for flag in remaining_flags),
                inline=False,
            )

        #bots = sum(m.bot for m in guild.members)
        #total = guild.member_count or 1
        # e.add_field(name='Members', value=str(intcomma(total)))
        # e.add_field(name='Bots', value=f'{intcomma(bots)} ({bots/total:.2%})')

        if user.display_avatar:
            e.set_thumbnail(url=user_obj.display_avatar.url)

        # if guild.me:
        #     e.timestamp = guild.me.joined_at

        e.timestamp = discord.utils.utcnow()
        
        e.set_footer(
            text=f'User count is now {intcomma((self.bot.bot_app_info.approximate_user_install_count or 0)+1)}.',
            icon_url=self.bot.user.display_avatar.url,
        )
        
        await self.webhook.send(embed=e)

    @stats_today.before_invoke
    @stats_global.before_invoke
    async def before_stats_invoke(self, ctx: ContextU):
        await ctx.typing()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        e = makeembed_bot(color=0x53DDA4, title='New Guild', footer_icon_url=self.bot.user.display_avatar.url)  # green colour
        await self.send_guild_stats(e, guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        e = makeembed_bot(color=0xDD5F53, title='Left Guild', footer_icon_url=self.bot.user.display_avatar.url)  # red colour
        await self.send_guild_stats(e, guild)
    
    async def on_user_add(self, user: discord.User):
        e = makeembed_bot(color=0x53DDA4, title='New User', footer_icon_url=self.bot.user.display_avatar.url) # green colour
        await self.send_user_stats(e, user)

    async def on_user_remove(self, user: discord.User):
        e = makeembed_bot(color=0xDD5F53, title='Left User', footer_icon_url=self.bot.user.display_avatar.url)
        await self.send_user_stats(e, user)

    async def on_user_authorization(self, user: discord.User):
        e = makeembed_bot(color=discord.Color.yellow(), title='Linked User', footer_icon_url=self.bot.user.display_avatar.url)
        await self.send_user_stats(e, user)
    
    async def on_user_deauthorization(self, user: discord.User):
        e = makeembed_bot(color=discord.Color.red(), title='Unlinked User', footer_icon_url=self.bot.user.display_avatar.url)
        await self.send_user_stats(e, user)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: ContextU, error: Exception) -> None:
        await self.register_command(ctx)

        #await self.do_bulk_insert()

        if not isinstance(error, (commands.CommandInvokeError, commands.ConversionError)):
            return

        error = error.original
        if isinstance(error, (discord.Forbidden, discord.NotFound, )):#menus.MenuError)):
            return

        e = makeembed(title='Command Error', color=0xCC3366)
        e.add_field(name='Name', value=ctx.command.qualified_name)
        e.add_field(name='Author', value=f'{ctx.author} (ID: {ctx.author.id})')

        fmt = f'Channel: {ctx.channel} (ID: {ctx.channel.id})'
        if ctx.guild:
            fmt = f'{fmt}\nGuild: {ctx.guild} (ID: {ctx.guild.id})'

        e.add_field(name='Location', value=fmt, inline=False)
        e.add_field(name='Content', value=textwrap.shorten(ctx.message.content, width=512))

        exc = ''.join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
        e.description = f'```py\n{exc}\n```'
        e.timestamp = discord.utils.utcnow()
        await self.webhook.send(embed=e)

    def add_record(self, record: logging.LogRecord) -> None:
        # if self.bot.config.debug:
        #     return
        self._logging_queue.put_nowait(record)

    async def send_log_record(self, record: logging.LogRecord) -> None:
        attributes = {'INFO': '\N{INFORMATION SOURCE}\ufe0f', 'WARNING': '\N{WARNING SIGN}\ufe0f'}

        emoji = attributes.get(record.levelname, '\N{CROSS MARK}')
        #dt = datetime.datetime.utcfromtimestamp(record.created)
        dt = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc)
        msg = textwrap.shorten(f'{emoji} {dctimestamp(dt)} {record.message}', width=1990)
        if record.name == 'discord.gateway':
            username = 'Gateway'
            avatar_url = 'https://i.imgur.com/4PnCKB3.png'
        else:
            username = f'{record.name} Logger'
            avatar_url = discord.utils.MISSING

        await self.webhook.send(msg, username=username, avatar_url=avatar_url)

    @command(hidden=True)
    @commands.is_owner()
    async def bothealth(self, ctx: ContextU):
        """Various bot health monitoring tools."""

        # This uses a lot of private methods because there is no
        # clean way of doing this otherwise.
        HEALTHY = discord.Colour(value=0x43B581)
        UNHEALTHY = discord.Colour(value=0xF04947)
        WARNING = discord.Colour(value=0xF09E47)
        total_warnings = 0

        embed = makeembed_bot(title='Bot Health Report', color=HEALTHY, footer_icon_url=self.bot.user.display_avatar.url)

        # Check the connection pool health.
        # pool = self.bot.pool
        # total_waiting = len(pool._queue._getters)  # type: ignore
        # current_generation = pool._generation

        # description = [
        #     f'Total `Pool.acquire` Waiters: {total_waiting}',
        #     f'Current Pool Generation: {current_generation}',
        #     f'Connections In Use: {len(pool._holders) - pool._queue.qsize()}',  # type: ignore
        # ]

        
        description = [

        ]

        # questionable_connections = 0
        # connection_value = []
        # for index, holder in enumerate(pool._holders, start=1):
        #     generation = holder._generation
        #     in_use = holder._in_use is not None
        #     is_closed = holder._con is None or holder._con.is_closed()
        #     display = f'gen={holder._generation} in_use={in_use} closed={is_closed}'
        #     questionable_connections += any((in_use, generation != current_generation))
        #     connection_value.append(f'<Holder i={index} {display}>')

        #joined_value = '\n'.join(connection_value)
        #embed.add_field(name='Connections', value=f'```py\n{joined_value}\n```', inline=False)

        spam_control = self.bot.spam_control
        being_spammed = [str(key) for key, value in spam_control._cache.items() if value._tokens == 0]

        description.append(f'''Current Spammers: {", ".join(['`'+str(x)+'`' for x in being_spammed]) if being_spammed else "`None`"}''')
        #description.append(f'Questionable Connections: {questionable_connections}')

        #total_warnings += questionable_connections
        if being_spammed:
            embed.colour = WARNING
            total_warnings += 1

        all_tasks = asyncio.all_tasks(loop=self.bot.loop)
        event_tasks = [t for t in all_tasks if 'Client._run_event' in repr(t) and not t.done()]

        cogs_directory = os.path.dirname(__file__)
        tasks_directory = os.path.join('discord', 'ext', 'tasks', '__init__.py')
        inner_tasks = [t for t in all_tasks if cogs_directory in repr(t) or tasks_directory in repr(t)]

        bad_inner_tasks = ", ".join(f'`{hex(id(t))}`' for t in inner_tasks if t.done() and t._exception is not None)
        total_warnings += bool(bad_inner_tasks)
        embed.add_field(name='Inner Tasks', value=f'Total: `{len(inner_tasks)}`\nFailed: `{bad_inner_tasks or "None"}`')
        embed.add_field(name='Events Waiting', value=f'Total: `{len(event_tasks)}`', inline=False)

        command_waiters = len(self._data_batch)
        is_locked = self._batch_lock.locked()
        description.append(f'Commands Waiting: `{command_waiters}`, Batch Locked: {emojidict.get(is_locked)}')

        memory_usage = self.process.memory_full_info().uss / 1024**2
        cpu_usage = self.process.cpu_percent() / psutil.cpu_count()
        embed.add_field(name='Process', value=f'`{memory_usage:.2f}` MiB\n`{cpu_usage:.2f}`% CPU', inline=False)

        global_rate_limit = not self.bot.http._global_over.is_set()
        description.append(f'Global Rate Limit: {emojidict.get(global_rate_limit)}')

        if command_waiters >= 8:
            total_warnings += 1
            embed.colour = WARNING

        if global_rate_limit or total_warnings >= 9:
            embed.colour = UNHEALTHY

        embed.set_footer(text=f'{total_warnings} warning(s)')
        embed.description = '\n'.join(description)
        await ctx.reply(embed=embed)

    @command(hidden=True)
    @commands.is_owner()
    async def gateway(self, ctx: ContextU):
        """Gateway related stats."""

        yesterday = discord.utils.utcnow() - datetime.timedelta(days=1)

        # fmt: off
        identifies = {
            shard_id: sum(1 for dt in dates if dt > yesterday)
            for shard_id, dates in self.bot.identifies.items()
        }
        resumes = {
            shard_id: sum(1 for dt in dates if dt > yesterday)
            for shard_id, dates in self.bot.resumes.items()
        }
        # fmt: on

        total_identifies = sum(identifies.values())

        builder = [
            f'Total RESUMEs: {sum(resumes.values())}',
            f'Total IDENTIFYs: {total_identifies}',
        ]

        issues = 0
        if isinstance(self.bot, commands.AutoShardedBot):
            shard_count = len(self.bot.shards)
            if total_identifies > (shard_count * 10):
                issues = 2 + (total_identifies // 10) - shard_count
            else:
                issues = 0

            for shard_id, shard in self.bot.shards.items():
                badge = None
                # Shard WS closed
                # Shard Task failure
                # Shard Task complete (no failure)
                if shard.is_closed():
                    badge = emojidict.get('offline')
                    issues += 1
                elif shard._parent._task and shard._parent._task.done():
                    exc = shard._parent._task.exception()
                    if exc is not None:
                        badge = '\N{FIRE}'
                        issues += 1
                    else:
                        badge = '\U0001f504'

                if badge is None:
                    badge = emojidict.get('online')

                stats = []
                identify = identifies.get(shard_id, 0)
                resume = resumes.get(shard_id, 0)
                if resume != 0:
                    stats.append(f'R: {resume}')
                if identify != 0:
                    stats.append(f'ID: {identify}')

                if stats:
                    builder.append(f'Shard ID {shard_id}: {badge} ({", ".join(stats)})')
                else:
                    builder.append(f'Shard ID {shard_id}: {badge}')

        if issues == 0:
            colour = 0x43B581
        #elif issues < len(self.bot.shards) // 4:
        #    colour = 0xF09E47
        else:
            colour = 0xF04947

        embed = makeembed_bot(color=colour, title='Gateway (last 24 hours)', footer_icon_url=self.bot.user.display_avatar.url)
        embed.description = '\n'.join(builder)
        embed.set_footer(text=f'{issues} warnings')
        await ctx.reply(embed=embed)

    @command(hidden=True, aliases=['cancel_task'])
    @commands.is_owner()
    async def debug_task(self, ctx: ContextU, memory_id: Annotated[int, hex_value]):
        """Debug a task by a memory location."""
        task = object_at(memory_id)
        if task is None or not isinstance(task, asyncio.Task):
            return await ctx.reply(f'Could not find Task object at {hex(memory_id)}.')

        if ctx.invoked_with == 'cancel_task':
            task.cancel()
            return await ctx.reply(f'Cancelled task object {task!r}.')

        paginator = commands.Paginator(prefix='```py')
        fp = io.StringIO()
        frames = len(task.get_stack())
        paginator.add_line(f'# Total Frames: {frames}')
        task.print_stack(file=fp)

        for line in fp.getvalue().splitlines():
            paginator.add_line(line)

        for page in paginator.pages:
            await ctx.send(page)

    async def tabulate_query(self, ctx: ContextU, records: Union[list[Commands], Dict[str, Any]], *args: Any):
        #records = await ctx.db.fetch(query, *args)

        if len(records) == 0:
            return await ctx.reply('No results found.')            

        headers = list(records[0].__dict__.keys())
        table = danny_formats.TabularData()
        table.set_columns(headers)

        # def format_datetimes(dt: datetime.datetime) -> str:
        #     return dctimestamp(dt)
        
        table.add_rows(list(r.__dict__.values()) for r in records)
        render = table.render()

        fmt = f'```\n{render}\n```'
        if len(fmt) > 2000:
            fp = io.BytesIO(fmt.encode('utf-8'))
            await ctx.reply('Too many results...', file=discord.File(fp, 'results.txt'))
        else:
            await ctx.reply(fmt)

    @group(hidden=True, invoke_without_command=True)
    @commands.is_owner()
    async def command_history(self, ctx: ContextU):
        """Command history."""
        query = """SELECT
                    id,
                    CASE failed
                        WHEN TRUE THEN command || ' [!]'
                        ELSE command
                    END AS "command",
                    to_char(used, 'Mon DD HH12:MI:SS AM') AS "invoked",
                    author_id,
                    guild_id
                   FROM "Commands"
                   ORDER BY used DESC
                   LIMIT 15;
                """
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [])
        await conn.close()
        
        results = []
        for row in query[1]:
            results.append(await Commands.get(id=row.get('id')))

        #await self.tabulate_query(ctx, await Commands.filter(author_id=user_id).group_by('command').order_by('-used').limit(20))
        await self.tabulate_query(ctx, results)
        # await self.tabulate_query(ctx, query)

    @command_history.command(name='for')
    @commands.is_owner()
    async def command_history_for(self, ctx: ContextU, days: Annotated[int, Optional[int]] = 7, *, command: str):
        """Command history for a command."""

        query = """SELECT *, t.success + t.failed AS "total"
                   FROM (
                       SELECT guild_id,
                              SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                              SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                       FROM "Commands"
                       WHERE command=$1
                       AND used > (CURRENT_TIMESTAMP - $2::interval)
                       GROUP BY guild_id
                   ) AS t
                   ORDER BY "total" DESC
                   LIMIT 30;
                """
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [command, days])
        await conn.close()
        results = []
        for row in query[1]:
            results.append(await Commands.get(id=row.get('id')))

        #await self.tabulate_query(ctx, await Commands.filter(author_id=user_id).group_by('command').order_by('-used').limit(20))
        await self.tabulate_query(ctx, results)

        # await self.tabulate_query(ctx, query, command, datetime.timedelta(days=days))
        await self.tabulate_query(ctx, await Commands.filter(command=command, used__gt=(discord.utils.utcnow() - datetime.timedelta(days=days))).group_by('guild').order_by('-used').limit(30))

    @command_history.command(name='guild', aliases=['server'])
    @commands.is_owner()
    async def command_history_guild(self, ctx: ContextU, guild_id: Optional[int]=None):
        """Command history for a guild."""

        if not guild_id:
            guild_id = ctx.guild.id
        assert guild_id is not None

        query = """SELECT
                        id,
                        CASE failed
                            WHEN TRUE THEN command || ' [!]'
                            ELSE command
                        END AS "command",
                        channel_id,
                        author_id,
                        used
                   FROM "Commands"
                   WHERE guild_id=$1
                   ORDER BY used DESC
                   LIMIT 15;
                """
        # await self.tabulate_query(ctx, query, guild_id)
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [guild_id])
        await conn.close()

        results = []
        for row in query[1]:
            results.append(await Commands.get(id=row.get('id')))

        #await self.tabulate_query(ctx, await Commands.filter(author_id=user_id).group_by('command').order_by('-used').limit(20))
        await self.tabulate_query(ctx, results)

    @command_history.command(name='user', aliases=['member'])
    @commands.is_owner()
    async def command_history_user(self, ctx: ContextU, user_id: int):
        """Command history for a user."""

        query = """SELECT
                        id,
                        CASE failed
                            WHEN TRUE THEN command || ' [!]'
                            ELSE command
                        END AS "command",
                    *
                   FROM "Commands"
                   WHERE author_id=$1
                   ORDER BY used DESC
                   LIMIT 20;
                """
        # await self.tabulate_query(ctx, query, user_id)
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [user_id])
        await conn.close()

        results = []
        for row in query[1]:
            results.append(await Commands.get(id=row.get('id')))

        #await self.tabulate_query(ctx, await Commands.filter(author_id=user_id).group_by('command').order_by('-used').limit(20))
        await self.tabulate_query(ctx, results)

    @command_history.command(name='log')
    @commands.is_owner()
    async def command_history_log(self, ctx: ContextU, days: int = 7):
        """Command history log for the last N days."""

        query = """SELECT command, COUNT(*)
                   FROM "Commands"
                   WHERE used > (CURRENT_TIMESTAMP - $1::interval)
                   GROUP BY command
                   ORDER BY 2 DESC
                """

        all_commands = {c.qualified_name: 0 for c in self.bot.walk_commands()}

        #records = await ctx.db.fetch(query, datetime.timedelta(days=days))
        # records = await Commands.filter(used__gt=(discord.utils.utcnow() - datetime.timedelta(days=days))).group_by('command','used', 'kwargs', 'uses', 'args').order_by('-used')
        # records_dict = {}
        # for record in records:
        #     if record.command not in records_dict.keys():
        #         records_dict[record.command] = 1
        #     else:
        #         records_dict[record.command] += 1
        
        conn = Tortoise.get_connection('default')
        query = await conn.execute_query(query, [datetime.timedelta(days=days)])
        await conn.close()

        results_dict: Dict[str, int] = {}
        for row in query[1]:
            results_dict[str(row.get('command'))] = int(row.get('count'))
        
        for name, uses in results_dict.items():
            if name in all_commands:
                all_commands[name] = uses

        as_data = sorted(all_commands.items(), key=lambda t: t[1], reverse=True)
        table = danny_formats.TabularData()
        table.set_columns(['Command', 'Uses'])
        table.add_rows(tup for tup in as_data)
        render = table.render()

        embed = makeembed_bot(title='Summary', color=discord.Colour.green(), footer_icon_url=self.bot.user.display_avatar.url)
        embed.set_footer(text='Since').timestamp = discord.utils.utcnow() - datetime.timedelta(days=days)
        
        command_mentions_top = [f"{await self.get_command_mention(command)}" for command, _ in [x for x in results_dict.items()][:10]]
        command_mentions_bottom = [f"{await self.get_command_mention(command)}" for command, _ in [x for x in results_dict.items()][-10:]]

        top_ten = '\n'.join(f'{command_mentions_top[index]}: {uses}' for index, (_, uses) in enumerate([x for x in results_dict.items()][:10]))
        bottom_ten = '\n'.join(f'{command_mentions_bottom[index]}: {uses}' for index, (_, uses) in enumerate([x for x in results_dict.items()][-10:]))
        embed.add_field(name='Top 10', value=top_ten)
        embed.add_field(name='Bottom 10', value=bottom_ten)

        unused = ', '.join(name for name, uses in as_data if uses == 0)
        if len(unused) > 1024:
            unused = 'Way too many...'

        embed.add_field(name='Unused', value=unused, inline=False)

        return await ctx.reply(embed=embed, file=discord.File(io.BytesIO(render.encode()), filename='full_results.txt'))

    @command_history.command(name='cog')
    @commands.is_owner()
    async def command_history_cog(self, ctx: ContextU, days: Annotated[int, Optional[int]] = 7, *, cog_name: Optional[str] = None):
        """Command history for a cog or grouped by a cog."""

        interval = datetime.timedelta(days=days)
        if cog_name is not None:
            cog = self.bot.get_cog(cog_name)
            if cog is None:
                return await ctx.reply(f'Unknown cog: {cog_name}')

            query = """SELECT *, t.success + t.failed AS "total"
                       FROM (
                           SELECT command,
                                  SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
                                  SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
                           FROM "Commands"
                           WHERE command = any($1::text[])
                           AND used > (CURRENT_TIMESTAMP - $2::interval)
                           GROUP BY command
                       ) AS t
                       ORDER BY "total" DESC
                       LIMIT 30;
                    """
                
            conn = Tortoise.get_connection('default')
            query = await conn.execute_query(query, [[c.qualified_name for c in cog.walk_commands()], interval])
            await conn.close()

            results: List[Tuple[Commands, int]] = []
            for row in query[1]:
                results.append((await Commands.get(id=row.get('id')), int(row.get('total'))))

            #return await self.tabulate_query(ctx, 
            return await self.tabulate_query(ctx, await Commands.filter(command__in=[c.qualified_name for c in cog.walk_commands()], used__gt=(discord.utils.utcnow() - interval)).group_by('command').order_by('-used').limit(30))

        # A more manual query with a manual grouper.
        # query = """SELECT *, t.success + t.failed AS "total"
        #            FROM (
        #                SELECT command,
        #                       SUM(CASE WHEN failed THEN 0 ELSE 1 END) AS "success",
        #                       SUM(CASE WHEN failed THEN 1 ELSE 0 END) AS "failed"
        #                FROM "Commands"
        #                WHERE used > (CURRENT_TIMESTAMP - $1::interval)
        #                GROUP BY command
        #            ) AS t;
        #         """

        # class Count:
        #     __slots__ = ('success', 'failed', 'total')

        #     def __init__(self):
        #         self.success = 0
        #         self.failed = 0
        #         self.total = 0

        #     def add(self, record):
        #         self.success += record['success']
        #         self.failed += record['failed']
        #         self.total += record['total']

        # data = defaultdict(Count)
        # records = await ctx.db.fetch(query, interval)
        # for record in records:
        #     command = self.bot.get_command(record['command'])
        #     if command is None or command.cog is None:
        #         data['No Cog'].add(record)
        #     else:
        #         data[command.cog.qualified_name].add(record)

        # table = formats.TabularData()
        # table.set_columns(['Cog', 'Success', 'Failed', 'Total'])
        # data = sorted([(cog, e.success, e.failed, e.total) for cog, e in data.items()], key=lambda t: t[-1], reverse=True)

        # table.add_rows(data)
        # render = table.render()
        # await ctx.safe_send(f'```\n{render}\n```')

    @command_history.command(name='export')
    @commands.is_owner()
    async def command_history_export(self, ctx: ContextU, guild_id: Optional[int]=None, user_id: Optional[int]=None, cog_name: Optional[str]=None, days: int = 7):
        """Export command history for a guild, user, or cog."""

        if guild_id is not None:
            query = """SELECT *
                       FROM "Commands"
                       WHERE guild_id=$1
                       AND used > (CURRENT_TIMESTAMP - $2::interval);
                    """
            args = [guild_id, datetime.timedelta(days=days)]
            #records = await ctx.db.fetch(query, guild_id, datetime.timedelta(days=days))
        elif user_id is not None:
            query = """SELECT *
                       FROM "Commands"
                       WHERE author_id=$1
                       AND used > (CURRENT_TIMESTAMP - $2::interval);
                    """
            args = [user_id, datetime.timedelta(days=days)]
            #records = await ctx.db.fetch(query, user_id, datetime.timedelta(days=days))
        elif cog_name is not None:
            cog = self.bot.get_cog(cog_name)
            if cog is None:
                return await ctx.reply(f'Unknown cog: {cog_name}')

            query = """SELECT *
                       FROM "Commands"
                       WHERE command = any($1::text[])
                       AND used > (CURRENT_TIMESTAMP - $2::interval);
                    """
            args = [[c.qualified_name for c in cog.walk_commands()], datetime.timedelta(days=days)]
            #records = await ctx.db.fetch(query, [c.qualified_name for c in cog.walk_commands()], datetime.timedelta(days=days))
        else:
            query = """SELECT *
                       FROM "Commands"
                       WHERE used > (CURRENT_TIMESTAMP - $1::interval);
                    """
            args = [datetime.timedelta(days=days)]
            #records = await ctx.db.fetch(query, datetime.timedelta(days=days))

        conn = Tortoise.get_connection('default')
        result = await conn.execute_query(query, args)
        await conn.close()

        # results = []
        # for row in query[1]:
        #     results.append(await Commands.get(id=row.get('id')))
        
        # write it to a CSV file in memory
        fp = io.StringIO()
        writer = csv.writer(fp)
        writer.writerow(['id', 'created_at', 'updated_at', 'guild_id', 'channel_id', 'author_id', 'used', 'prefix', 'command', 'failed', 'app_command', 'args', 'kwargs', 'command_id', 'transaction_id', 'is_user_install', 'is_guild_install'])
        for row in result[1]:
            writer.writerow([row.get('id'), row.get('created_at'), row.get('updated_at'), row.get('guild_id'), row.get('channel_id'), row.get('author_id'), row.get('used'), row.get('prefix'), row.get('command'), row.get('failed'), row.get('app_command'), row.get('args'), row.get('kwargs'), row.get('command_id'), row.get('transaction_id'), row.get('is_user_install'), row.get('is_guild_install')])

        fp.seek(0)
        await ctx.reply(file=discord.File(fp, filename='command_history.csv'))

    @group(hidden=True, invoke_without_command=False)
    @commands.is_owner()
    async def export(self, ctx: ContextU):
        """Export data from the database."""
        pass

    @export.command(name='guilds',aliases=['guild', 'servers', 'server'])
    @commands.is_owner()
    async def export_guilds(self, ctx: ContextU):
        """Export all guilds."""
        
        #id	created_at	updated_at	guild_id	name	guild_created_at	description	guild_owner_id	features	vanity_url	vanity_url_code	approximate_member_count	member_count	approximate_presence_count	max_members	max_presences	max_video_channel_users	bitrate_limit	filesize_limit	sticker_limit	emoji_limit	afk_timeout	verification_level	explicit_content_filter	default_notifications	premium_tier	premium_subscription_count	preferred_locale	nsfw_level	mfa_level	premium_progress_bar_enabled	widget_enabled	afk_channel_id	system_channel_id	system_channel_flags	rules_channel_id	public_updates_channel_id	safety_alerts_channel_id	widget_channel_id	default_role_id	premium_subscriber_role_id	invited_paused_until	dms_paused_until	icon_url	icon_bytes	banner_url	banner_bytes	splash_url	splash_bytes	discovery_splash_url	discovery_splash_bytes	self_role_id	shard_id	bot_nickname	bot_joined_at	chunked	large	bot_in_guild	owner_id
        fp = io.StringIO()
        writer = csv.writer(fp)
        writer.writerow(['id', 'created_at', 'guild_id', 'name', 'guild_created_at', 'description', 'guild_owner_id', 'features', 'vanity_url', 'vanity_url_code', 'approximate_member_count', 'member_count', 'approximate_presence_count', 'max_members', 'max_presences', 'max_video_channel_users', 'bitrate_limit', 'filesize_limit', 'sticker_limit', 'emoji_limit', 'afk_timeout', 'verification_level', 'explicit_content_filter', 'default_notifications', 'premium_tier', 'premium_subscription_count', 'preferred_locale', 'nsfw_level', 'mfa_level', 'premium_progress_bar_enabled', 'widget_enabled', 'afk_channel_id', 'system_channel_id', 'system_channel_flags', 'rules_channel_id', 'public_updates_channel_id', 'safety_alerts_channel_id', 'widget_channel_id', 'default_role_id', 'premium_subscriber_role_id', 'invited_paused_until', 'dms_paused_until', 'icon_url', 'banner_url', 'splash_url', 'discovery_splash_url', 'self_role_id', 'shard_id', 'bot_nickname', 'bot_joined_at', 'chunked', 'large', 'bot_in_guild', 'owner_id'])
                
        guilds = sorted(self.bot.guilds, key=lambda g: g.me.joined_at if g.me.joined_at else g.created_at, reverse=True) # type: ignore

        for tr, guild in enumerate(guilds):
            # use getattr to get _id attrs
            writer.writerow([tr, guild.created_at, guild.id, guild.name, guild.created_at, guild.description, guild.owner_id, guild.features, guild.vanity_url, guild.vanity_url_code, guild.approximate_member_count, guild.member_count, guild.approximate_presence_count, guild.max_members, guild.max_presences, guild.max_video_channel_users, guild.bitrate_limit, guild.filesize_limit, guild.sticker_limit, guild.emoji_limit, guild.afk_timeout, guild.verification_level, guild.explicit_content_filter, guild.default_notifications, guild.premium_tier, guild.premium_subscription_count, guild.preferred_locale, guild.nsfw_level, guild.mfa_level, guild.premium_progress_bar_enabled, guild.widget_enabled, getattr(guild.afk_channel, 'id', None), getattr(guild.system_channel, 'id', None), guild.system_channel_flags, getattr(guild.rules_channel, 'id', None), getattr(guild.public_updates_channel, 'id', None), getattr(guild.safety_alerts_channel, 'id', None), getattr(guild.widget_channel, 'id', None), getattr(guild.default_role, 'id', None), getattr(guild.premium_subscriber_role, 'id', None), guild.invites_paused_until, guild.dms_paused_until, getattr(guild.icon, 'url', None), getattr(guild.banner, 'url', None), getattr(guild.splash, 'url', None), getattr(guild.discovery_splash, 'url', None), getattr(guild.self_role, 'id', None), guild.shard_id, guild.me.nick, guild.me.joined_at, guild.chunked, guild.large, True, guild.owner_id])
        fp.seek(0)
        await ctx.reply(file=discord.File(fp, filename='exported_guilds.csv'))
    
    @export.command(name='users')
    @commands.is_owner()
    async def export_users(self, ctx: ContextU):
        """Export all users."""
        fp = io.StringIO()
        writer = csv.writer(fp)
        #writer.writerow(['id', 'created_at', 'user_id', 'name', 'discriminator', 'avatar_url', 'bot', 'system', 'public_flags'])
        # id	created_at	updated_at	user_id	name	discriminator	global_name	bot	system	dm_channel_id	accent_color	avatar_url	avatar_bytes	avatar_decoration_url	avatar_decoration_bytes	avatar_decoration_sku_id	banner_url	banner_bytes	color	user_created_at	default_avatar_url	default_avatar_bytes	public_flags
        writer.writerow(['id', 'created_at', 'user_id', 'name', 'discriminator', 'global_name', 'bot', 'system', 'dm_channel_id', 'accent_color', 'avatar_url', 'avatar_decoration_url', 'avatar_decoration_sku_id', 'banner_url', 'color', 'user_created_at', 'default_avatar_url', 'public_flags'])
        
        users = sorted(self.bot.users, key=lambda u: u.created_at, reverse=False)
        for tr, user in enumerate(users):
            #writer.writerow([user.id, user.created_at, user.id, user.name, user.discriminator, getattr(user.avatar, 'url', None), user.bot, user.system, user.public_flags])
            writer.writerow([tr, user.created_at, user.id, user.name, user.discriminator, user.global_name, user.bot, user.system, getattr(getattr(user, 'dm_channel', None), 'id', None), user.accent_color, getattr(user.avatar, 'url', None), getattr(user.avatar_decoration, 'url', None), user.avatar_decoration_sku_id, getattr(user.banner, 'url', None), user.color, user.created_at, getattr(user.default_avatar, 'url', None), user.public_flags])
        fp.seek(0)
        await ctx.reply(file=discord.File(fp, filename='exported_users.csv'))

    @export.command(name='channels')
    @commands.is_owner()
    async def export_channels(self, ctx: ContextU):
        """Export all channels."""

        fp = io.StringIO()
        writer = csv.writer(fp)
        #  id	created_at	updated_at	channel_id	name	jump_url	channel_created_at	category_id	permissions_synced	type	position	topic	last_message_id	slowmode_delay	nsfw	default_auto_archive_duration	default_thread_slowmode_delay	default_reaction_emoji	default_layout	default_sort_order	flags	parent_id	owner_id	message_count	member_count	archived	invitable	archiver_id	auto_archive_duration	archive_timestamp	starter_message_id	bitrate	rtc_region	user_limit	video_quality_mode	icon_url	icon_bytes	guild_id
        writer.writerow(['id', 'created_at', 'channel_id', 'name', 'jump_url', 'channel_created_at', 'category_id', 'permissions_synced', 'type', 'position', 'topic', 'last_message_id', 'slowmode_delay', 'nsfw', 'default_auto_archive_duration', 'default_thread_slowmode_delay', 'default_reaction_emoji', 'default_layout', 'default_sort_order', 'flags', 'parent_id', 'owner_id', 'message_count', 'member_count', 'archived', 'invitable', 'archiver_id', 'auto_archive_duration', 'archive_timestamp', 'starter_message_id', 'bitrate', 'rtc_region', 'user_limit', 'video_quality_mode', 'icon_url', 'guild_id'])
        channels: Dict[discord.Guild, List[discord.abc.GuildChannel]] = {}
        #for channel in self.bot.get_all_channels():
        for guild in self.bot.guilds:
            channels[guild] = []
            for channel in guild.channels:
                channels[channel.guild].append(channel)
                # use getattr for all attributes
                #writer.writerow([getattr(channel, 'id', None), ])

        for tr, (guild, guild_channels) in enumerate(channels.items()):
            for channel in guild_channels:
                writer.writerow([tr, getattr(channel, 'created_at', None), getattr(channel, 'id', None), getattr(channel, 'name', None), getattr(channel, 'jump_url', None), getattr(channel, 'created_at', None), getattr(channel.category, 'id', None), getattr(channel, 'permissions_synced', None), getattr(channel, 'type', None), getattr(channel, 'position', None), getattr(channel, 'topic', None), getattr(getattr(channel, 'last_message', None), 'id', None), getattr(channel, 'slowmode_delay', None), getattr(channel, 'nsfw', None), getattr(channel, 'default_auto_archive_duration', None), getattr(channel, 'default_thread_slowmode_delay', None), getattr(channel, 'default_reaction_emoji', None), getattr(channel, 'default_layout', None), getattr(channel, 'default_sort_order', None), getattr(channel, 'flags', None), getattr(channel.category, 'id', None), getattr(getattr(channel, 'owner', None), 'id', None), getattr(channel, 'message_count', None), getattr(channel, 'member_count', None), getattr(channel, 'archived', None), getattr(channel, 'invitable', None), getattr(getattr(channel, 'archiver', None), 'id', None), getattr(channel, 'auto_archive_duration', None), getattr(channel, 'archive_timestamp', None), getattr(getattr(channel, 'starter_message', None), 'id', None), getattr(channel, 'bitrate', None), getattr(channel, 'rtc_region', None), getattr(channel, 'user_limit', None), getattr(channel, 'video_quality_mode', None), getattr(getattr(channel, 'icon', None), 'url', None), getattr(guild, 'id', None)])
        fp.seek(0)
        await ctx.reply(file=discord.File(fp, filename='exported_channels.csv'))
    
    @export.command(name='roles')
    @commands.is_owner()
    async def export_roles(self, ctx: ContextU):
        """Export all roles."""

        fp = io.StringIO()
        writer = csv.writer(fp)
        #id, created_at, role_id, name, role_created_at, hoist, "position", unicode_emoji, managed, mentionable, is_default, is_bot_managed, is_premium_subscriber, permissions, icon_url, flags, guild_id
        writer.writerow(['id', 'created_at', 'role_id', 'name', 'role_created_at', 'hoist', 'position', 'unicode_emoji', 'managed', 'mentionable', 'is_default', 'is_bot_managed', 'is_premium_subscriber', 'permissions', 'icon_url', 'flags', 'guild_id'])
        roles: Dict[discord.Guild, List[discord.Role]]= {}
        for guild in self.bot.guilds:
            for role in guild.roles:
                if guild.id not in roles.keys():
                    roles[guild] = [role]
                else:
                    roles[guild].append(role)
                # use getattr for all attributes
                #writer.writerow([getattr(role, 'id', None), ])
        
        for tr, (guild, guild_roles) in enumerate(roles.items()):
            for role in guild_roles:
                writer.writerow([tr, getattr(role, 'created_at', None), getattr(role, 'id', None), getattr(role, 'name', None), getattr(role, 'created_at', None), getattr(role, 'hoist', None), getattr(role, 'position', None), getattr(role, 'unicode_emoji', None), getattr(role, 'managed', None), getattr(role, 'mentionable', None), role.is_default(), role.is_bot_managed(), role.is_premium_subscriber(), getattr(role, 'permissions', None).value, getattr(getattr(role, 'icon', None), 'url', None), getattr(role, 'flags', None), getattr(guild, 'id', None)])
        fp.seek(0)
        await ctx.reply(file=discord.File(fp, filename='exported_roles.csv'))

    # @tasks.loop(seconds=10)
    # async def log_new_authorized_users(self):
    #     #await self.bot.wait_until_ready()

    #     unlogged_additions = await ApplicationAuthorizations.filter(application_id=self.bot.user.id, has_been_logged=False, is_reauthorization=False, integration_type=1)

    #     if not unlogged_additions:
    #         return
        
    #     for auth in unlogged_additions:
    #         user = await self.bot.getorfetch_user(auth.user_id, None)

    #         if "applications.commands" in auth.scopes:
    #             if not auth.is_removal:
    #                 await self.on_user_add(user)
    #             else:
    #                 await self.on_user_remove(user)
            
    #         elif "identify" in auth.scopes:
    #             if not auth.is_removal:
    #                 await self.on_user_authorization(user)
    #             else:
    #                 await self.on_user_deauthorization(user)
    #         auth.has_been_logged = True
    #         await auth.save()




old_on_error = commands.AutoShardedBot.on_error


async def on_error(self, event: str, *args: Any, **kwargs: Any) -> None:
    (exc_type, exc, tb) = sys.exc_info()
    # Silence command errors that somehow get bubbled up far enough here
    if isinstance(exc, commands.CommandInvokeError):
        return

    e = makeembed_bot(title='Event Error', color=0xA32952, footer_icon_url=self.user.display_avatar.url)
    e.add_field(name='Event', value=event)
    trace = "".join(traceback.format_exception(exc_type, exc, tb))
    e.description = f'```py\n{trace}\n```'
    e.timestamp = discord.utils.utcnow()

    args_str = ['```py']
    for index, arg in enumerate(args):
        args_str.append(f'[{index}]: {arg!r}')
    args_str.append('```')
    e.add_field(name='Args', value='\n'.join(args_str), inline=False)
    hook = self.get_cog('Statistics').webhook
    try:
        await hook.send(embed=e)
    except Exception:
        pass


async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError, /) -> None:
    command = interaction.command
    error = getattr(error, 'original', error)

    if isinstance(error, (discord.Forbidden, discord.NotFound, )):#menus.MenuError)):
        return

    hook = interaction.client.get_cog('Statistics').webhook  # type: ignore
    e = makeembed_bot(title='App Command Error', color=0xCC3366, footer_icon_url=interaction.client.user.display_avatar.url)

    if command is not None:
        if command._has_any_error_handlers():
            return

        e.add_field(name='Name', value=command.qualified_name)

    e.add_field(name='User', value=f'{interaction.user} (ID: {interaction.user.id})')

    fmt = f'Channel: {interaction.channel} (ID: {interaction.channel_id})'
    if interaction.guild:
        fmt = f'{fmt}\nGuild: {interaction.guild} (ID: {interaction.guild.id})'

    e.add_field(name='Location', value=fmt, inline=False)
    e.add_field(name='Namespace', value=' '.join(f'{k}: {v!r}' for k, v in interaction.namespace), inline=False)

    exc = ''.join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
    e.description = f'```py\n{exc}\n```'
    e.timestamp = interaction.created_at

    try:
        await hook.send(embed=e)
    except Exception:
        pass


async def setup(bot: BotU):
    if not hasattr(bot, 'command_stats'):
        bot.command_stats = Counter()

    if not hasattr(bot, 'socket_stats'):
        bot.socket_stats = Counter()

    if not hasattr(bot, 'command_types_used'):
        bot.command_types_used = Counter()

    cog = Stats(bot)
    await bot.add_cog(cog)
    bot.logging_handler = handler = LoggingHandler(cog)
    logging.getLogger().addHandler(handler)
    commands.AutoShardedBot.on_error = on_error
    bot.old_tree_error = bot.tree.on_error  # type: ignore
    bot.tree.on_error = on_app_command_error


async def teardown(bot: BotU):
    commands.AutoShardedBot.on_error = old_on_error
    logging.getLogger().removeHandler(bot.logging_handler)
    bot.tree.on_error = bot.old_tree_error  # type: ignore
    del bot.logging_handler