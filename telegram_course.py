"""
Chance 101 on Telegram: the same 13-episode course as the Discord bot, shown one slide
at a time in a private chat, with each episode's quiz as answer buttons.

Slides and their words (text.json) come from assets/chance101/, and the episode list and
quizzes from assets/chance101/telegram.json (the Marketing Hub's Telegram posts). Both
are written by tools/export_chance101.py. Every button carries its target in its
callback data (see data()), so a course keeps working after the bot restarts.

This module only builds screens; telegram_bot.py sends them.
"""

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "chance101"
SLIDES_PER_EPISODE = 4
SITE_URL = "https://chance.fun"
RULES = "18+ · Only where permitted"


def _load(name: str) -> dict:
    try:
        return json.loads((ASSET_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


COURSE = _load("telegram.json")
EPISODES = [(ep["id"], ep["title"]) for ep in COURSE.get("episodes", [])]
QUIZZES = [ep["quiz"] for ep in COURSE.get("episodes", [])]
SLIDE_TEXT = _load("text.json")
TOTAL_SLIDES = len(EPISODES) * SLIDES_PER_EPISODE

# Callback data: action, up to two numbers, then ":t" while the Text version is on.
# g:N slide N · q:E quiz for episode E · a:E:K answer K · m episodes · f finished · w welcome
_DATA = re.compile(r"(?P<action>[gqamfw])(?::(?P<x>\d{1,2}))?(?::(?P<y>\d))?(?P<text>:t)?")


@dataclass
class Screen:
    photo: str    # file name in ASSET_DIR
    caption: str  # Telegram HTML
    buttons: list  # rows of (label, callback data or https:// link)


def data(action: str, *numbers: int, text: bool = False) -> str:
    return ":".join([action, *map(str, numbers)]) + (":t" if text else "")


def slide_name(index: int) -> str:
    prefix, _ = EPISODES[index // SLIDES_PER_EPISODE]
    return f"{prefix}-{index % SLIDES_PER_EPISODE + 1:02d}.jpg"


def missing_files() -> list:
    """Slides or course data that aren't on disk (checked at startup)."""
    if not EPISODES:
        return ["telegram.json"]
    return [slide_name(i) for i in range(TOTAL_SLIDES) if not (ASSET_DIR / slide_name(i)).is_file()]


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _words(name: str) -> str:
    """A slide's words (Discord markdown in text.json) as Telegram HTML."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _esc(SLIDE_TEXT.get(name) or ""))


def _after_quiz(episode: int, text: bool) -> tuple:
    if episode + 1 < len(EPISODES):
        return "Next episode", data("g", (episode + 1) * SLIDES_PER_EPISODE, text=text)
    return "Finish", data("f", text=text)


def welcome(text: bool = False) -> Screen:
    caption = ("<b>Chance 101</b>\n"
               f"Everything about CHANCE.fun in {len(EPISODES)} short episodes, one slide at a time. "
               "Each episode ends with a one-question quiz.\n\n"
               "Only you can see this chat, so go at your own pace.\n"
               f"{RULES}")
    return Screen(slide_name(0), caption,
                  [[("Start the course", data("g", 0, text=text)), ("Pick an episode", data("m", text=text))]])


def slide(index: int, text: bool = False) -> Screen:
    """One slide with Back, Next (or the quiz), Episodes and Text version buttons."""
    index = max(0, min(index, TOTAL_SLIDES - 1))
    episode, number = divmod(index, SLIDES_PER_EPISODE)
    name = slide_name(index)
    caption = (f"<b>Episode {episode + 1} of {len(EPISODES)}: {_esc(EPISODES[episode][1])}</b>\n"
               f"Slide {number + 1} of {SLIDES_PER_EPISODE}")
    if text and SLIDE_TEXT.get(name):
        caption += "\n\n" + _words(name)

    back = data("g", index - 1, text=text) if index else data("w", text=text)
    if number == SLIDES_PER_EPISODE - 1:
        forward = ("Take the quiz", data("q", episode, text=text))
    else:
        forward = ("Next", data("g", index + 1, text=text))
    toggle = ("Hide text", data("g", index)) if text else ("Text version", data("g", index, text=True))
    return Screen(name, caption, [[("Back", back), forward], [("Episodes", data("m", text=text)), toggle]])


def quiz(episode: int, text: bool = False) -> Screen:
    """The episode's quiz: the options in the caption, numbered answer buttons below."""
    episode = max(0, min(episode, len(EPISODES) - 1))
    q = QUIZZES[episode]
    options = "\n".join(f"{n}. {_esc(option)}" for n, option in enumerate(q["options"], start=1))
    caption = (f"<b>Quiz · Episode {episode + 1} of {len(EPISODES)}</b>\n"
               f"{_esc(q['question'])}\n\n{options}")
    last = episode * SLIDES_PER_EPISODE + SLIDES_PER_EPISODE - 1
    answers = [(str(k + 1), data("a", episode, k, text=text)) for k in range(len(q["options"]))]
    return Screen(slide_name(last), caption,
                  [answers, [("Back", data("g", last, text=text)), ("Skip", _after_quiz(episode, text)[1])]])


def answer(episode: int, choice: int, text: bool = False) -> Screen:
    """Right or not, with the correct answer and the explanation."""
    episode = max(0, min(episode, len(EPISODES) - 1))
    q = QUIZZES[episode]
    correct = q["correct"]
    if choice == correct:
        verdict = "<b>Right.</b>"
    else:
        verdict = f"<b>Not quite.</b> The answer is {correct + 1}: {_esc(q['options'][correct])}."
    caption = (f"<b>Quiz · Episode {episode + 1} of {len(EPISODES)}</b>\n"
               f"{_esc(q['question'])}\n\n{verdict}\n{_esc(q['explanation'])}")
    last = episode * SLIDES_PER_EPISODE + SLIDES_PER_EPISODE - 1
    return Screen(slide_name(last), caption,
                  [[_after_quiz(episode, text)], [("Episodes", data("m", text=text))]])


def episodes(text: bool = False) -> Screen:
    """The episode list, with one numbered button per episode."""
    lines = "\n".join(f"{n}. {_esc(title)}" for n, (_, title) in enumerate(EPISODES, start=1))
    numbers = [(str(n + 1), data("g", n * SLIDES_PER_EPISODE, text=text)) for n in range(len(EPISODES))]
    return Screen(slide_name(0), f"<b>Chance 101</b>\nPick an episode:\n\n{lines}",
                  [numbers[i:i + 5] for i in range(0, len(numbers), 5)])


def finished(text: bool = False) -> Screen:
    """Shown after the last quiz, on the course's closing slide."""
    caption = ("<b>You finished Chance 101</b>\n"
               "That's the whole course: what Chance is, how to play, how winners are picked, "
               "getting paid, creating your own Chance and staying safe.\n\n"
               "<b>Create a Chance. Or take one. You decide.</b>\n"
               f"{RULES}")
    return Screen(slide_name(TOTAL_SLIDES - 1), caption,
                  [[("Start again", data("g", 0, text=text)), ("Episodes", data("m", text=text))],
                   [("Open chance.fun", SITE_URL)]])


def from_data(value: str):
    """The screen a button opens, or None if the data isn't ours."""
    match = _DATA.fullmatch(value or "")
    if not match or not EPISODES:
        return None
    action, text = match["action"], match["text"] is not None
    x = int(match["x"]) if match["x"] else 0
    if action == "g":
        return slide(x, text)
    if action == "q":
        return quiz(x, text)
    if action == "a" and match["y"]:
        return answer(x, int(match["y"]), text)
    if action == "m":
        return episodes(text)
    if action == "f":
        return finished(text)
    if action == "w":
        return welcome(text)
    return None


def from_start(payload: str) -> Screen:
    """The screen for /start, with an optional deep-link payload: go, or ep1 to ep13."""
    if payload == "go":
        return slide(0)
    match = re.fullmatch(r"ep(\d{1,2})", payload or "")
    if match and 1 <= int(match[1]) <= len(EPISODES):
        return slide((int(match[1]) - 1) * SLIDES_PER_EPISODE)
    return welcome()
