"""
Chance 101: the 13-episode explainer course, shown privately with buttons.

The slides are exported from the Marketing Hub's explainer carousel
(chance-marketing-hub/templates/explainer-carousel), resized to 1080 px,
and stored in assets/chance101/. Every button keeps its target in its
custom_id (c101:...), so a course keeps working after the bot restarts.
Needs discord.py 2.4+ for DynamicItem.
"""

from pathlib import Path

import discord

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "chance101"
SLIDES_PER_EPISODE = 4
MINT = discord.Color(0x00F386)
SITE_URL = "https://chance.fun"

# (file prefix, title) in course order
EPISODES = [
    ("ep01-what-is-chance", "What is Chance?"),
    ("ep02-getting-set-up", "Getting set up"),
    ("ep03-reading-a-chance", "Reading a Chance"),
    ("ep04-taking-an-instant-win", "Taking an Instant Win"),
    ("ep05-taking-a-multiwin", "Taking a MultiWin"),
    ("ep06-what-an-entry-costs", "What an entry costs"),
    ("ep07-how-the-winner-is-picked", "How the winner is picked"),
    ("ep08-getting-paid", "Getting paid"),
    ("ep09-creating-a-chance", "Creating a Chance"),
    ("ep10-sharing-and-referrals", "Sharing and referrals"),
    ("ep11-points-and-chance-games", "Points and Chance Games"),
    ("ep12-the-chance-token", "The CHANCE token"),
    ("ep13-staying-safe", "Staying safe"),
]
TOTAL_SLIDES = len(EPISODES) * SLIDES_PER_EPISODE


def slide_name(index: int) -> str:
    prefix, _ = EPISODES[index // SLIDES_PER_EPISODE]
    return f"{prefix}-{index % SLIDES_PER_EPISODE + 1:02d}.jpg"


def missing_slides() -> list:
    """Slide files that aren't on disk (checked at startup)."""
    return [slide_name(i) for i in range(TOTAL_SLIDES) if not (ASSET_DIR / slide_name(i)).is_file()]


def _attach(name: str, embed: discord.Embed) -> discord.File:
    embed.set_image(url=f"attachment://{name}")
    return discord.File(ASSET_DIR / name, filename=name)


class CourseButton(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"c101:(?P<action>start|startmenu|menu|done|go:\d{1,2})"):
    """A course button whose custom_id says what it opens (a slide, the episode list or the end)."""

    def __init__(self, action: str, label: str, style=discord.ButtonStyle.secondary,
                 row=None, disabled: bool = False):
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"c101:{action}",
                                           row=row, disabled=disabled))
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(match["action"], item.label or "Chance 101", item.style)

    async def callback(self, interaction: discord.Interaction):
        if self.action == "start":
            await send_private(interaction, 0)
            return
        if self.action == "startmenu":
            embed, view = episodes_screen()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return
        if self.action == "menu":
            embed, view = episodes_screen()
            file = None
        elif self.action == "done":
            embed, file, view = finished_screen()
        else:
            embed, file, view = slide_screen(int(self.action[3:]))
        await interaction.response.edit_message(embed=embed, attachments=[file] if file else [], view=view)


def slide_screen(index: int):
    """One slide with Back, Next and Episodes buttons."""
    index = max(0, min(index, TOTAL_SLIDES - 1))
    episode, slide = divmod(index, SLIDES_PER_EPISODE)
    _, title = EPISODES[episode]
    embed = discord.Embed(title=f"{episode + 1}. {title}", color=MINT)
    embed.set_footer(text=f"Episode {episode + 1} of {len(EPISODES)} · Slide {slide + 1} of {SLIDES_PER_EPISODE}")
    file = _attach(slide_name(index), embed)

    view = discord.ui.View(timeout=None)
    if index == 0:
        view.add_item(CourseButton("go:0", "Back", row=0, disabled=True))
    else:
        view.add_item(CourseButton(f"go:{index - 1}", "Back", row=0))
    if index == TOTAL_SLIDES - 1:
        view.add_item(CourseButton("done", "Finish", discord.ButtonStyle.success, row=0))
    else:
        label = "Next episode" if slide == SLIDES_PER_EPISODE - 1 else "Next"
        view.add_item(CourseButton(f"go:{index + 1}", label, discord.ButtonStyle.success, row=0))
    view.add_item(CourseButton("menu", "Episodes", row=0))
    return embed, file, view


def episodes_screen():
    """The episode list, with one numbered button per episode."""
    lines = [f"**{n}.** {title}" for n, (_, title) in enumerate(EPISODES, start=1)]
    embed = discord.Embed(title="Chance 101", description="\n".join(lines), color=MINT)
    embed.set_footer(text="Pick an episode. Only you can see this.")

    view = discord.ui.View(timeout=None)
    for n in range(len(EPISODES)):
        view.add_item(CourseButton(f"go:{n * SLIDES_PER_EPISODE}", str(n + 1), row=n // 5))
    return embed, view


def finished_screen():
    """Shown after the last slide."""
    embed = discord.Embed(
        title="You finished Chance 101",
        description=("That's the whole course: what Chance is, how to play, how winners are picked, "
                     "getting paid, creating your own Chance and staying safe.\n\n"
                     "**Create a Chance. Or take one. You decide.**\n\n"
                     "18+, only where permitted."),
        color=MINT,
    )
    view = discord.ui.View(timeout=None)
    view.add_item(CourseButton("go:0", "Start again", row=0))
    view.add_item(CourseButton("menu", "Episodes", row=0))
    view.add_item(discord.ui.Button(label="Open chance.fun", style=discord.ButtonStyle.link, url=SITE_URL, row=0))
    return embed, None, view


async def send_private(interaction: discord.Interaction, index: int = 0):
    """Open the course for this user only, at a slide."""
    embed, file, view = slide_screen(index)
    await interaction.response.send_message(embed=embed, file=file, view=view, ephemeral=True)


def public_post():
    """The permanent post with the start buttons (posted by /postchance101)."""
    embed = discord.Embed(
        title="Chance 101",
        description=("New here? Learn everything about Chance in 13 short episodes: what it is, "
                     "how to take a Chance, how winners are picked, getting paid, creating your own "
                     "and staying safe.\n\n"
                     "The course opens privately, so only you see it. Go at your own pace."),
        color=MINT,
    )
    embed.set_footer(text=f"{TOTAL_SLIDES} slides · about 10 minutes · 18+, only where permitted")
    file = _attach(slide_name(0), embed)

    view = discord.ui.View(timeout=None)
    view.add_item(CourseButton("start", "Start Chance 101", discord.ButtonStyle.success, row=0))
    view.add_item(CourseButton("startmenu", "Pick an episode", row=0))
    return embed, file, view
