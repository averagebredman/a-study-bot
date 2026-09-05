"""/analyze and "Analyze Paper": parse marked DSE paper images and record weak topics."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.ai_client import AIError
from utils.pdf_handler import AttachmentError, prepare_image
from utils.subjects import normalize_subject

EMBED_COLOR = discord.Color.blue()
REVIEW_TIMEOUT_SECONDS = 300


def _clip(text: str, limit: int = 900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _unique_topics(topics: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for topic in topics:
        name = str(topic.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append({"name": name, "evidence": str(topic.get("evidence") or "").strip()})
    return unique


def _review_embed(
    subject: str,
    detected: str,
    topics: list[dict[str, str]],
) -> discord.Embed:
    embed = discord.Embed(title="Review the weak topics found", color=EMBED_COLOR)
    subject_text = f"**{subject}**"
    if detected and detected != subject:
        subject_text += f"\nDetected as: {_clip(detected, 900)}"
    embed.add_field(name="Subject", value=subject_text, inline=False)
    lines = []
    for number, topic in enumerate(topics, start=1):
        line = f"**{number}. {topic['name']}**"
        if topic["evidence"]:
            line += f"\n{_clip(topic['evidence'], 300)}"
        lines.append(line)
    embed.description = _clip("\n".join(lines), 3900)
    embed.set_footer(
        text="Deselect anything the AI got wrong, then press Save. "
        "Nothing is saved until you confirm."
    )
    return embed


def _saved_embed(
    subject: str,
    saved: list[dict[str, str]],
    skipped: list[dict[str, str]],
) -> discord.Embed:
    embed = discord.Embed(title="Analysis confirmed", color=discord.Color.green())
    description = (
        f"**{len(saved)} weak topic(s) saved for {subject}:** "
        + ", ".join(topic["name"] for topic in saved)
    )
    if skipped:
        description += (
            "\n\n**Skipped (not recorded):** "
            + ", ".join(topic["name"] for topic in skipped)
        )
    embed.description = _clip(description, 3900)
    embed.set_footer(text="Run /quiz to drill the saved topics.")
    return embed


class _TopicSelect(discord.ui.Select):
    """Multi-select listing every weak topic the vision model found."""

    def __init__(self, topics: list[dict[str, str]]) -> None:
        options = [
            discord.SelectOption(
                label=_clip(str(topic["name"]), 100),
                description=_clip(topic["evidence"] or "No evidence shown", 100).replace(
                    "\n", " "
                ),
                value=str(index),
                default=True,
            )
            for index, topic in enumerate(topics)
        ]
        super().__init__(
            placeholder="Tick the topics that are really weak…",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, _TopicReviewView):
            return
        self.view.selected = {int(value) for value in self.values}
        await interaction.response.defer()


class _TopicReviewView(discord.ui.View):
    """Lets the user keep or drop topics before anything reaches the database."""

    def __init__(
        self,
        bot: commands.Bot,
        user_id: int,
        subject: str,
        detected: str,
        topics: list[dict[str, str]],
    ) -> None:
        super().__init__(timeout=REVIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.user_id = user_id
        self.subject = subject
        self.detected = detected
        self.topics = topics
        self.selected = set(range(len(topics)))
        self.embed = _review_embed(subject, detected, topics)
        self.message: discord.Message | None = None
        if topics:
            self.add_item(_TopicSelect(topics))

    def _disable(self) -> None:
        for child in self.children:
            child.disabled = True

    async def on_timeout(self) -> None:
        self._disable()
        self.embed.set_footer(
            text="Review expired — nothing was saved. Run /analyze again to retry."
        )
        if self.message is not None:
            try:
                await self.message.edit(embed=self.embed, view=self)
            except (discord.HTTPException, discord.NotFound):
                pass

    @discord.ui.button(
        label="Save selected",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def save_selected(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        indices = sorted(self.selected) if self.selected else []
        if not indices:
            await interaction.response.send_message(
                "No topics are ticked, so there is nothing to save. "
                "If the AI got it all wrong, press Discard instead.",
                ephemeral=True,
            )
            return
        selected = _unique_topics([self.topics[i] for i in indices])
        all_topics = _unique_topics(self.topics)
        saved_names = {topic["name"] for topic in selected}
        skipped = [topic for topic in all_topics if topic["name"] not in saved_names]
        try:
            await self.bot.db.record_analyzed_topics(
                self.user_id,
                self.subject,
                [topic["name"] for topic in selected],
            )
        except Exception:
            await interaction.response.send_message(
                "Could not save the topics right now; please try again.",
                ephemeral=True,
            )
            return
        self._disable()
        await interaction.response.edit_message(
            embed=_saved_embed(self.subject, selected, skipped),
            view=self,
        )
        self.stop()

    @discord.ui.button(
        label="Discard",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def discard(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self._disable()
        embed = discord.Embed(
            title="Analysis discarded",
            description="No weak topics were saved. "
            "If the AI misread the paper, try a clearer photo or crop the image.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


async def _run_analysis(
    bot: commands.Bot,
    interaction: discord.Interaction,
    parts: list[tuple[bytes, str]],
) -> None:
    await interaction.followup.send(
        f"Reading the paper ({len(parts)} image{'s' if len(parts) != 1 else ''}) "
        "and its marks — this usually takes under a minute…",
        ephemeral=True,
    )
    try:
        result = await bot.ai.analyze_paper_image(parts)
    except AIError as exc:
        await interaction.followup.send(f"Analysis failed: {exc}", ephemeral=True)
        return

    weak_topics = _unique_topics(result["weak_topics"])
    subject = normalize_subject(result["subject"])
    detected = (result["subject"] or "").strip()
    if not weak_topics:
        embed = discord.Embed(title="Paper analysis complete", color=EMBED_COLOR)
        embed.add_field(
            name="No weak topics found",
            value="Looks like a clean paper — nothing recorded. Nice work!",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    view = _TopicReviewView(
        bot,
        interaction.user.id,
        subject,
        detected,
        weak_topics,
    )
    view.message = await interaction.followup.send(
        embed=view.embed,
        view=view,
        ephemeral=True,
    )
    await view.wait()


class Analyzer(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="analyze",
        description="Analyse a marked DSE paper page image and record weak topics.",
    )
    async def analyze(
        self, interaction: discord.Interaction, image: discord.Attachment
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        try:
            await self.bot.db.ensure_user(user.id, str(user))
            parts = [prepare_image(await image.read(), image.filename)]
        except AttachmentError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        await _run_analysis(self.bot, interaction, parts)


@app_commands.context_menu(name="Analyze Paper")
async def analyze_paper_menu(
    interaction: discord.Interaction, message: discord.Message
) -> None:
    """Analyse every image attached to a message as one paper."""
    await interaction.response.defer(ephemeral=True)
    user = interaction.user
    attachments = [a for a in message.attachments if a.filename]
    if not attachments:
        await interaction.followup.send(
            "That message has no attachments to analyse.", ephemeral=True
        )
        return
    try:
        await interaction.client.db.ensure_user(user.id, str(user))  # type: ignore[attr-defined]
        parts = [
            prepare_image(await attachment.read(), attachment.filename)
            for attachment in attachments
        ]
    except AttachmentError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await _run_analysis(interaction.client, interaction, parts)  # type: ignore[arg-type]
