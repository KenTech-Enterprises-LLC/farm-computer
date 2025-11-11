import discord
from discord.ext import commands, tasks
import environ
from tortoise import Tortoise
from tortoise.exceptions import TransactionManagementError, MultipleObjectsReturned

from cogs.models import DiscordChannels, DiscordGuilds, DiscordUsers
from utils import BotU, CogU

class DiscordLogging(CogU, hidden=True):
    def __init__(self, bot):
        self.bot = bot
    
    # guild
    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        try:
            await DiscordGuilds.from_guild(guild, self.bot)
        except TransactionManagementError:
            pass
    
    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        try:
            await DiscordGuilds.from_guild(guild, self.bot)
        except TransactionManagementError:
            pass
    
    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        try:
            await DiscordGuilds.from_guild(after, self.bot)
        except TransactionManagementError:
            pass
    
    # @commands.Cog.listener()
    # async def on_guild_available(self, guild: discord.Guild):
    #     await DiscordGuilds.from_guild(guild, self.bot)
    
    # @commands.Cog.listener()
    # async def on_guild_unavailable(self, guild: discord.Guild):
    #     await DiscordGuilds.from_guild(guild, self.bot)

    # roles
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        try:
            await DiscordGuilds.from_guild(role.guild, self.bot)
        except TransactionManagementError:
            pass
    
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await DiscordGuilds.from_guild(role.guild, self.bot)
    
    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        try:
            await DiscordGuilds.from_guild(after.guild, self.bot)
        except TransactionManagementError:
            pass

    # channels

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        try:
            await DiscordChannels.from_channel(channel, self.bot)
        except TransactionManagementError:
            pass
    
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        try:
            await DiscordChannels.from_channel(channel, self.bot)
        except TransactionManagementError:
            pass
    
    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        try:
            await DiscordChannels.from_channel(after, self.bot)
        except (TransactionManagementError, MultipleObjectsReturned):
            pass
    
    @commands.Cog.listener()
    async def on_private_channel_update(self, before: discord.GroupChannel, after: discord.GroupChannel):
        try:
            await DiscordChannels.from_channel(after, self.bot)
        except TransactionManagementError:
            pass

    # @commands.Cog.listener()
    # async def on_thread_create(self, thread: discord.Thread):
    #     await DiscordChannels.from_channel(thread, self.bot)
    
    # @commands.Cog.listener()
    # async def on_thread_delete(self, thread: discord.Thread):
    #     await DiscordChannels.from_channel(thread, self.bot)
    
    # @commands.Cog.listener()
    # async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
    #     await DiscordChannels.from_channel(after, self.bot)
    

    # members

    # @commands.Cog.listener()
    # async def on_member_join(self, member: discord.Member):
    #     await DiscordMembers.from_member(member, self.bot)
    
    # @commands.Cog.listener()
    # async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent): # covers on_member_ban
    #     await DiscordUsers.from_user(payload.user, self.bot)
    
    # @commands.Cog.listener()
    # async def on_member_update(self, before: discord.Member, after: discord.Member):
    #     await DiscordMembers.from_member(after, self.bot)
    
    # @commands.Cog.listener()
    # async def on_member_unban(self, guild: discord.Guild, user: discord.User):
    #     await DiscordUsers.from_user(user, self.bot)
    
    # @commands.Cog.listener()
    # async def on_user_update(self, before: discord.User, after: discord.User):
    #     await DiscordUsers.from_user(after, self.bot)
    
    # @commands.Cog.listener()
    # async def on_presence_update(self, before: discord.Member, after: discord.Member):
    #     await DiscordMembers.from_member(after, self.bot)
    
    # message

    # @commands.Cog.listener()
    # async def on_message(self, message: discord.Message):
    #     await DiscordMessages.from_message(message, self.bot)

    # @commands.Cog.listener()
    # async def on_message_edit(self, before: discord.Message, after: discord.Message):
    #     await DiscordMessages.from_message(after, self.bot)

    # @commands.Cog.listener()
    # async def on_message_delete(self, message: discord.Message):
    #     await DiscordMessages.from_message(message, self.bot)
    
    # @commands.Cog.listener()
    # async def on_message_bulk_delete(self, messages: list):
    #     for message in messages:
    #         await DiscordMessages.from_message(message, self.bot)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.update.is_running():
            self.update.start()
    
    @tasks.loop(hours=1)
    async def update(self):
        await self.bot.wait_until_ready()
        
        guild_ids = []

        for guild in self.bot.guilds:
            guild_ids.append(guild.id)
            await DiscordGuilds.from_guild(guild, self.bot)
        
        # update bot_in_guild for all guilds

        for guild in await DiscordGuilds.filter(bot_in_guild=True):
            if guild.guild_id not in guild_ids:
                guild.bot_in_guild = False
                await guild.save()

            # for channel in guild.channels:
            #     await DiscordChannels.from_channel(channel, self.bot)
            # for member in guild.members:
            #     await DiscordMembers.from_member(member, self.bot)
        #     for role in guild.roles:
        #         await DiscordRoles.from_role(role, self.bot)
        # for message in self.bot.cached_messages:
        #     await DiscordMessages.from_message(message, self.bot)
        for member in self.bot.users:
            await DiscordUsers.from_user(member, self.bot)

async def setup(bot: BotU):
    env = environ.Env(
        PROD=(bool, False),
        DEBUG=(bool, False),
    )

    PROD = env("PROD")
    if PROD:
        cog = DiscordLogging(bot)
        #cog.update.start()
        await bot.add_cog(cog)
        await Tortoise.init(config_file='db.yml')
