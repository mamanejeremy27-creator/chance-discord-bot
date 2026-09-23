"""
================================================================================
CHANCE DATA LAYER
================================================================================
Reads the Chance "prizes" subgraph (Robinhood Chain) and hands the rest of the
bot clean data.

Why this file exists:
- The old subgraph had `lotteries` in one token (USDC, 6 decimals).
- The new subgraph has `prizes` of two game types (instant / multiwin) paid in
  more than one token (USDG = 6 decimals, CHANCE = 18 decimals).
- Every token has a `usdUnit` (raw units worth $1) in `whitelistedTokens`.

All money values returned by the `*_legacy` helpers are "USD micro-units"
(1 USD = 1_000_000), so existing code that does `int(x) / 1_000_000` keeps
showing correct dollar values for every token.
================================================================================
"""

import os
import time
import aiohttp

NEW_SUBGRAPH_URL = (
    "https://api.goldsky.com/api/public/project_cmjboofbdidyj01x8bi8t0xia"
    "/subgraphs/chance-prizes/stable/gn"
)


def resolve_api_url() -> str:
    """Use CHANCE_API_URL unless it still points at the retired lottery subgraph."""
    url = os.getenv("CHANCE_API_URL", "").strip()
    if not url:
        return NEW_SUBGRAPH_URL
    if "chance-lottery-testnet" in url:
        print("⚠️ CHANCE_API_URL points to the OLD lottery subgraph - using the new prizes subgraph instead. "
              "Update CHANCE_API_URL in Railway to silence this warning.")
        return NEW_SUBGRAPH_URL
    return url


# --- Tokens -------------------------------------------------------------------

USDG = os.getenv("TOKEN_USDG", "0x20bb04a48498707a4563f9fb8075672ad200c69c").lower()
CHANCE = os.getenv("TOKEN_CHANCE", "0x331eda897b9f01a0ade4cefeb4a10300e4095b2b").lower()

TOKEN_SYMBOLS = {USDG: "USDG", CHANCE: "CHANCE"}

# Raw units worth $1. Refreshed from the subgraph; these are the fallbacks.
_usd_units = {USDG: 10 ** 6, CHANCE: 10 ** 20}
_usd_units_loaded_at = 0.0
_USD_UNITS_TTL = 600  # seconds

USD_MICRO = 1_000_000

APP_URL = os.getenv("CHANCE_APP_URL", "https://chance.fun").rstrip("/")


def game_url(prize_id: str = None) -> str:
    """Link to a prize page on the app (same format as the lottery monitor)."""
    return f"{APP_URL}/lobby/game/{prize_id}" if prize_id else APP_URL


def token_symbol(token: str) -> str:
    return TOKEN_SYMBOLS.get((token or "").lower(), "tokens")


def usd_unit(token: str) -> int:
    return _usd_units.get((token or "").lower(), 10 ** 6)


def to_usd_micro(raw, token: str) -> int:
    """Convert a raw on-chain amount to USD micro-units (1 USD = 1_000_000)."""
    try:
        raw = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    return raw * USD_MICRO // usd_unit(token)


def to_usd(raw, token: str) -> float:
    return to_usd_micro(raw, token) / USD_MICRO


# --- GraphQL ------------------------------------------------------------------

async def graphql(api_url: str, query: str, variables: dict = None):
    """Run a query and return the `data` object, or None on any failure."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, json=payload,
                                    headers={"Content-Type": "application/json"}) as resp:
                if resp.status != 200:
                    print(f"⚠️ Subgraph HTTP {resp.status}")
                    return None
                body = await resp.json()
    except Exception as e:
        print(f"⚠️ Subgraph request failed: {e}")
        return None
    if body.get("errors"):
        print(f"⚠️ Subgraph error: {body['errors'][0].get('message', body['errors'])}")
        return None
    return body.get("data") or {}


async def refresh_token_units(api_url: str, force: bool = False):
    """Load usdUnit for every whitelisted token (cached for 10 minutes)."""
    global _usd_units_loaded_at
    if not force and time.time() - _usd_units_loaded_at < _USD_UNITS_TTL:
        return
    data = await graphql(api_url, "{ whitelistedTokens(first: 100) { token usdUnit active } }")
    if data is None:
        return
    for row in data.get("whitelistedTokens", []):
        try:
            unit = int(row.get("usdUnit") or 0)
        except (TypeError, ValueError):
            continue
        if unit > 0:
            _usd_units[(row.get("token") or "").lower()] = unit
    _usd_units_loaded_at = time.time()


async def fetch_paginated(api_url: str, entity: str, fields: str, where: str = "",
                          max_items: int = 5000) -> list:
    """Fetch up to max_items rows of an entity, paging by id."""
    rows, last_id = [], ""
    while len(rows) < max_items:
        where_clause = f'id_gt: "{last_id}"' + (f", {where}" if where else "")
        query = (f"{{ items: {entity}(first: 1000, orderBy: id, orderDirection: asc, "
                 f"where: {{ {where_clause} }}) {{ id {fields} }} }}")
        data = await graphql(api_url, query)
        if data is None:
            return rows if rows else None
        batch = data.get("items", [])
        rows.extend(batch)
        if len(batch) < 1000:
            break
        last_id = batch[-1]["id"]
    return rows[:max_items]


# --- Legacy-shaped data (what the older bot.py code expects) ------------------

PRIZE_FIELDS = """
    prizeType prizeToken prizeAmount entryPrice numberRange endTime status
    hasWinner winner entriesSold maxEntries grossRevenue createdAt prizeProvider
    remainingPrize tier1Prize tier2Prize tier3Prize tier4Prize
"""

HIT_FIELDS = """
    payoutAmount resultAt bestTier player { id }
    prize { id prizeType prizeToken numberRange entryPrice }
"""

_STATUS_MAP = {"ACTIVE": "ACTIVE", "ENDED": "COMPLETED", "EXPIRED": "EXPIRED"}


def prize_to_legacy(p: dict) -> dict:
    """Convert a new `Prize` into the old lottery dict shape, amounts in USD micro."""
    token = p.get("prizeToken") or ""
    is_instant = p.get("prizeType") == "instant"
    return {
        "id": p.get("id"),
        "prizeType": p.get("prizeType"),
        "prizeToken": token,
        "tokenSymbol": token_symbol(token),
        "prizeAmount": str(to_usd_micro(p.get("prizeAmount"), token)),
        "ticketPrice": str(to_usd_micro(p.get("entryPrice"), token)),
        # Only Instant Win has "1 in N" odds. Multi Win uses tiers, so 0 = not applicable.
        "pickRange": str(int(p.get("numberRange") or 0) if is_instant else 0),
        "endTime": p.get("endTime") or "0",
        "status": _STATUS_MAP.get(p.get("status"), p.get("status") or ""),
        "hasWinner": bool(p.get("hasWinner")),
        "winner": (p.get("winner") or ""),
        "ticketsSold": p.get("entriesSold") or "0",
        "maxTickets": p.get("maxEntries") or "0",
        "grossRevenue": str(to_usd_micro(p.get("grossRevenue"), token)),
        "createdAt": p.get("createdAt") or "0",
        "prizeProvider": (p.get("prizeProvider") or ""),
        "remainingPrize": str(to_usd_micro(p.get("remainingPrize"), token)),
        "tiers": [to_usd(p.get(f"tier{i}Prize"), token) for i in range(1, 5)],
    }


def hit_to_legacy(h: dict) -> dict:
    """Convert a Multi Win winning EntryResult into a 'winner record' in the old shape."""
    prize = h.get("prize") or {}
    token = prize.get("prizeToken") or ""
    return {
        "id": prize.get("id"),
        "prizeType": "multiwin",
        "prizeToken": token,
        "prizeAmount": str(to_usd_micro(h.get("payoutAmount"), token)),
        "ticketPrice": str(to_usd_micro(prize.get("entryPrice"), token)),
        "pickRange": "0",
        "hasWinner": True,
        "winner": ((h.get("player") or {}).get("id") or ""),
        "status": "HIT",
        "ticketsSold": "0",
        "grossRevenue": "0",
        "prizeProvider": "",   # empty so creator rankings skip hit records
        "createdAt": h.get("resultAt") or "0",
        "bestTier": h.get("bestTier"),
        "is_hit": True,
    }


async def fetch_all_prizes_legacy(api_url: str) -> list:
    await refresh_token_units(api_url)
    rows = await fetch_paginated(api_url, "prizes", PRIZE_FIELDS)
    if rows is None:
        return None
    return sorted((prize_to_legacy(p) for p in rows),
                  key=lambda x: int(x["createdAt"]), reverse=True)


async def fetch_multiwin_hits_legacy(api_url: str, since: int = None) -> list:
    await refresh_token_units(api_url)
    where = 'won: true, prize_: { prizeType: multiwin }'
    if since:
        where += f', resultAt_gte: "{int(since)}"'
    rows = await fetch_paginated(api_url, "entryResults", HIT_FIELDS, where=where)
    if rows is None:
        return None
    return [hit_to_legacy(h) for h in rows]


async def fetch_active_prizes_legacy(api_url: str, first: int = 100) -> list:
    """Active prizes, soonest ending first (for ending-soon alerts)."""
    await refresh_token_units(api_url)
    now = int(time.time())
    query = f"""
    {{ prizes(first: {int(first)}, orderBy: endTime, orderDirection: asc,
              where: {{ status: ACTIVE, endTime_gt: "{now}" }}) {{ id {PRIZE_FIELDS} }} }}
    """
    data = await graphql(api_url, query)
    if data is None:
        return None
    return [prize_to_legacy(p) for p in data.get("prizes", [])]


async def fetch_daily_stats_legacy(api_url: str, since: int) -> dict:
    """Same keys the old daily-stats query returned, plus Multi Win hits."""
    prizes = await fetch_all_prizes_legacy(api_url)
    if prizes is None:
        return None
    hits = await fetch_multiwin_hits_legacy(api_url) or []
    instant_winners = [p for p in prizes if p["hasWinner"]]
    return {
        "todayLotteries": [p for p in prizes if int(p["createdAt"]) >= since],
        "allWinners": instant_winners + hits,
        "allLotteries": prizes,
        "todayHits": [h for h in hits if int(h["createdAt"]) >= since],
    }


async def fetch_leaderboard_legacy(api_url: str) -> list:
    """All prizes plus one record per Multi Win hit (so winner rankings include Multi Win)."""
    prizes = await fetch_all_prizes_legacy(api_url)
    if prizes is None:
        return None
    hits = await fetch_multiwin_hits_legacy(api_url) or []
    return prizes + hits


async def fetch_wallet_legacy(api_url: str, wallet: str) -> dict:
    """{'created': prizes this wallet created, 'won': every win (Instant + Multi Win hits)}."""
    await refresh_token_units(api_url)
    wallet = wallet.lower()
    created = await fetch_paginated(api_url, "prizes", PRIZE_FIELDS,
                                    where=f'prizeProvider: "{wallet}"')
    wins = await fetch_paginated(api_url, "entryResults", HIT_FIELDS,
                                 where=f'won: true, player: "{wallet}"')
    if created is None or wins is None:
        return None
    won = []
    for h in wins:
        rec = hit_to_legacy(h)
        prize = h.get("prize") or {}
        if prize.get("prizeType") == "instant":
            rec["prizeType"] = "instant"
            rec["pickRange"] = str(int(prize.get("numberRange") or 0))
        won.append(rec)
    return {"created": [prize_to_legacy(p) for p in created], "won": won}


async def fetch_player_leaderboard(api_url: str, order: str = "winnings", top: int = 10) -> list:
    """
    Top players with winnings converted to USD across all tokens.
    Returns [{'id', 'totalWinnings' (USD micro, str), 'winCount' (str)}].
    order: 'winnings' or 'hits'
    """
    await refresh_token_units(api_url)
    rows = await fetch_paginated(api_url, "playerTokenStats_collection",
                                 "token winnings winCount player { id }")
    if rows is None:
        return None
    totals = {}
    for r in rows:
        pid = ((r.get("player") or {}).get("id") or "").lower()
        if not pid:
            continue
        t = totals.setdefault(pid, {"id": pid, "usd": 0, "hits": 0})
        t["usd"] += to_usd_micro(r.get("winnings"), r.get("token"))
        t["hits"] += int(r.get("winCount") or 0)
    key = (lambda x: x["hits"]) if order == "hits" else (lambda x: x["usd"])
    ranked = sorted((t for t in totals.values() if t["usd"] > 0 or t["hits"] > 0),
                    key=key, reverse=True)[:top]
    return [{"id": t["id"], "totalWinnings": str(t["usd"]), "winCount": str(t["hits"])}
            for t in ranked]


async def fetch_platform_totals(api_url: str) -> dict:
    """Platform-wide totals in USD, summed correctly per token."""
    await refresh_token_units(api_url)
    data = await graphql(api_url, """
    {
      globalStats(id: "global") { totalPrizes activePrizes endedPrizes totalEntriesSold totalPlayers }
      tokenStats_collection(first: 100) { token totalGrossRevenue totalPrizesAwarded totalPrizesPaid }
    }""")
    if data is None:
        return None
    g = data.get("globalStats") or {}
    volume = paid = 0
    for t in data.get("tokenStats_collection", []):
        volume += to_usd_micro(t.get("totalGrossRevenue"), t.get("token"))
        paid += to_usd_micro(t.get("totalPrizesAwarded"), t.get("token"))
    return {
        "total_prizes": int(g.get("totalPrizes") or 0),
        "active_prizes": int(g.get("activePrizes") or 0),
        "ended_prizes": int(g.get("endedPrizes") or 0),
        "total_entries": int(g.get("totalEntriesSold") or 0),
        "total_players": int(g.get("totalPlayers") or 0),
        "total_volume_usd": volume / USD_MICRO,
        "total_awarded_usd": paid / USD_MICRO,
    }


async def fetch_player_usd_winnings(api_url: str, player: str) -> float:
    """One player's total winnings in USD, summed correctly across tokens."""
    await refresh_token_units(api_url)
    data = await graphql(api_url, f"""
    {{ playerTokenStats_collection(first: 100, where: {{ player: "{(player or '').lower()}" }}) {{ token winnings }} }}""")
    if data is None:
        return 0.0
    return sum(to_usd(r.get("winnings"), r.get("token"))
               for r in data.get("playerTokenStats_collection", []))
