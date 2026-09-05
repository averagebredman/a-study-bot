"""/stats: show recorded weaknesses and per-topic quiz accuracy."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.subjects import SUPPORTED_SUBJECTS

STATS_COLOR = discord.Color.teal()
SUBJECT_CHOICES = [app_commands.Choice(name=s, value=s) for s in SUPPORTED_SUBJECTS]


def _accuracy_text(attempts: int, correct: int) -> str:
    if attempts == 0:
        return "No quiz attempts yet"
    return f"{correct}/{attempts} ({round(correct / attempts * 100)}%)"


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="stats",
        description="Show your weakest syllabus topics and quiz accuracy.",
    )
    @app_commands.choices(subject=SUBJECT_CHOICES)
    async def stats(
        self,
        interaction: discord.Interaction,
        subject: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        await self.bot.db.ensure_user(user.id, str(user))
        if subject is None:
            rows = await self.bot.db.get_subject_summary(user.id)
        else:
            rows = await self.bot.db.get_topic_stats(user.id, subject)
        if not rows:
            embed = discord.Embed(
                title="No study data yet",
                description=(
                    "Run /analyze with a marked DSE paper image to record weak topics, "
                    "then /quiz to build an accuracy record."
                ),
                color=STATS_COLOR,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        title = f"Your DSE study stats — {subject}" if subject else "Your DSE study stats"
        embed = discord.Embed(title=title, color=STATS_COLOR)
        if subject is None:
            for row in rows:
                embed.add_field(
                    name=row["subject"] or "Unknown",
                    value=(
                        f"Weak topics: **{row['topic_count']}** "
                        f"(score **{row['mistake_total']}**)\n"
                        f"Accuracy: **{_accuracy_text(int(row['attempts']), int(row['correct']))}**"
                    ),
                    inline=False,
                )
            embed.set_footer(
                text="Run /stats with a subject (Math, M2, ICT, Physics) for topic details."
            )
        else:
            for row in rows[:5]:
                topic_name = row["topic_name"]
                weakness = row["mistake_count"]
                attempts = int(row["attempts"])
                correct = int(row["correct"])
                value = (
                    f"Weakness score: **{weakness}**\n"
                    f"Accuracy: **{_accuracy_text(attempts, correct)}**"
                )
                embed.add_field(name=topic_name, value=value, inline=False)
            embed.set_footer(
                text="Weakness score counts how often a topic was flagged or answered wrong."
            )
        await interaction.followup.send(embed=embed, ephemeral=True)
