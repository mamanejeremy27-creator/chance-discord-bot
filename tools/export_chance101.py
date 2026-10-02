"""
Export the Chance 101 course from the Marketing Hub into this bot.

    python tools/export_chance101.py [path to chance-marketing-hub]

Reads the episode files (templates/explainer-carousel/episodes/*.json) and the
rendered slides (templates/explainer-carousel/output/<episode>/*.jpg), then writes
1080 px slides and text.json (each slide's words, for the Text version button) to
assets/chance101/. It also copies telegram.json (the episode list and quizzes the
Telegram bot uses). Needs Pillow (pip install pillow); the bots themselves don't.
Run it after re-rendering the carousel, then check chance101.EPISODES still matches.
"""

import json
import re
import sys
from pathlib import Path

from PIL import Image

BOT = Path(__file__).resolve().parents[1]
OUT = BOT / "assets" / "chance101"
SIZE = 1080


def plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def bold_lines(text: str) -> str:
    return "\n".join(f"**{line}**" for line in text.split("\n") if line)


def slide_text(slide: dict):
    """The slide's words as Discord markdown, or None for covers."""
    kind = slide["kind"]
    if kind == "cover":
        return None
    parts = [f"**{plain(slide['headline'])}**" + (f"\n{slide['body']}" if slide.get("body") else "")]
    if kind == "explainer":
        visual = slide["visual"]
        if visual["type"] == "callouts":
            lines = [f"**{n}. {item['label']}:** {item.get('detail', '')}".rstrip()
                     for n, item in enumerate(visual["items"], start=1)]
        elif visual["type"] == "compare":
            lines = [f"**{col['label']}:** {col['text']}" for col in visual["columns"]]
        elif visual["type"] == "list":
            lines = [f"**{item['label']}:** {item.get('detail', '')}".rstrip() for item in visual["items"]]
        elif visual["type"] == "split":
            lines = [f"{visual['total']}:"] + [f"**{p['label']}:** {p.get('detail', '')}".rstrip() for p in visual["parts"]]
        else:
            raise SystemExit(f"unknown visual type {visual['type']}")
        parts.append("\n".join(lines))
    elif kind == "takeaway":
        if slide.get("proofs"):
            parts.append("\n".join(f"• {p['label']}" for p in slide["proofs"]))
        if slide.get("signoff"):
            parts.append(bold_lines(slide["signoff"]))
        if slide.get("rules"):
            parts.append(slide["rules"])
    text = "\n\n".join(parts)
    if len(text) > 4000:
        raise SystemExit(f"slide text too long for Discord ({len(text)} chars)")
    return text


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    hub = Path(sys.argv[1]) if len(sys.argv) > 1 else BOT.parent / "chance-marketing-hub"
    carousel = hub / "templates" / "explainer-carousel"
    OUT.mkdir(parents=True, exist_ok=True)
    texts = {}
    for path in sorted((carousel / "episodes").glob("ep*.json")):
        episode = json.loads(path.read_text(encoding="utf-8"))
        for n, slide in enumerate(episode["slides"], start=1):
            name = f"{episode['id']}-{n:02d}.jpg"
            src = carousel / "output" / episode["id"] / name
            if not src.is_file():
                raise SystemExit(f"missing render: {src} (run npm run render:carousel first)")
            Image.open(src).convert("RGB").resize((SIZE, SIZE), Image.LANCZOS).save(
                OUT / name, "JPEG", quality=85, optimize=True, progressive=True)
            texts[name] = slide_text(slide)
        print(f"{episode['id']}: {len(episode['slides'])} slides")
    (OUT / "text.json").write_text(json.dumps(texts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(texts)} slides and text.json written to {OUT}")
    posts = carousel / "telegram.json"
    if not posts.is_file():
        raise SystemExit(f"missing {posts} (the Telegram episode list and quizzes)")
    (OUT / "telegram.json").write_bytes(posts.read_bytes())
    print(f"telegram.json copied ({len(json.loads(posts.read_text(encoding='utf-8'))['episodes'])} episodes)")


if __name__ == "__main__":
    main()
