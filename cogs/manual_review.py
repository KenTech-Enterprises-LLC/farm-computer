import datetime
from gettext import gettext as _
from typing import Any, List, Optional

import discord
from discord import Object, app_commands
from discord.ext import commands

from cogs.models import Blacklist, Commands, ReportedErrors
from cogs.stats import Stats
from main import PROD
from utils import (
    BotU,
    CogU,
    ContextU,
    CustomBaseView,
    SUPPORT_SERVER,
    dchyperlink,
    emojidict,
    group,
    hybrid_group,
    makeembed,
    makeembed_bot,
    makeembed_failedaction,
    makeembed_successfulaction,
)
# apatb 1

MANUAL_REVIEW_FORUM = 1277467073810923561

NORMAL_TAG_SOLVED = 1277467137992294514
NORMAL_TAG_COMPLETED = 1277467138336096358
NORMAL_TAG_ACCEPTED = 1171666153274343564
NORMAL_TAG_PENDING = 1253173654918008953
NORMAL_TAG_DENIED = 1277467141032906874
NORMAL_TAG_OTHER = 1277467144661106741

NORMAL_TAG_NONBUG = 1296107336246755438
NORMAL_TAG_FEATURE_REQUEST = 1277467143608209434
NORMAL_TAG_MANUALREVIEW = 1277467144132759575
NORMAL_TAG_POTENTIAL_BUG = 1277467143138705450
NORMAL_TAG_ACTUAL_BUG = 1277467142597378069

ERROR_ROLE = 1296312005661036575

GUILDS = [1029151630215618600]

RESPONSE_REQUEST_WEBHOOK = "https://canary.discord.com/api/webhooks/1319401605019471903/JbndHFu07_nHa4IyVvewt7IsXvNNRVSwe4qIj7gPtFXNIpA_iGmV3rscfuzYJwywPowu"

# support server

# MANUAL_REVIEW_FORUM = 1295899402656944220

# NORMAL_TAG_SOLVED = 1295901664372326506
# NORMAL_TAG_COMPLETED = 1295901666020823072
# NORMAL_TAG_DENIED = 1295901668327817258
# NORMAL_TAG_PENDING = 1295901664372326506
# NORMAL_TAG_ACCEPTED = 1295901672006090785
# NORMAL_TAG_OTHER = 1295901683146162179
# NORMAL_TAG_FEATURE_REQUEST = 1295901677777453150
# NORMAL_TAG_POTENTIAL_BUG = 1295901675898540115
# NORMAL_TAG_ACTUAL_BUG = 1295901674107306126
# NORMAL_TAG_MANUALREVIEW = 1295901679778267137
# NORMAL_TAG_NONBUG = 1296108015480471572

# GUILDS = [1220413076243742790]

# RESPONSE_REQUEST_WEBHOOK = "https://discord.com/api/webhooks/1295904855361064970/wQN1pKFNzcT7oVFT2JOH7STQhuYj9C109mc1oMu4BdsHw_m-i9SQnChuwCzb3kobH8dB"

def format_request_num(num: Any, include_formatting: bool=False) -> str:
    if include_formatting:
        return f"`#{num}`"
    return f"{num}"

class ReportErrorView(CustomBaseView):
    message: Optional[discord.Message] = None

    def __init__(self, reporting_user: discord.abc.User, error_id: str, error_forum: discord.ForumChannel,  cog: 'ManualReviewCog', addl_buttons: List[discord.ui.Button]=[],  *args, **kwargs):
        if 'message' not in kwargs:
            kwargs['message'] = None

        super().__init__(*args, **kwargs)
        self.error_id = error_id
        self.error_forum = error_forum
        self.reporting_user = reporting_user
        self.cog = cog

        for button in addl_buttons:
            self.add_item(button)

    @discord.ui.button(label=_("Report Error"), style=discord.ButtonStyle.red)
    async def report_error(self, interaction: discord.Interaction, button: discord.ui.Button,):
        #await interaction.response.defer(thinking=True, ephemeral=True)

        if not self.reporting_user:
            return await interaction.response.send_message("You must be the user who ran this command to report this error.")
        
        if PROD:
            if await Blacklist.is_blacklisted(self.reporting_user.id):
                if isinstance(interaction.client, BotU):
                    support_server_cmd = await interaction.client.get_command_mention("support")
                else:
                    support_server_cmd = "`/support`"
                return await interaction.response.send_message(f"You are blacklisted from reporting errors. If you believe this is a mistake, please contact the developers in the support server. See {support_server_cmd} for more information.")
        await interaction.response.send_modal(ReportErrorModal(view=self))

    async def on_modal_submit(self, interaction: discord.Interaction, modal: 'ReportErrorModal'):
        await interaction.response.defer(thinking=True, ephemeral=True)

        # confirm = await prompt(
        #     interaction,
        #     "Are you sure that you want to report this error? Note that misuse/spam of this feature may result in a blacklist.",
        #     author_id=interaction.user.id,
        #     delete_after=True,
        # )
        confirm = True

        thread = None

        if confirm:
            emb = makeembed_bot(
                title="Error Report",
                color=discord.Color.red(),
            )
            emb.add_field(name="Error ID", value=f"`{self.error_id}`")
            emb.add_field(name="User", value=f"{self.reporting_user.mention} (`{self.reporting_user.name}`)")
            emb.add_field(name="Guild", value=f"`{interaction.guild.name}` (`{interaction.guild.id}`)" if interaction.guild else "DMs".strip())
            emb.add_field(name="Channel", value=f"`#{interaction.channel.name}` (`{interaction.channel.id}`)" if interaction.channel else "DMs")
            emb.add_field(name="Message", value=f"{dchyperlink(self.message.jump_url, 'Jump to Message')}" if self.message else "No message")
            emb.add_field(name="User Description", value=modal.description.value if modal.description.value else "No description provided.", inline=False)
            #emb.add_field(name="Command", value=f"`{ctx.command.name}`")

            unconfirmed_bug_tag = discord.utils.find(lambda t: t.name.lower() == "Potential Bug".lower(), self.error_forum.available_tags)
            applied_tags = [unconfirmed_bug_tag] if unconfirmed_bug_tag else []

            thread, message = await self.error_forum.create_thread(
                name=f"{self.reporting_user.name} - `{self.error_id}`",
                content=f"<@&{ERROR_ROLE}>",
                embed=emb,
                reason=f"Error reported by {self.reporting_user.id}",
                applied_tags=applied_tags,
            )

            reported_error = await ReportedErrors.create(
                error_id=str(self.error_id),
                user_id=self.reporting_user.id,

                forum_id=self.error_forum.id,
                forum_post_id=thread.id,
                forum_initial_message_id=message.id,

                user_description=modal.description.value if modal.description.value else None,
                error_message=None,
            )

            #emb = makeembed_successfulaction(description="The error has been reported to the developers. Thank you for your help.")
            #msg = await interaction.followup.send(embed=emb, ephemeral=True, wait=True)

            assert isinstance(interaction.client, BotU)
            ch = await interaction.client.getorfetch_dm(interaction.user)

            try:
                emb = makeembed_bot("Reported Error: `{}`".format(self.error_id),description="You just submitted an error for manual review (`{}`). A Developer will be back with you in up to 48 hours either with followup questions or a success message.".format(self.error_id))
                await ch.send(embed=emb)
            except:
                await interaction.followup.send("The bot just tried to DM you, but your DM settings are closed. Please open them or you will be unable to recieve updates and questions relating to your error request.",)
            self.report_error.disabled = True
        elif confirm is None:
            emb = makeembed_failedaction(
                description="Not reporting error: Timed out."
            )
        else:
            emb = makeembed_failedaction(
                description="Not reporting error: Did not confirm."
            )
        
        await interaction.followup.send(embed=emb, ephemeral=True)

        if self.message:
            await self.message.edit(view=self)
        
        #await asyncio.sleep(10)
        if (embed := (await self.cog.get_command_invocation_embed(self.error_id))) and thread is not None:
            await thread.send(embed=embed)


class ReportErrorModal(discord.ui.Modal):
    description = discord.ui.TextInput(label=_("Description"), placeholder=_("Enter any relevant information/any other info that you think will be helpful for the developers."), min_length=10, max_length=2000, style=discord.TextStyle.paragraph, required=False)

    def __init__(self, *args, view: ReportErrorView, **kwargs):
        self.view = view
        if 'title' not in kwargs.keys() and len(args) == 0:
            kwargs['title'] = "Report a Command Error"
        super().__init__(*args, **kwargs)

    async def on_submit(self, interaction: discord.Interaction):
        return await self.view.on_modal_submit(interaction, self)

class ManualReviewCog(CogU, hidden=True):
    def __init__(self, bot: BotU):
        self.bot = bot

        allowed_contexts = app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False)
        allowed_installs = app_commands.AppInstallationType(guild=True, user=False)

        self.bot.tree.add_command(app_commands.ContextMenu(
            name=_('Get Command Invocation'),
            callback=self.get_commandinvocation_ctxmenu,
            allowed_contexts=allowed_contexts,
            allowed_installs=allowed_installs,
            guild_ids=GUILDS,
        ))
    
    @hybrid_group(name=_('request'),description=_('Commands for managing error reports.'))#,guilds=GUILDS)
    @commands.is_owner()
    #@Cooldown(1, 5, BucketType.user)
    @commands.guild_only()
    @app_commands.guilds(*GUILDS)
    async def request(self, ctx: ContextU):
        """Commands for managing error reports."""
        pass
    
    @request.command(name=_('reply'),description=_('Reply to a error report.'))#,guilds=GUILDS)
    @commands.is_owner()
    @commands.guild_only()
    @app_commands.describe(
    message=_('The message to send to the user.'),
    annonymous='Whether or not to perform this action annonymously\.',
    attachment1=_('An Attachment to send to the user.'),attachment2=_('An Attachment to send to the user.'),attachment3=_('An Attachment to send to the user.'),
    attachment4=_('An Attachment to send to the user.'),attachment5=_('An Attachment to send to the user.'),attachment6=_('An Attachment to send to the user.'),
    attachment7=_('An Attachment to send to the user.'),attachment8=_('An Attachment to send to the user.'),attachment9=_('An Attachment to send to the user.'),
    attachment10=_('An Attachment to send to the user.'),
    )
    async def reply(self, ctx: ContextU, *, message: str, annonymous: bool=False, 
        attachment1: Optional[discord.Attachment]=None, attachment2: Optional[discord.Attachment]=None, attachment3: Optional[discord.Attachment]=None,
        attachment4: Optional[discord.Attachment]=None, attachment5: Optional[discord.Attachment]=None, attachment6: Optional[discord.Attachment]=None,
        attachment7: Optional[discord.Attachment]=None, attachment8: Optional[discord.Attachment]=None, attachment9: Optional[discord.Attachment]=None,
        attachment10: Optional[discord.Attachment]=None):
        """Replies to an active error report."""

        await ctx.defer()
        try:
            request = await ReportedErrors.filter(forum_post_id=ctx.channel.id).first()
            assert request is not None

            command_invocation = await Commands.filter(transaction_id=request.error_id).first()
            if not command_invocation:
                return await ctx.reply("I was unable to find the command invocation for this error report.")

            if len(message) > 2000:
                return await ctx.reply("Your message is too long.")

            user = await self.bot.getorfetch_user(request.user_id, await self.bot.getorfetch_guild(command_invocation.guild_id))

            dm = await self.bot.getorfetch_dm(user)

            replyer = str(ctx.author) if not annonymous else 'The Developers'

            desc = "> {} | Reply from {}:".format(emojidict.get('person'), replyer)
            
            desc += "> ```{}```".format(message)

            desc += "> To reply, put `[#{}]` at the beginning of your message.".format(format_request_num(request.error_id, include_formatting=False))
            
            emb = makeembed_bot(
                author=replyer,
                author_icon_url=ctx.author.display_avatar.url if not annonymous else None,
                timestamp=datetime.datetime.now(),
                description=desc,
                footer=f"Reported Error #{request.error_id}",
                color=discord.Colour.brand_green()
            )

            # attachments: Optional[Union[discord.File, List[discord.File]]] = []
            # for x in [attachment1, attachment2, attachment3, attachment4, attachment5, attachment6, attachment7, attachment8, attachment9, attachment10]:
            #     if x is not None: attachments.append(await x.to_file())
            files = []
            for x in [attachment1, attachment2, attachment3, attachment4, attachment5, attachment6, attachment7, attachment8, attachment9, attachment10]:
                if x is not None: 
                    files.append(await x.to_file())
            
            # if len(attachments) == 1:
            #     attachments = attachments[0]
            try:
                #await dm.send(content=str(message), embed=emb, files=attachments)
                await dm.send(embed=emb, files=files)
            except discord.Forbidden:
                return await ctx.reply(embed=makeembed_failedaction(description="I was unable to DM the user."))
            
            await ctx.reply(embed=makeembed_successfulaction(description="Successfully sent message to user."))

        except AssertionError:
            return await ctx.reply(embed=makeembed_failedaction(description="This command can only be run in a error report thread."))

    @request.command(name=_('complete'),description=_('Complete a error report.'))#,guilds=GUILDS)
    @commands.is_owner()
    @commands.guild_only()
    @app_commands.describe(annonymous='Whether or not to perform this action annonymously\.')
    async def solved(self, ctx: ContextU, reason: Optional[str]=None, annonymous: bool=False):
        """Marks a error report as complete."""
        await ctx.defer()

        try:
            thread: discord.Thread = ctx.channel # type: ignore

            request = await ReportedErrors.filter(forum_post_id=ctx.channel.id).first()

            if request is None:
                return await ctx.reply(embed=makeembed_failedaction("This thread is not a error report."))
            
            request.resolved = True
            await request.save()

            try:
                command_invocation = await Commands.filter(transaction_id=request.error_id).first()
                if not command_invocation:
                    return await ctx.reply(embed=makeembed_failedaction("I was unable to find the command invocation for this error report."))

                user = await self.bot.getorfetch_user(request.user_id, await self.bot.getorfetch_guild(command_invocation.guild_id))

                dm = await self.bot.getorfetch_dm(user)
                
                if annonymous:
                    completed_by = ''
                else:
                    completed_by = " by {} ({})".format(ctx.author, ctx.author.mention)
                
                desc = "Your error report `#{}` has been marked as completed{}.".format(format_request_num(request.error_id, include_formatting=False), completed_by)
                if reason:
                    desc += "Reason: `{}`".format(reason)
                emb = makeembed_bot(
                    title='Your error report has been completed.',
                    description=desc,
                    color=discord.Colour.dark_gray(),
                    timestamp=datetime.datetime.now(),
                    footer=f"Reported Error #{format_request_num(request.error_id, include_formatting=False)}",
                    #footer_icon_url=self.bot.display_avatar.url,
                    bot=self.bot,
                )
                await dm.send(embed=emb)
            except Exception:
                await ctx.reply(embed=makeembed_failedaction("I was unable to DM the user."))

            # new_tags are the non solved, completed, etc tags 
            tags = thread.applied_tags
            new_tags = []
            for tag in tags:
                if not tag.moderated:
                    new_tags.append(tag)
            
            m = thread.starter_message
            if m is None:
                m = [x async for x in thread.history(limit=1,oldest_first=True)][0]
            
            emb = m.embeds[0].copy()
            emb.set_field_at(3,name='Status',value='Completed',inline=True)
            emb.color = discord.Colour.brand_green()

            await m.edit(content=m.content,embed=emb)

            await ctx.reply(embed=makeembed_successfulaction("Successfully marked error review `#{}` as completed.".format(format_request_num(request.error_id, include_formatting=False))))
            
            await thread.edit(
                archived=True,
                locked=True,
                applied_tags=[Object(NORMAL_TAG_COMPLETED), Object(NORMAL_TAG_SOLVED)] + new_tags, # type: ignore
                reason="Marked as complete by {}.".format(ctx.author),
            )
        except AssertionError:
            return await ctx.reply(embed=makeembed_failedaction("This command can only be run in a error report thread."))

    @request.command(name='deny',description='Deny a error report.')#,guilds=GUILDS)
    @commands.is_owner()
    @commands.guild_only()
    @app_commands.describe(annonymous=_('Whether or not to perform this action annonymously.'))
    async def deny(self, ctx: ContextU, reason: Optional[str]=None, annonymous: bool=False):
        """Denies a error report."""
        await ctx.defer()

        try:
            assert isinstance(ctx.channel, discord.Thread)

            thread: discord.Thread = ctx.channel
            
            if not await ReportedErrors.filter(forum_post_id=ctx.channel.id).exists():
                return await ctx.reply("This thread is not a error report.")
            
            request = await ReportedErrors.filter(forum_post_id=ctx.channel.id).first()

            if request is None:
                return await ctx.reply(embed=makeembed_failedaction("This thread is not a error report."))
            
            if request.resolved:
                return await ctx.reply(embed=makeembed_failedaction("This request has already been completed."))

            request.resolved = True
            await request.save()

            try:
                command_invocation = await Commands.filter(transaction_id=request.error_id).first()
                if not command_invocation:
                    return await ctx.reply(embed=makeembed_failedaction("I was unable to find the command invocation for this error report."))

                user = await self.bot.getorfetch_user(request.user_id, await self.bot.getorfetch_guild(command_invocation.guild_id))

                dm = await self.bot.getorfetch_dm(user)

                if annonymous:
                    denied_by = ''
                else:
                    denied_by = " by {} ({})".format(ctx.author, ctx.author.mention)

                desc = "Your error report `#{}` has been marked as denied{}.".format(format_request_num(request.error_id, include_formatting=False), denied_by)
                if reason:
                    desc += "Reason: `{}`".format(reason)
                emb = makeembed_bot(
                    title=_('Your error report has been denied.'),
                    description=desc,
                    color=discord.Colour.brand_red(),
                    timestamp=datetime.datetime.now(),
                    footer=f"Reported Error #{format_request_num(request.error_id, include_formatting=False)}",
                    bot=self.bot,
                )
                await dm.send(embed=emb)
            except Exception:
                await ctx.reply(embed=makeembed_failedaction("I was unable to DM the user."))
            
            # new_tags are the non solved, completed, etc tags 
            tags = thread.applied_tags
            new_tags = []
            for tag in tags:
                if not tag.moderated:
                    new_tags.append(tag)
            
            m = thread.starter_message
            if m is None:
                m = [x async for x in thread.history(limit=1,oldest_first=True)][0]
            
            emb = m.embeds[0].copy()
            emb.set_field_at(3,name='Status',value='Denied',inline=True)
            emb.color = discord.Colour.brand_red()

            await m.edit(content=m.content,embed=emb)
            
            await thread.edit(
                archived=True,
                locked=True,
                applied_tags=[Object(id=NORMAL_TAG_DENIED),Object(id=NORMAL_TAG_SOLVED)] + new_tags, # type: ignore
                reason=f'Marked as denied by {ctx.author}.',
            )
            await ctx.reply(embed=makeembed_successfulaction("Successfully marked request `#{}` as denied.".format(format_request_num(request.error_id, include_formatting=False))))
        except AssertionError:
            return await ctx.reply(embed=makeembed_failedaction("This command can only be run in a error report thread."))

    @request.command(name=_('reopen'),description=_('Reopen a error report.'))#,guilds=GUILDS)
    @commands.is_owner()
    @commands.guild_only()
    @app_commands.describe(annonymous=_('Whether or not to perform this action annonymously.'))
    async def reopen(self, ctx: ContextU, reason: Optional[str]=None, annonymous: bool=False):
        """Reopen a closed error report."""
        await ctx.defer()

        thread: discord.Thread = ctx.channel # type: ignore

        request = await ReportedErrors.filter(forum_post_id=ctx.channel.id).first()

        if request is None:
            return await ctx.reply(embed=makeembed_failedaction("This thread is not a error report."))

        if not request.resolved:
            return await ctx.reply(embed=makeembed_failedaction("This request is still open."))
        
        request.resolved = False
        await request.save()

        try:
            command_invocation = await Commands.filter(transaction_id=request.error_id).first()
            if not command_invocation:
                return await ctx.reply(embed=makeembed_failedaction("I was unable to find the command invocation for this error report."))

            user = await self.bot.getorfetch_user(request.user_id, await self.bot.getorfetch_guild(command_invocation.guild_id))

            dm = await self.bot.getorfetch_dm(user)

            if annonymous:
                reopened_by = ''
            else:
                reopened_by = " by {} ({})".format(ctx.author, ctx.author.mention)

            desc = "Your error report `#{}` has been reopened{}.".format(format_request_num(request.error_id, include_formatting=False), reopened_by)
            if reason:
                desc += "Reason: `{}`".format(reason)
            emb = makeembed_bot(
                title='Your error report has been reopened.',
                description=desc,
                color=discord.Colour.orange(),
                timestamp=datetime.datetime.now(),
                footer=f"Reported Error #{format_request_num(request.error_id, include_formatting=False)}",
                bot=self.bot
            )
            await dm.send(embed=emb)
        except Exception:
            await ctx.reply(embed=makeembed_failedaction("I was unable to DM the user."))
        
        # new_tags are the non solved, completed, etc tags 
        tags = thread.applied_tags
        new_tags = []
        for tag in tags:
            if not tag.moderated:
                new_tags.append(tag)
        
        m = thread.starter_message
        if m is None:
            m = [x async for x in thread.history(limit=1,oldest_first=True)][0]
        
        emb = m.embeds[0].copy()

        emb.set_field_at(2,name='Claimed By',value=ctx.author.mention,inline=True)
        emb.set_field_at(3,name='Status',value='In Progress',inline=True)
        emb.color = discord.Colour.dark_gold()

        await m.edit(content=m.content,embed=emb)

        await thread.edit(
            archived=False,
            locked=False,
            applied_tags=[Object(id=NORMAL_TAG_PENDING)] + new_tags, # type: ignore
        )

        await ctx.reply(makeembed_successfulaction("Successfully reopened request `#{}`.".format(format_request_num(request.error_id, include_formatting=False))))

    @discord.utils.cached_property
    def response_webhook(self):
        return discord.Webhook.from_url(RESPONSE_REQUEST_WEBHOOK,client=self.bot)

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.author.bot: 
            return

        if msg.guild is None: # dm command
            assert isinstance(msg.channel, discord.DMChannel)

            if not msg.content.startswith('[#'):
                return

            # print(msg.content)
            # print(msg.content[2:])
            # print(msg.content.index(']')+1)
            # print(msg.content[msg.content.index(']'):])
            # print(msg.content[msg.content.index(']')+1:].strip())

            request_num = msg.content.strip()[2:msg.content.index(']')].strip()

            request = await ReportedErrors.filter(error_id=request_num).first()

            if request is None:
                return await msg.reply(embed=makeembed_failedaction("I was unable to find that request."))
            
            if request.resolved:
                return await msg.reply(embed=makeembed_failedaction("This request has already been completed."))
            
            if request.user_id != msg.author.id:
                return await msg.reply(embed=makeembed_failedaction("You are not the user who created this request."))
            
            if msg.content.replace(f'[#{format_request_num(request_num, include_formatting=False)}]','').strip() == '':
                return await msg.reply(embed=makeembed_failedaction("You must include a message."))
            
            thread= await self.bot.getorfetch_thread(request.forum_post_id, await self.bot.getorfetch_guild(GUILDS[0]))
            assert thread is not None and isinstance(thread, discord.Thread)

            content = msg.content.replace(f'[#{format_request_num(request_num, include_formatting=False)}] ','').strip()

            try:
                wb = self.response_webhook

                files = []

                for x in msg.attachments:
                    if x is not None:
                        files.append(await x.to_file())
                    
                await wb.send(
                    content=content,
                    username=str(msg.author),
                    avatar_url=msg.author.display_avatar.url,
                    thread=Object(id=request.forum_post_id),
                    wait=True,
                    files=files,
                )

                await msg.add_reaction(emojidict.get(True))

                await msg.reply(embed=makeembed_successfulaction("Successfully sent your message.\nA Developer will be back with you in up to 48 hours. If you do not recieve a response within that timeframe, please send another message or join the {}.".format(dchyperlink(SUPPORT_SERVER, "Support Server"))))
            except discord.HTTPException:
                await msg.add_reaction(emojidict.get(False))
                await msg.reply(embed=makeembed_failedaction('I had problems sending your message. Try sending it again. If this continues please alert The Developers.'))
        
        elif isinstance(msg.channel, discord.Thread) and msg.channel.parent_id == MANUAL_REVIEW_FORUM:
            if not msg.content.startswith('areply') and not msg.content.startswith('reply'): 
                return
            
            reported_error = await ReportedErrors.filter(forum_post_id=msg.channel.id).first()

            if reported_error is not None and not reported_error.resolved:
                request = reported_error
                assert request is not None
                
                annonymous = msg.content.startswith('areply ')

                user = await self.bot.getorfetch_user(request.user_id,msg.guild)

                dm = await self.bot.getorfetch_dm(user)

                replyer = str(msg.author) if not annonymous else 'The Developers'

                desc = "> {} | Reply from {}:\n\n".format(emojidict.get('person'), replyer)

                message = msg.content.replace('areply ','').replace('reply ','')
                
                desc += f"> ```{message}```\n"

                desc += "> To reply, put `[#{}]` at the beginning of your message.".format(format_request_num(request.error_id, include_formatting=False))
                
                emb = makeembed_bot(
                    author=replyer,
                    author_icon_url=msg.author.display_avatar.url if not annonymous else None,
                    timestamp=datetime.datetime.now(),
                    description=desc,
                    footer=f"Reported Error #{format_request_num(request.error_id, include_formatting=False)}",
                    color=discord.Colour.brand_green()
                )

                # if len(attachments) == 1:
                #     attachments = attachments[0]
                try:
                    #await dm.send(content=str(message), embed=emb, files=attachments)
                    await dm.send(embed=emb, files=[await x.to_file() for x in msg.attachments])
                except Exception:
                    return await msg.reply("I was unable to DM the user.")
                
                await msg.reply("Successfully sent message to user.")

    @group(name=_('commandinvocation'), description=_('Command invocation'), aliases=['ci'],hidden=True)
    @commands.is_owner()
    @commands.guild_only()
    async def commandinvocation(self, ctx: ContextU):
        """Commands for managing command invocations."""
        pass

    @commandinvocation.command(name=_('list'),description=_('List command invocations.'),aliases=['ls'])
    # @app_commands.describe(
    #     user='The user to list command invocations for.',
    #     guild='The guild to list invocations for.',
    #     channel='The channel to list invocations for.',
    #     limit='The number of invocations to list.',
    #     offset='The number of invocations to skip.',
    # )
    async def list_commandinvocation(self, ctx: ContextU, user: Optional[discord.User]=None, guild: Optional[discord.Guild]=None, channel: Optional[discord.TextChannel]=None, limit: int=10, offset: int=0):
        """List command invocations."""
        await ctx.defer()
        query = {}
        if user is not None:
            query['author'] = user.id
        if guild is not None:
            query['guild'] = guild.id
        if channel is not None:
            query['channel'] = channel.id
        
        invocations = await Commands.filter(**query).limit(limit).offset(offset)
        
        if not invocations:
            return await ctx.reply("No invocations found.")
        
        emb = makeembed(
            title='Command Invocations',
            description='\n'.join([f"`{x.used.strftime('%Y-%m-%d %H:%M:%S')}` | `{x.command_id}` | `{x.command}` | `{x.author}` | `{x.guild_id}` | `{x.channel_id}`" for x in invocations]),
            color=discord.Colour.dark_gold(),
            timestamp=datetime.datetime.now(),
            footer=f"Showing {len(invocations)} of {await Commands.filter(**query).count()} invocations.",
        )
        await ctx.reply(embed=emb)

    @commandinvocation.command(name=_('get'),description=_('Get a command invocation.'),aliases=['g'])
    async def get_commandinvocation(self, ctx: ContextU, transaction_id: str):
        """Get a command invocation."""
        await ctx.defer()

        return await ctx.reply(embed=await self.get_command_invocation_embed(transaction_id))
    
    async def get_commandinvocation_ctxmenu(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.defer()
        if message.author == self.bot.user:
            transaction_id = discord.utils.find(lambda f: f.name.lower() == 'error id', message.embeds[0].fields)
            if transaction_id:
                return await interaction.followup.send(embed=await self.get_command_invocation_embed(str(transaction_id.value)))
        return await interaction.followup.send("This message is not a command invocation.")

    async def get_command_invocation_embed(self, transaction_id: str):
        command_invocation = await Commands.filter(transaction_id=transaction_id).first()
        if command_invocation is None:
            stats_cog: Stats = self.bot.get_cog('Statistics')
            if stats_cog is not None:
                command_invocation = discord.utils.find(lambda x: x.get('transaction_id', None) == transaction_id, stats_cog._data_batch)
            return None
        
        emb = makeembed(
            title='Command Invocation',
            description=f"```{command_invocation.command}```",
            color=discord.Colour.dark_gold(),
            timestamp=datetime.datetime.now(),
            footer=f"Command ID: {command_invocation.command_id}",
        )
        emb.add_field(name='Command',value=command_invocation.command).add_field(name='User ID',value=command_invocation.user_id,inline=True).add_field(name='Guild ID',value=command_invocation.guild_id,inline=True).add_field(name='Channel ID',value=command_invocation.channel_id,inline=True)
        emb.add_field(name='Slash Command',value=command_invocation.prefix == '/',inline=True)#.add_field(name='Transaction ID',value=command_invocation.transaction_id,inline=True)
        emb.add_field(name='Args', value=f'`{command_invocation.args}`').add_field(name='Kwargs',value=f"`{command_invocation.kwargs}`")
        emb.add_field(name='DB', value=f'`{"Commands" if isinstance(command_invocation, Commands) else "CommandInvocation"}`').add_field(name='Error',value=f"`{getattr(command_invocation, 'error', 'N/A')}`")
        return emb
    
async def setup(bot: BotU):
    cog = ManualReviewCog(bot)
    await bot.add_cog(cog)
