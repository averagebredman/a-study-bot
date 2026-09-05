"""/quiz: interactive 3-question quiz over recorded weak topics."""

from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from services.ai_client import AIError
from utils.subjects import SUPPORTED_SUBJECTS

QUESTION_TIMEOUT_SECONDS = 120
TOPIC_LIMIT = 6
OPTION_LABELS = ("A", "B", "C", "D")
QUIZ_COLOR = discord.Color.purple()
SUBJECT_CHOICES = [app_commands.Choice(name=s, value=s) for s in SUPPORTED_SUBJECTS]


def _clip(text: str, limit: int = 900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _mcq_prompt_embed(
    question: dict[str, Any], number: int, total: int
) -> discord.Embed:
    options = question.get("options") or []
    lines = [_clip(str(question["question"]), 4000)]
    for index, option in enumerate(options):
        lines.append(f"**{OPTION_LABELS[index]}.** {option}")
    embed = discord.Embed(
        title=f"Question {number}/{total} · Multiple choice",
        description="\n".join(lines),
        color=QUIZ_COLOR,
    )
    embed.add_field(name="Topic", value=question["topic"], inline=False)
    embed.set_footer(text="Pick one option below. You have 2 minutes.")
    return embed


def _mcq_correct_text(question: dict[str, Any]) -> str:
    options = question.get("options") or []
    correct_index = question.get("correct_index")
    if isinstance(correct_index, int) and 0 <= correct_index < len(options):
        return f"{OPTION_LABELS[correct_index]}. {options[correct_index]}"
    return "?"


def _mcq_feedback_embed(
    question: dict[str, Any],
    number: int,
    total: int,
    chosen_label: str,
    correct: bool,
) -> discord.Embed:
    correct_answer = _mcq_correct_text(question)
    title = f"Question {number}/{total} — {'Correct' if correct else 'Incorrect'}"
    if correct:
        description = f"Your answer **{chosen_label}** is right."
    else:
        description = f"Your answer **{chosen_label}** is not correct."
    description += f"\n\n**Correct answer:** {_clip(correct_answer)}"
    explanation = question.get("model_answer") or "No explanation provided."
    description += f"\n\n**Explanation:** {_clip(str(explanation))}"
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.green() if correct else discord.Color.red(),
    )
    embed.add_field(name="Topic", value=question["topic"], inline=False)
    return embed


class McqView(discord.ui.View):
    """Four-option buttons for one MCQ; stops after the owner picks."""

    def __init__(
        self,
        author_id: int,
        question: dict[str, Any],
        number: int,
        total: int,
    ) -> None:
        super().__init__(timeout=QUESTION_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.question = question
        self.number = number
        self.total = total
        self.choice: str | None = None
        self.correct: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "That quiz belongs to someone else.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="A", style=discord.ButtonStyle.primary)
    async def option_a(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self._pick(interaction, "A")

    @discord.ui.button(label="B", style=discord.ButtonStyle.primary)
    async def option_b(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self._pick(interaction, "B")

    @discord.ui.button(label="C", style=discord.ButtonStyle.primary)
    async def option_c(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self._pick(interaction, "C")

    @discord.ui.button(label="D", style=discord.ButtonStyle.primary)
    async def option_d(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self._pick(interaction, "D")

    async def _pick(
        self, interaction: discord.Interaction, chosen_label: str
    ) -> None:
        options = self.question.get("options") or []
        correct_index = self.question.get("correct_index")
        self.choice = chosen_label
        self.correct = (
            isinstance(correct_index, int)
            and 0 <= correct_index < len(options)
            and OPTION_LABELS[correct_index] == chosen_label
        )
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        embed = _mcq_feedback_embed(
            self.question,
            self.number,
            self.total,
            chosen_label,
            bool(self.correct),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


def _short_prompt_embed(
    question: dict[str, Any], number: int, total: int
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Question {number}/{total} · Written answer",
        description=_clip(str(question["question"]), 4000),
        color=QUIZ_COLOR,
    )
    embed.add_field(name="Topic", value=question["topic"], inline=False)
    embed.set_footer(
        text="Type your answer in chat. You have 2 minutes — DSE marking applies."
    )
    return embed


def _short_feedback_embed(
    question: dict[str, Any],
    number: int,
    total: int,
    user_answer: str,
    correct: bool,
    feedback: str,
) -> discord.Embed:
    title = f"Question {number}/{total} — {'Correct' if correct else 'Not quite'}"
    description = f"**Your answer:** {_clip(user_answer)}\n\n"
    expected = str(question.get("model_answer") or "").strip()
    if expected:
        description += (
            f"**Expected answer (marking scheme):** {_clip(expected)}\n\n"
        )
    description += f"**Feedback:** {_clip(feedback) or 'No feedback provided.'}"
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.green() if correct else discord.Color.red(),
    )
    embed.add_field(name="Topic", value=question["topic"], inline=False)
    return embed


def _mcq_review_item(
    number: int,
    question: dict[str, Any],
    chosen_label: str | None,
    correct: bool | None,
) -> dict[str, Any]:
    options = question.get("options") or []
    chosen_text = ""
    if chosen_label and chosen_label in OPTION_LABELS:
        index = OPTION_LABELS.index(chosen_label)
        if 0 <= index < len(options):
            chosen_text = str(options[index])
    chosen = f"{chosen_label}. {chosen_text}" if chosen_text else "Not answered"
    detail = _clip(
        "\n".join(
            [
                f"**Question:** {_clip(str(question['question']), 400)}",
                f"**Your answer:** {_clip(chosen, 300)}",
                f"**Correct answer:** {_clip(_mcq_correct_text(question), 500)}",
                "**Explanation:** "
                + _clip(
                    str(
                        question.get("model_answer")
                        or "No explanation provided."
                    ),
                    500,
                ),
            ]
        ),
        1000,
    )
    return {
        "label": f"Q{number} · Multiple choice",
        "outcome": (
            "Correct"
            if correct is True
            else "Incorrect"
            if correct is False
            else "Not answered"
        ),
        "detail": detail,
    }


def _short_review_item(
    number: int,
    question: dict[str, Any],
    user_answer: str | None,
    correct: bool | None,
    feedback: str,
) -> dict[str, Any]:
    expected = str(question.get("model_answer") or "").strip()
    detail = _clip(
        "\n".join(
            [
                f"**Question:** {_clip(str(question['question']), 400)}",
                f"**Your answer:** {_clip(user_answer or 'Not answered', 300)}",
                "**Expected (marking scheme):** "
                + _clip(expected or "No model answer provided.", 700),
                f"**Feedback:** {_clip(feedback or '—', 300)}",
            ]
        ),
        1000,
    )
    return {
        "label": f"Q{number} · Written answer",
        "outcome": (
            "Correct"
            if correct is True
            else "Incorrect"
            if correct is False
            else "Not answered"
        ),
        "detail": detail,
    }


def _review_embed(items: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="Answer key — review your quiz",
        color=discord.Color.blue(),
    )
    for item in items:
        embed.add_field(
            name=f"{item['label']} · {item['outcome']}",
            value=item["detail"],
            inline=False,
        )
    embed.set_footer(text="Use /quiz to practise these topics again.")
    return embed


def _reported_ai_answer(question: dict[str, Any]) -> str:
    """Human-readable version of the AI's expected answer for a report."""
    explanation = str(question.get("model_answer") or "").strip()
    question_type = str(question.get("type") or "short").lower()
    if question_type == "mcq":
        parts = []
        correct = _mcq_correct_text(question)
        if correct != "?":
            parts.append(f"Correct answer: {correct}")
        if explanation:
            parts.append(f"Explanation: {explanation}")
        return " | ".join(parts)
    return explanation or "No expected answer was provided."


class _FlagQuestionModal(discord.ui.Modal):
    """Collects why the student believes the AI's expected answer is wrong."""

    note = discord.ui.TextInput(
        label="What is wrong, or what should the answer be?",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Q2 is actually about subnet masks — the expected answer should be B.",
        max_length=1000,
        required=True,
    )

    def __init__(
        self,
        bot: commands.Bot,
        user_id: int,
        subject: str,
        number: int,
        question: dict[str, Any],
    ) -> None:
        super().__init__(title=f"Flag question {number} answer")
        self.bot = bot
        self.user_id = user_id
        self.subject = subject
        self.question = question

    async def on_submit(self, interaction: discord.Interaction) -> None:
        question = self.question
        question_type = str(question.get("type") or "short").lower()
        if question_type != "mcq":
            question_type = "short"
        try:
            await self.bot.db.record_answer_report(
                self.user_id,
                self.subject,
                str(question.get("topic") or "").strip(),
                question_type,
                str(question.get("question") or "").strip(),
                _reported_ai_answer(question),
                self.note.value.strip(),
            )
        except Exception:
            await interaction.response.send_message(
                "Could not save the flag right now; please try again.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Thanks — this answer was flagged for review. "
            "It is saved for a later look and does not change your saved score.",
            ephemeral=True,
        )


class _FlagAnswerButton(discord.ui.Button):
    """One button per question on the end-of-quiz answer key."""

    def __init__(
        self,
        bot: commands.Bot,
        user_id: int,
        subject: str,
        number: int,
        question: dict[str, Any],
    ) -> None:
        super().__init__(
            label=f"Flag Q{number}",
            style=discord.ButtonStyle.secondary,
        )
        self.bot = bot
        self.user_id = user_id
        self.subject = subject
        self.number = number
        self.question = question

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            _FlagQuestionModal(
                self.bot,
                self.user_id,
                self.subject,
                self.number,
                self.question,
            )
        )


class _FlagAnswersView(discord.ui.View):
    """Flag buttons attached to the answer-key message."""

    def __init__(
        self,
        bot: commands.Bot,
        user_id: int,
        subject: str,
        questions: list[dict[str, Any]],
    ) -> None:
        super().__init__(timeout=600)
        self.message: discord.Message | None = None
        for number, question in enumerate(questions, start=1):
            self.add_item(
                _FlagAnswerButton(bot, user_id, subject, number, question)
            )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, discord.NotFound):
                pass


class Quiz(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active: dict[int, dict[str, Any]] = {}

    @app_commands.command(
        name="quiz", description="Start a 3-question quiz on your weak topics."
    )
    @app_commands.choices(subject=SUBJECT_CHOICES)
    async def quiz(
        self,
        interaction: discord.Interaction,
        subject: str | None = None,
    ) -> None:
        user = interaction.user
        user_id = user.id
        if user_id in self._active:
            await interaction.response.send_message(
                "You already have a quiz running. Finish it or wait for it to time out.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=False)
        await self.bot.db.ensure_user(user_id, str(user))
        if subject is None:
            subject = await self.bot.db.get_latest_subject(user_id)
        if subject is None:
            await interaction.followup.send(
                "You have no recorded weak topics yet. "
                "Run /analyze with a marked paper image first."
            )
            return
        topics = await self.bot.db.get_weak_topics(user_id, subject, TOPIC_LIMIT)
        if not topics:
            await interaction.followup.send(
                f"No weak topics recorded for **{subject}** yet. "
                "Run /analyze with a marked paper for that subject first."
            )
            return

        await interaction.followup.send(
            f"Preparing 3 **{subject}** questions from your weak topics…"
        )
        try:
            questions = await self.bot.ai.generate_quiz(topics)
        except AIError as exc:
            await interaction.followup.send(
                f"Could not generate the quiz: {exc}"
            )
            return

        session = {
            "questions": questions,
            "subject": subject,
            "correct": 0,
            "answered": 0,
            "review": [],
        }
        self._active[user_id] = session
        try:
            await self._play(interaction, session)
        finally:
            self._active.pop(user_id, None)

    async def _play(
        self, interaction: discord.Interaction, session: dict[str, Any]
    ) -> None:
        questions = session["questions"]
        total = len(questions)
        channel = interaction.channel
        live_message: discord.Message | None = None

        for number, question in enumerate(questions, start=1):
            question_type = str(question.get("type") or "short").lower()
            if question_type == "mcq":
                view = McqView(interaction.user.id, question, number, total)
                embed = _mcq_prompt_embed(question, number, total)
                if live_message is None:
                    live_message = await interaction.followup.send(
                        embed=embed, view=view
                    )
                else:
                    await live_message.edit(embed=embed, view=view)
                await view.wait()
                if view.choice is None:
                    timeout_embed = discord.Embed(
                        title=f"Question {number}/{total} timed out",
                        description="No answer was selected, so the quiz ends here.",
                        color=discord.Color.orange(),
                    )
                    await live_message.edit(embed=timeout_embed, view=None)
                    session["review"].append(
                        _mcq_review_item(number, question, None, None)
                    )
                    break
                await self.bot.db.record_quiz_answer(
                    interaction.user.id,
                    session["subject"],
                    question["topic"],
                    "mcq",
                    bool(view.correct),
                )
                session["answered"] += 1
                if view.correct:
                    session["correct"] += 1
                session["review"].append(
                    _mcq_review_item(
                        number, question, view.choice, bool(view.correct)
                    )
                )
            else:
                embed = _short_prompt_embed(question, number, total)
                if live_message is None:
                    live_message = await interaction.followup.send(embed=embed)
                else:
                    await live_message.edit(embed=embed, view=None)

                answer = await self._wait_for_typed_answer(interaction, channel)
                if answer is None:
                    timeout_embed = discord.Embed(
                        title=f"Question {number}/{total} timed out",
                        description="No answer was typed, so the quiz ends here.",
                        color=discord.Color.orange(),
                    )
                    await live_message.edit(embed=timeout_embed, view=None)
                    session["review"].append(
                        _short_review_item(number, question, None, None, "")
                    )
                    break
                try:
                    grade = await self.bot.ai.grade_answer(question, answer)
                except AIError as exc:
                    await live_message.edit(
                        embed=discord.Embed(
                            title=f"Question {number}/{total} grading failed",
                            description=f"Grading error: {exc}. Skipping this question.",
                            color=discord.Color.orange(),
                        ),
                        view=None,
                    )
                    continue
                correct = bool(grade.get("correct"))
                await self.bot.db.record_quiz_answer(
                    interaction.user.id,
                    session["subject"],
                    question["topic"],
                    "short",
                    correct,
                )
                session["answered"] += 1
                if correct:
                    session["correct"] += 1
                feedback = str(grade.get("feedback") or "").strip()
                await live_message.edit(
                    embed=_short_feedback_embed(
                        question,
                        number,
                        total,
                        answer,
                        correct,
                        feedback,
                    ),
                    view=None,
                )
                session["review"].append(
                    _short_review_item(
                        number, question, answer, correct, feedback
                    )
                )

        description = f"Score: **{session['correct']}/{total}**"
        unanswered = total - session["answered"]
        if unanswered:
            description += f" ({unanswered} question{'s' if unanswered != 1 else ''} not answered)"
        summary = discord.Embed(
            title="Quiz complete",
            description=description,
            color=discord.Color.gold(),
        )
        summary.set_footer(text="Run /stats to see updated weakness and accuracy.")
        await interaction.followup.send(embed=summary)
        if session["review"]:
            view = _FlagAnswersView(
                self.bot,
                interaction.user.id,
                session["subject"],
                session["questions"],
            )
            message = await interaction.followup.send(
                embed=_review_embed(session["review"]),
                view=view,
            )
            view.message = message
            asyncio.create_task(view.wait())

    async def _wait_for_typed_answer(
        self, interaction: discord.Interaction, channel: discord.abc.Messageable
    ) -> str | None:
        def check(message: discord.Message) -> bool:
            return (
                message.author == interaction.user
                and message.channel == channel
                and message.content.strip()
                and not message.content.lstrip().startswith("/")
            )

        try:
            message = await self.bot.wait_for(
                "message",
                check=check,
                timeout=QUESTION_TIMEOUT_SECONDS,
            )
            return message.content.strip()
        except asyncio.TimeoutError:
            return None
