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

import asyncio
import os
import time
import aiohttp

# Live platform data (Robinhood Chain mainnet, chain ID 4663) - the same
# endpoint chance.fun itself reads from.
NEW_SUBGRAPH_URL = "https://chance.fun/api/subgraph"

# Data sources that are testnet/dev only and must never be used in production.
_NON_LIVE_MARKERS = (
    "chance-lottery-testnet",               # retired lottery subgraph
    "project_cmjboofbdidyj01x8bi8t0xia",    # Goldsky testnet (chain 46630) prizes subgraph
    "dev.chance.fun",
)


def resolve_api_url() -> str:
    """Use CHANCE_API_URL unless it points at a testnet/dev data source."""
    url = os.getenv("CHANCE_API_URL", "").strip()
    if not url:
        return NEW_SUBGRAPH_URL
    if any(marker in url for marker in _NON_LIVE_MARKERS):
        print("⚠️ CHANCE_API_URL points to TESTNET/DEV data - using the live chance.fun data instead. "
              "Update CHANCE_API_URL in Railway to silence this warning.")
        return NEW_SUBGRAPH_URL
    return url


def as_graphql_body(body) -> dict:
    """
    Normalise a response to the standard GraphQL shape {"data": ..., "errors": ...}.
    Goldsky wraps results in "data"; the chance.fun proxy returns them unwrapped.
    """
    if not isinstance(body, dict):
        return {"errors": [{"message": "Invalid response from subgraph"}]}
    if "data" in body or "errors" in body:
        return body
    return {"data": body}


def is_ok_status(status: int) -> bool:
    """The chance.fun proxy answers 201; Goldsky answers 200."""
    return 200 <= status < 300


# --- Tokens (Robinhood Chain mainnet) -----------------------------------------

RPC_URL = os.getenv("CHAIN_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")

USDG = os.getenv("TOKEN_USDG", "0x5fc5360d0400a0fd4f2af552add042d716f1d168").lower()

TOKEN_SYMBOLS = {
    USDG: "USDG",
    "0x020bfc650a365f8bb26819deaabf3e21291018b4": "CASHCAT",
    "0x2e8c31162b855a2ffa90f6f8634643ad6f111e18": "AI",
    "0x39dbed3a2bd333467115de45665cc57f813c4571": "PONS",
    "0xd9db30bb0d2b8d2eae3826a1372117e058791e18": "MOO",
}

# ERC-20 decimals (read from the chain). Unknown tokens are looked up on refresh.
TOKEN_DECIMALS = {
    USDG: 6,
    "0x020bfc650a365f8bb26819deaabf3e21291018b4": 18,
    "0x2e8c31162b855a2ffa90f6f8634643ad6f111e18": 18,
    "0x39dbed3a2bd333467115de45665cc57f813c4571": 18,
    "0xd9db30bb0d2b8d2eae3826a1372117e058791e18": 18,
}

# Raw units worth $1 as set by the platform admin (NOT a market price).
# Only used internally for thresholds/ranking - never shown to users.
# Refreshed from the subgraph; these are the fallbacks.
_usd_units = {
    USDG: 10 ** 6,
    "0x020bfc650a365f8bb26819deaabf3e21291018b4": 10_000_000_000_000_000_000,
    "0x2e8c31162b855a2ffa90f6f8634643ad6f111e18": 8_333_333_333_333_333_333,
    "0x39dbed3a2bd333467115de45665cc57f813c4571": 2_857_142_857_142_857_142,
    "0xd9db30bb0d2b8d2eae3826a1372117e058791e18": 125_000_000_000_000_000_000,
}
_usd_units_loaded_at = 0.0
_USD_UNITS_TTL = 600  # seconds

USD_MICRO = 1_000_000

APP_URL = os.getenv("CHANCE_APP_URL", "https://chance.fun").rstrip("/")


def game_url(prize_id: str = None) -> str:
    """Link to a prize page on the app (same format as the new-Chance announcements)."""
    return f"{APP_URL}/lobby/game/{prize_id}" if prize_id else APP_URL


def token_symbol(token: str) -> str:
    return TOKEN_SYMBOLS.get((token or "").lower(), "tokens")


def token_decimals(token: str) -> int:
    return TOKEN_DECIMALS.get((token or "").lower(), 18)


def token_amount(raw, token: str) -> float:
    """Raw on-chain amount -> amount of that coin (e.g. 2300e18 CASHCAT -> 2300.0)."""
    try:
        return int(raw or 0) / 10 ** token_decimals(token)
    except (TypeError, ValueError):
        return 0.0


def fmt_number(x: float, compact: bool = False) -> str:
    if compact and x >= 1_000_000:
        return f"{x / 1_000_000:.2f}M"
    if compact and x >= 1_000:
        return f"{x / 1_000:.1f}K"
    if x == 0 or x >= 1:
        return f"{x:,.2f}"
    return f"{x:.4f}".rstrip("0").rstrip(".")


def fmt_token(raw, token: str, compact: bool = False) -> str:
    """Show an amount in the coin it was played with, e.g. '2,300.00 CASHCAT'."""
    return f"{fmt_number(token_amount(raw, token), compact)} {token_symbol(token)}"


def add_to_totals(totals: dict, raw, token: str) -> dict:
    """Accumulate raw amounts per coin (different coins can't be added together)."""
    token = (token or "").lower()
    try:
        totals[token] = totals.get(token, 0) + int(raw or 0)
    except (TypeError, ValueError):
        pass
    return totals


def fmt_totals(totals: dict, compact: bool = False, sep: str = " · ", empty: str = "0.00 USDG") -> str:
    """Per-coin breakdown, largest first, e.g. '500.00 USDG · 1,964.00 CASHCAT'."""
    items = sorted(((t, v) for t, v in (totals or {}).items() if v),
                   key=lambda tv: to_usd_micro(tv[1], tv[0]), reverse=True)
    return sep.join(fmt_token(v, t, compact) for t, v in items) or empty


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


def paid_more_than_cost(result: dict, prize: dict) -> bool:
    """True when an EntryResult paid more than its purchase cost (free entries cost nothing).

    The subgraph marks every tier hit as won, including a Multi Win Tier 1 that pays back less
    than the entry, and a loss is a loss. Entries can be paid in a different token from the
    prize; those two compare in dollars, so load both tokens first (ensure_tokens).
    Needs payoutAmount, entryCount and entry { totalCost } on the result, and prizeToken,
    entryToken and entryPrice on the prize."""
    prize_token = (prize.get("prizeToken") or "").lower()
    entry_token = (prize.get("entryToken") or prize_token).lower()
    try:
        payout = int(result.get("payoutAmount") or 0)
        cost = (result.get("entry") or {}).get("totalCost")
        if cost is None:
            cost = int(result.get("entryCount") or 1) * int(prize.get("entryPrice") or 0)
        cost = int(cost)
    except (TypeError, ValueError):
        return False
    if prize_token == entry_token:
        return payout > cost
    return to_usd_micro(payout, prize_token) > to_usd_micro(cost, entry_token)


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
                if not is_ok_status(resp.status):
                    print(f"⚠️ Subgraph HTTP {resp.status}")
                    return None
                body = as_graphql_body(await resp.json(content_type=None))
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
    # Newly whitelisted tokens: read their symbol and decimals from the chain
    for token in list(_usd_units):
        if token and token not in TOKEN_SYMBOLS:
            await _load_token_info(token)


async def _load_token_info(token: str) -> bool:
    """Read a token's symbol() and decimals() from the chain. True on success."""
    symbol = await _eth_call(token, "0x95d89b41")      # symbol()
    decimals = await _eth_call(token, "0x313ce567")    # decimals()
    if not (symbol and decimals):
        return False
    try:
        raw = bytes.fromhex(symbol[2:])
        length = int.from_bytes(raw[32:64], "big")
        TOKEN_SYMBOLS[token] = raw[64:64 + length].decode("utf-8", "ignore").strip("\x00")
        TOKEN_DECIMALS[token] = int(decimals, 16)
        print(f"🪙 New coin loaded: {TOKEN_SYMBOLS[token]} ({token}, {TOKEN_DECIMALS[token]} decimals)")
        return True
    except ValueError as e:
        print(f"⚠️ Could not decode token info for {token}: {e}")
        return False


async def ensure_tokens(api_url: str, tokens):
    """Make sure every token is known (value, symbol, decimals) before formatting it.
    Reloads immediately if a token was newly whitelisted, instead of waiting for the cache,
    and retries a symbol lookup that failed earlier."""
    tokens = {(t or "").lower() for t in tokens if t}
    await refresh_token_units(api_url, force=bool(tokens - set(_usd_units)))
    for token in tokens:
        if token in _usd_units and token not in TOKEN_SYMBOLS:
            await _load_token_info(token)


async def _eth_call(to: str, data: str):
    """Read-only contract call via the Robinhood Chain RPC. Returns hex result or None."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": to, "data": data}, "latest"]}
    for attempt in range(2):
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(RPC_URL, json=payload) as resp:
                    result = (await resp.json(content_type=None)).get("result")
            return result if result and result != "0x" else None
        except Exception as e:
            print(f"⚠️ Chain RPC call failed for {to} (attempt {attempt + 1}/2): {type(e).__name__} {e}")
            if attempt == 0:
                await asyncio.sleep(1)
    return None


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
        # Raw on-chain amounts in the prize's own coin - use these for display
        "prizeAmountRaw": p.get("prizeAmount") or "0",
        "ticketPriceRaw": p.get("entryPrice") or "0",
        "grossRevenueRaw": p.get("grossRevenue") or "0",
        "remainingPrizeRaw": p.get("remainingPrize") or "0",
        "tiersRaw": [p.get(f"tier{i}Prize") or "0" for i in range(1, 5)],
    }


def hit_to_legacy(h: dict) -> dict:
    """Convert a Multi Win winning EntryResult into a 'winner record' in the old shape."""
    prize = h.get("prize") or {}
    token = prize.get("prizeToken") or ""
    return {
        "id": prize.get("id"),
        "prizeType": "multiwin",
        "prizeToken": token,
        "tokenSymbol": token_symbol(token),
        "prizeAmountRaw": h.get("payoutAmount") or "0",
        "ticketPriceRaw": prize.get("entryPrice") or "0",
        "grossRevenueRaw": "0",
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
    # Skip sold-out prizes - chance.fun doesn't list them as available
    return [prize_to_legacy(p) for p in data.get("prizes", []) if not is_sold_out(p)]


def is_sold_out(p: dict) -> bool:
    max_entries = int(p.get("maxEntries") or 0)
    return max_entries > 0 and int(p.get("entriesSold") or 0) >= max_entries


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
        t = totals.setdefault(pid, {"id": pid, "usd": 0, "hits": 0, "by_token": {}})
        t["usd"] += to_usd_micro(r.get("winnings"), r.get("token"))
        t["hits"] += int(r.get("winCount") or 0)
        add_to_totals(t["by_token"], r.get("winnings"), r.get("token"))
    key = (lambda x: x["hits"]) if order == "hits" else (lambda x: x["usd"])
    ranked = sorted((t for t in totals.values() if t["usd"] > 0 or t["hits"] > 0),
                    key=key, reverse=True)[:top]
    return [{"id": t["id"], "totalWinnings": str(t["usd"]), "winCount": str(t["hits"]),
             "winningsByToken": t["by_token"]}
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
    volume_by_token, awarded_by_token = {}, {}
    for t in data.get("tokenStats_collection", []):
        volume += to_usd_micro(t.get("totalGrossRevenue"), t.get("token"))
        paid += to_usd_micro(t.get("totalPrizesAwarded"), t.get("token"))
        add_to_totals(volume_by_token, t.get("totalGrossRevenue"), t.get("token"))
        add_to_totals(awarded_by_token, t.get("totalPrizesAwarded"), t.get("token"))
    return {
        "total_prizes": int(g.get("totalPrizes") or 0),
        "active_prizes": int(g.get("activePrizes") or 0),
        "ended_prizes": int(g.get("endedPrizes") or 0),
        "total_entries": int(g.get("totalEntriesSold") or 0),
        "total_players": int(g.get("totalPlayers") or 0),
        "total_volume_usd": volume / USD_MICRO,
        "total_awarded_usd": paid / USD_MICRO,
        "volume_by_token": volume_by_token,
        "awarded_by_token": awarded_by_token,
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


async def fetch_player_winnings_by_token(api_url: str, player: str) -> dict:
    """One player's lifetime winnings per coin: {token: raw amount}."""
    await refresh_token_units(api_url)
    data = await graphql(api_url, f"""
    {{ playerTokenStats_collection(first: 100, where: {{ player: "{(player or '').lower()}" }}) {{ token winnings }} }}""")
    totals = {}
    for r in (data or {}).get("playerTokenStats_collection", []):
        add_to_totals(totals, r.get("winnings"), r.get("token"))
    return totals
