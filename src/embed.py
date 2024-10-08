import discord
from typing import Optional

from utils import makeembed_bot

class EmbedBuilder:

    def __init__(
        self,
        title: str = '',
        url: str = '',
        description: str = '',
        fields: Optional[list] = None,
        color: discord.Color = discord.Color.default(),
        footer: Optional[str] = None,
        thumbnail: Optional[str] = None,
        image: Optional[str] = None,
    ):
        self.title = title
        self.url = url
        self.description = description
        self.fields = fields
        self.color = color
        self.footer = footer
        self.thumbnail = thumbnail
        self.image = image

    def __str__(self) -> str:
        # return self.build() as a dict
        return str(self.build().to_dict())

    def build(self):
        embed = makeembed_bot(
            title=self.title,
            description=self.description,
            color=self.color
        )

        if self.fields:
            for field in self.fields:
                embed.add_field(
                    name=field['name'],
                    value=field['value'],
                    inline=field['inline']
                )
        if self.url:
            embed.url = self.url
        if self.footer:
            embed.set_footer(text=self.footer)

        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)

        if self.image:
            embed.set_image(url=self.image)

        return embed
