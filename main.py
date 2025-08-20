import asyncio
import datetime
import time

import aiohttp
import discord
from discord.ext import commands
import environ
import yaml

from cogs import EXTENSIONS
from utils import (
    BotU,
    Help,
    MentionableTree,
    handler,
)

env = environ.Env(
    PROD=(bool, False),
    DEBUG=(bool, False)
)

PROD = env("PROD")

DEBUG = env("DEBUG")

if PROD:
    with open("client.yml", "r") as f:
        token = dict(yaml.safe_load(f)).get("token")
else:
    with open("client_beta.yml", "r") as f:
        token = dict(yaml.safe_load(f)).get("token")

prefixes = ["fc!"]

currentdate_epoch = int(time.time())
currentdate = datetime.datetime.fromtimestamp(currentdate_epoch)


if __name__ == "__main__":
    print(
    f"""Started running:
PROD: {PROD}
DEBUG: {DEBUG}
{currentdate}
{currentdate_epoch}"""
)

if PROD:
    intents = discord.Intents.default()
    #intents.message_content = True
else:
    intents = discord.Intents.all()

#intents.message_content = True
#intents.members = True

# class BotU(OldBotU):
#     blacklist: List
#     started_at: datetime.datetime

#     async def check_blacklist(self, ctx):
#         if getattr(self, _('blacklist'), None):
#             if blacklist_obj := discord.utils.find(lambda x: x.offender_id == ctx.author.id, self.blacklist):
#                 desc = _("You are currently blacklisted from using the bot. Please reach out to the bot developer on the support server for more information.")
#                 if blacklist_obj.reason:
#                     desc += _("Reason: `{}`").format(blacklist_obj.reason)
#                 emb = makeembed_failedaction(description=desc)
#                 await ctx.reply(embed=emb, ephemeral=True, delete_after=10 if not ctx.interaction else None)
#                 return False
#         return True


bot = BotU(
    command_prefix=commands.when_mentioned_or(*prefixes),
    intents=intents,
    activity=discord.Activity(type=discord.ActivityType.watching, name="Pelican Town"),
    status=discord.Status.online,
    help_command=Help(),
    tree_cls=MentionableTree,
    started_at=currentdate,
)
tree = bot.tree


@bot.event
async def on_ready():
    date = datetime.datetime.fromtimestamp(int(time.time()))
    print(f"{date}: Ready!")

async def main():
    #if PROD:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from utils import SENTRY_URL

        sentry_sdk.init(
            dsn=SENTRY_URL,
            # Set traces_sample_rate to 1.0 to capture 100%
            # of transactions for performance monitoring.
            traces_sample_rate=.5,
            # Set profiles_sample_rate to 1.0 to profile 100%
            # of sampled transactions.
            # We recommend adjusting this value in production.
            profiles_sample_rate=.5,

            environment="production" if PROD else "development",

            integrations=[
                AsyncioIntegration(),
            ],

            _experiments={
                "profiles_sample_rate": .5, #type: ignore
            },
        )
    except ImportError:
        pass

    async with aiohttp.ClientSession() as session:
        async with aiohttp.ClientSession() as session2:
            async with aiohttp.ClientSession() as session3:
                bot.session = session
                bot.session2 = session2
                bot.session3 = session3
                discord.utils.setup_logging(handler=handler)
                for file in EXTENSIONS:
                    await bot.load_extension(file)
                    #bot_logger.debug(f"Loaded extension {file}")
                await bot.load_extension("jishaku")
                #bot_logger.debug("Loaded extension jishaku")
                # await bot.load_extension("utils.cogs.error_handler")
                # bot_logger.debug("Loaded extension utils.cogs.error_handler")
                await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
