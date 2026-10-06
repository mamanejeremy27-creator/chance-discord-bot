"""
Chance Live overlay: a stream overlay for one Chance, and the small server it needs.

    python overlay_server.py        (listens on PORT, default 8787)

A streamer adds  https://<this server>/overlay/?chance=multiwin-12  (or the Chance's
chance.fun link) as a Browser Source in OBS, sized 1920x1080. The page asks this server
for the Chance every few seconds. The server reads chance.fun's subgraph, which a page on
another site isn't allowed to read directly, and caches each Chance for a few seconds, so
many overlays on the same Chance cost one upstream call.

Page options: &pos=bl|br|tl|tr (corner, default bl), &scale=0.5..2, &demo=1 (a labelled
sample win every 20 seconds, for setting up a scene), &replay=1 (show the latest real win
again when the overlay loads).

A win on stream is a result that paid more than its purchase cost. Chance's data marks every
tier hit as won, including a MultiWin Tier 1 that pays back less than the entry, and a loss
is a loss.
"""

import asyncio
import os
import re
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # keep the repo's tracked __pycache__ untouched
for _stream in (sys.stdout, sys.stderr):  # chance_data logs with emoji; a Windows console can't print them
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
from aiohttp import web  # noqa: E402

import chance_data as cd  # noqa: E402

ROOT = Path(__file__).resolve().parent
OVERLAY_DIR = ROOT / "overlay"
API_URL = cd.resolve_api_url()
CACHE_SECONDS = 4
WINS_SHOWN = 5
WINS_CHECKED = 25  # newest results marked won; the real wins among them are shown
_ID = re.compile(r"(instant|multiwin)-\d{1,7}")

_cache: dict = {}   # id -> (fetched at, payload)
_locks: dict = {}

PRIZE_FIELDS = ("id prizeType prizeToken entryToken prizeAmount entryPrice numberRange endTime status hasWinner "
                "winner entriesSold maxEntries remainingPrize tier1Prize tier2Prize tier3Prize tier4Prize")
WIN_FIELDS = "id payoutAmount entryCount resultAt bestTier winningNumber player { id } entry { totalCost }"


def parse_id(raw: str):
    """A Chance ID from an ID or a chance.fun link, e.g. 'multiwin-12'. None if there isn't one."""
    match = _ID.search(raw or "")
    return match.group(0) if match else None


def _tx_of(result_id: str):
    """Subgraph event ids are the transaction hash followed by the log index."""
    return result_id[:66] if re.fullmatch(r"0x[0-9a-fA-F]{64}[0-9a-fA-F]*", result_id or "") else None


def _paid_more_than_cost(row: dict, prize_token: str, entry_token: str, entry_price: int) -> bool:
    """True when a result paid more than its purchase cost (free entries cost nothing). Entries
    can be paid in a different token from the prize; those two compare in dollars."""
    try:
        payout = int(row.get("payoutAmount") or 0)
        cost = (row.get("entry") or {}).get("totalCost")
        cost = int(cost) if cost is not None else int(row.get("entryCount") or 1) * entry_price
    except (TypeError, ValueError):
        return False
    if prize_token == entry_token:
        return payout > cost
    return cd.to_usd_micro(payout, prize_token) > cd.to_usd_micro(cost, entry_token)


async def load(cid: str) -> dict:
    query = (f'{{ prize(id: "{cid}") {{ {PRIZE_FIELDS} }} '
             f'entryResults(first: {WINS_CHECKED}, orderBy: resultAt, orderDirection: desc, '
             f'where: {{ prize: "{cid}", won: true }}) {{ {WIN_FIELDS} }} }}')
    data = await cd.graphql(API_URL, query)
    if data is None:
        return {"ok": False, "code": "upstream", "error": "Can't reach chance.fun right now."}
    prize = data.get("prize")
    if not prize:
        return {"ok": False, "code": "not_found", "error": f"No Chance called {cid}."}

    token = (prize.get("prizeToken") or "").lower()
    entry_token = (prize.get("entryToken") or token).lower()  # entries can be paid in another token
    await cd.ensure_tokens(API_URL, [token, entry_token])
    try:
        entry_price = int(prize.get("entryPrice") or 0)
    except (TypeError, ValueError):
        entry_price = 0

    def amount(raw, coin=token):
        return cd.fmt_number(cd.token_amount(raw, coin))

    wins = []
    for row in data.get("entryResults") or []:
        if len(wins) == WINS_SHOWN:
            break
        if not _paid_more_than_cost(row, token, entry_token, entry_price):
            continue
        tx = _tx_of(row.get("id"))
        wins.append({
            "id": row.get("id"),
            "player": (row.get("player") or {}).get("id") or "",
            "payout": amount(row.get("payoutAmount")),
            "tier": row.get("bestTier"),
            "number": row.get("winningNumber"),
            "at": int(row.get("resultAt") or 0),
            "verify": f"{cd.APP_URL}/verify?tx={tx}" if tx else cd.game_url(cid),
        })
    return {
        "ok": True,
        "now": int(time.time()),
        "chance": {
            "id": prize.get("id"),
            "type": prize.get("prizeType"),
            "url": cd.game_url(prize.get("id")),
            "token": cd.token_symbol(token),
            "prize": amount(prize.get("prizeAmount")),
            "entryPrice": amount(prize.get("entryPrice"), entry_token),
            "entryToken": cd.token_symbol(entry_token),
            "poolLeft": amount(prize.get("remainingPrize")),
            "tiers": [amount(prize.get(f"tier{i}Prize")) for i in range(1, 5)],
            "range": int(prize.get("numberRange") or 0),
            "endTime": int(prize.get("endTime") or 0),
            "status": prize.get("status") or "",
            "hasWinner": bool(prize.get("hasWinner")),
            "winner": prize.get("winner") or "",
            "entries": int(prize.get("entriesSold") or 0),
            "maxEntries": int(prize.get("maxEntries") or 0),
        },
        "wins": wins,
    }


async def chance_api(request: web.Request) -> web.Response:
    cid = parse_id(request.query.get("id", ""))
    if not cid:
        return web.json_response({"ok": False, "code": "bad_id",
                                  "error": "Give a Chance ID like multiwin-12, or its chance.fun link."}, status=400)
    hit = _cache.get(cid)
    if not hit or time.time() - hit[0] >= CACHE_SECONDS:
        async with _locks.setdefault(cid, asyncio.Lock()):
            hit = _cache.get(cid)
            if not hit or time.time() - hit[0] >= CACHE_SECONDS:
                hit = (time.time(), await load(cid))
                _cache[cid] = hit
                if len(_cache) > 500:  # forget Chances nobody has asked about for a minute
                    for key, (at, _) in list(_cache.items()):
                        if time.time() - at > 60:
                            _cache.pop(key, None)
    payload = hit[1]
    status = 200 if payload.get("ok") else (404 if payload.get("code") == "not_found" else 502)
    return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})


async def overlay_page(request: web.Request) -> web.Response:
    return web.FileResponse(OVERLAY_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def to_overlay(request: web.Request):
    raise web.HTTPFound("/overlay/" + (f"?{request.query_string}" if request.query_string else ""))


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", to_overlay)
    app.router.add_get("/overlay", to_overlay)
    app.router.add_get("/overlay/", overlay_page)
    app.router.add_get("/api/chance", chance_api)
    app.router.add_get("/health", health)
    app.router.add_static("/overlay/assets", OVERLAY_DIR / "assets")
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8787")))
