"""Async OpenRouter client: Qwen for vision, DeepSeek for text."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

VISION_MODEL = "qwen/qwen3-vl-30b-a3b-instruct"
TEXT_MODEL = "deepseek/deepseek-chat"

_ANALYZE_PROMPT = """You are an experienced HKDSE tutor reviewing a scanned, already MARKED exam paper.
You may receive one image or several images (pages/photos) that together make up one paper.
Treat all attached images as a single exam paper.
Each image may contain the printed question paper together with the student's handwritten answers,
ticks/crosses, corrections, circled errors and marks awarded by a teacher.

Read the paper carefully and:
1. Look across every page before judging the paper as a whole.
2. Identify the subject/paper (e.g. HKDSE ICT Paper 2A, Economics, Biology).
3. Find the syllabus topics where the student clearly lost marks or made mistakes.
   Use the corrections/marks as evidence. Ignore printing artefacts and blank margins.
4. Name each weak topic concisely in English using standard HKDSE syllabus terminology
   (e.g. "SQL queries", "Logic gates", "Normalization", "Network security").

Return ONLY valid JSON with no markdown fences, no commentary, in exactly this shape:
{"subject": "<subject or paper name>", "weak_topics": [{"name": "<topic>", "evidence": "<e.g. Page 2 Q3(b) wrong SQL clause; 2 marks lost>"}]}
Keep the JSON compact: at most 8 weak topics, one short evidence sentence each, and no
text before or after the JSON. If the paper has no obvious mistakes, return
{"subject": "...", "weak_topics": []}."""

_QUIZ_PROMPT_TEMPLATE = """You are an HKDSE tutor building a short practice quiz.
Weak topics for this student: {weak_topics}

Create exactly 3 questions. Use a mix of question types: at least one multiple-choice
question and at least one short-answer/long question. Aim each question at DSE level and
base it on the weak topics above. Questions must be answerable from the paper text alone.

Return ONLY valid JSON, no markdown fences, no commentary. It must be a JSON array of
exactly 3 objects:
[
  {{
    "type": "mcq",
    "topic": "<one of the weak topics>",
    "question": "<question text>",
    "options": ["<A>", "<B>", "<C>", "<D>"],
    "correct_index": <0-3>,
    "model_answer": "<one-line explanation of the correct answer>"
  }},
  {{
    "type": "short",
    "topic": "<one of the weak topics>",
    "question": "<short or long answer question>",
    "model_answer": "<marking-scheme style expected answer or key points>"
  }}
]
Every object must include exactly the keys shown for its type."""


class AIError(RuntimeError):
    """User-safe error raised when an AI provider call fails."""


def _provider_label(model: str) -> str:
    names = {"google": "Gemini", "deepseek": "DeepSeek", "qwen": "Qwen", "z-ai": "GLM"}
    vendor = model.split("/", 1)[0]
    return names.get(vendor, vendor.title())


def extract_json(text: str) -> Any:
    """Extract the first top-level JSON object or array from model output."""
    start = next((i for i, char in enumerate(text) if char in "{["), None)
    if start is None:
        raise AIError("The AI response contained no JSON.")
    depth = 0
    in_string = False
    escaped = False
    end = None
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise AIError("The AI response contained unfinished JSON.")
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise AIError("The AI response contained invalid JSON.") from exc


class AIClient:
    def __init__(self, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Title": "A Study Bot",
            },
            timeout=httpx.Timeout(180.0, connect=20.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            response = await self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            raise AIError("The AI provider timed out; please try again.") from exc
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = exc.response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict):
                    detail = str(error.get("message") or "")
                else:
                    detail = str(error or "")
            status = exc.response.status_code
            if status == 402:
                raise AIError(
                    "You are out of OpenRouter API credits. "
                    "Top up at https://openrouter.ai/settings/credits and try again."
                ) from exc
            if status == 401:
                raise AIError(
                    "OpenRouter rejected the API key. "
                    "Check OPENROUTER_API_KEY in your .env file."
                ) from exc
            suffix = f": {detail}" if detail else ""
            raise AIError(
                f"{_provider_label(model)} (via OpenRouter) "
                f"returned HTTP {status}{suffix}"
            ) from exc
        except httpx.RequestError as exc:
            raise AIError(
                "Could not reach the AI provider; please try again later."
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIError("The AI provider returned an unexpected response.") from exc

    async def analyze_paper_image(
        self, image_parts: list[tuple[bytes, str]]
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": _ANALYZE_PROMPT}]
        for image_bytes, content_type in image_parts:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{content_type};base64,{encoded}"
                    },
                }
            )
        messages = [{"role": "user", "content": content}]
        raw = await self._chat(
            VISION_MODEL, messages, temperature=0.1, max_tokens=4000
        )
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise AIError("The paper analyser did not return topic data.")
        weak_topics: list[dict[str, str]] = []
        for item in data.get("weak_topics") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                weak_topics.append(
                    {
                        "name": name,
                        "evidence": str(item.get("evidence") or "").strip(),
                    }
                )
        subject = str(data.get("subject") or "Unknown subject").strip()
        return {"subject": subject, "weak_topics": weak_topics}

    async def generate_quiz(self, weak_topics: list[str]) -> list[dict[str, Any]]:
        if not weak_topics:
            raise AIError("No weak topics available to build a quiz from.")
        prompt = _QUIZ_PROMPT_TEMPLATE.format(
            weak_topics=json.dumps(weak_topics, ensure_ascii=False)
        )
        raw = await self._chat(
            TEXT_MODEL,
            [{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000,
        )
        data = extract_json(raw)
        if not isinstance(data, list):
            raise AIError("The quiz generator did not return a list of questions.")
        questions: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            topic = str(item.get("topic") or "").strip()
            question = str(item.get("question") or "").strip()
            if not topic or not question:
                continue
            question_type = str(item.get("type") or "short").lower()
            if question_type == "mcq":
                options = [
                    str(option).strip()
                    for option in (item.get("options") or [])
                    if str(option).strip()
                ]
                correct_index = item.get("correct_index")
                if len(options) < 2 or not isinstance(correct_index, int):
                    continue
                if not 0 <= correct_index < len(options):
                    continue
                questions.append(
                    {
                        "type": "mcq",
                        "topic": topic,
                        "question": question,
                        "options": options[:4],
                        "correct_index": correct_index,
                        "model_answer": str(item.get("model_answer") or "").strip(),
                    }
                )
            else:
                questions.append(
                    {
                        "type": "short",
                        "topic": topic,
                        "question": question,
                        "model_answer": str(item.get("model_answer") or "").strip(),
                    }
                )
        if not questions:
            raise AIError("The quiz generator returned no usable questions.")
        return questions[:3]

    async def grade_answer(
        self, question: dict[str, Any], user_answer: str
    ) -> dict[str, Any]:
        prompt = f"""You are an HKDSE tutor grading one student answer.

Question: {json.dumps(question, ensure_ascii=False)}

Student answer:
{user_answer}

Grade the answer against the expected answer/model_answer inside the question using
standard DSE marking-scheme judgement (key points, not exact wording). Be fair: credit a
correct idea even if wording differs. Return ONLY valid JSON, no markdown, exactly:
{{"correct": true or false, "feedback": "<short, encouraging explanation of what was right or missing>"}}"""
        raw = await self._chat(
            TEXT_MODEL,
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=600,
        )
        data = extract_json(raw)
        if not isinstance(data, dict) or "correct" not in data:
            raise AIError("The answer grader did not return a verdict.")
        return {
            "correct": bool(data["correct"]),
            "feedback": str(data.get("feedback") or "").strip(),
        }
