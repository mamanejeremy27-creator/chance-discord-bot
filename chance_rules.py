"""
================================================================================
CHANCE RULES
================================================================================
Single source of truth for the platform rules used by every calculator,
the FAQ, /help and the tutorial.

Source: dev.chance.fun FAQ + the Create form's validation (read 2026-09-22).
If the platform changes a rule, change it HERE only.
================================================================================
"""

# --- Fees ---------------------------------------------------------------------
# (platform fee on settled paid entries, one-time deposit fee on the prize)
FEE_TIERS = {
    "standard": {"name": "Standard", "stake": 0,         "platform": 0.05,  "deposit": 0.01},
    "bronze":   {"name": "Bronze",   "stake": 50_000,    "platform": 0.045, "deposit": 0.0075},
    "silver":   {"name": "Silver",   "stake": 500_000,   "platform": 0.04,  "deposit": 0.005},
    "gold":     {"name": "Gold",     "stake": 2_500_000, "platform": 0.035, "deposit": 0.0035},
    "diamond":  {"name": "Diamond",  "stake": 5_000_000, "platform": 0.03,  "deposit": 0.0025},
}
STANDARD = FEE_TIERS["standard"]
PLATFORM_FEE = STANDARD["platform"]      # 5%
DEPOSIT_FEE = STANDARD["deposit"]        # 1%


def fees(tier: str = "standard") -> dict:
    return FEE_TIERS.get((tier or "standard").lower(), STANDARD)


# --- Shared limits ------------------------------------------------------------
MIN_PRIZE = 100          # USD (token dollar units)
MIN_ENTRY = 1            # USD
MAX_REFERRAL = 20        # percent, paid from the creator's share
MAX_ENTRIES_PER_PURCHASE = 100
MIN_DURATION_MIN = 1
MAX_DURATION_DAYS = 30
FREE_CREDIT_CAP = 0.20   # credits up to 20% of initial prize / entry price

# --- Instant Win --------------------------------------------------------------
IW_MIN_ODDS = 3                  # at least 1 in 3
IW_MIN_MULTIPLIER = 2            # prize / entry  (entry <= half the prize)
IW_MAX_MULTIPLIER = 10_000
IW_MAX_RTP = 150                 # percent
IW_MAX_PURCHASE_SHARE = 0.40     # one purchase covers at most 40% of the range


def iw_min_rtp(prize: float) -> tuple:
    """Minimum RTP for an Instant Win prize (percent, tier label)."""
    if prize <= 10_000:
        return 70, "up to $10K"
    if prize <= 100_000:
        return 60, "$10K–$100K"
    return 50, "over $100K"


def iw_rtp(prize: float, entry: float, odds: int) -> float:
    if odds <= 0 or entry <= 0:
        return 0.0
    return prize / (odds * entry) * 100


def iw_issues(prize: float, entry: float, odds: int, referral: float = 0) -> list:
    """Every Instant Win creation rule these settings break (empty list = OK)."""
    issues = []
    if prize < MIN_PRIZE:
        issues.append(f"Prize must be at least ${MIN_PRIZE:,}")
    if entry < MIN_ENTRY:
        issues.append(f"Entry price must be at least ${MIN_ENTRY}")
    if entry > 0 and prize / entry < IW_MIN_MULTIPLIER:
        issues.append(f"Entry price must be at most half the prize (${prize / 2:,.2f})")
    if entry > 0 and prize / entry > IW_MAX_MULTIPLIER:
        issues.append(f"Prize can be at most {IW_MAX_MULTIPLIER:,}× the entry price")
    if odds < IW_MIN_ODDS:
        issues.append(f"Odds must be at least 1 in {IW_MIN_ODDS}")
    rtp = iw_rtp(prize, entry, odds)
    min_rtp, tier = iw_min_rtp(prize)
    if prize >= MIN_PRIZE and rtp < min_rtp:
        issues.append(f"RTP {rtp:.1f}% is below the {min_rtp}% minimum for prizes {tier}")
    if rtp > IW_MAX_RTP:
        issues.append(f"RTP {rtp:.1f}% is above the {IW_MAX_RTP}% maximum")
    if referral > MAX_REFERRAL:
        issues.append(f"Referral commission can be at most {MAX_REFERRAL}%")
    return issues


def iw_creator_profit(prize: float, entry: float, odds: int, referral: float = 0,
                      tier: str = "standard", entries: float = None) -> dict:
    """
    Creator economics for an Instant Win game.
    entries defaults to `odds` (the average number of entries until someone wins).
    """
    f = fees(tier)
    n = odds if entries is None else entries
    gross = n * entry
    platform = gross * f["platform"]
    ref = gross * referral / 100
    deposit = prize * f["deposit"]
    net = gross - platform - ref - prize - deposit
    return {"gross": gross, "platform_fee": platform, "referral": ref, "deposit_fee": deposit,
            "prize": prize, "net": net, "roi": net / (prize + deposit) * 100 if prize else 0.0}


def iw_breakeven_entries(prize: float, entry: float, referral: float = 0,
                         tier: str = "standard") -> float:
    """Entries needed to cover the prize plus the deposit fee."""
    f = fees(tier)
    per_entry = entry * (1 - f["platform"] - referral / 100)
    if per_entry <= 0:
        return float("inf")
    return (prize * (1 + f["deposit"])) / per_entry


# --- MultiWin -----------------------------------------------------------------
MW_MIN_RANGE = 1_000
MW_MAX_RANGE = 9_999
MW_MIN_RTP = 54                  # percent


# Tier probability constants used by the app and contract (scaled by 10,000):
# p(k matches) = (base_k * R + var_k * f(R)) / (R * 10_000), f(R) = 1000 if R >= 1999 else 999
_MW_BASE = (2430, 270, 10, 0)
_MW_VAR = (4860, 2160, 260, 10)


def mw_match_probabilities(number_range: int) -> tuple:
    """
    Probability that an entry matches exactly 1, 2, 3 or 4 digit POSITIONS
    of the winning number (numbers 1..number_range shown as 4 digits).
    Same formula as the Chance app. Returns (p1, p2, p3, p4).
    """
    R = int(number_range)
    f = 1000 if R >= 1999 else 999
    return tuple((b * R + v * f) / (R * 10_000) for b, v in zip(_MW_BASE, _MW_VAR))


def mw_rtp(number_range: int, multipliers) -> float:
    """RTP (%) for tier multipliers (1-match, 2-match, 3-match, 4-match), as × entry price."""
    probs = mw_match_probabilities(number_range)
    return sum(p * m for p, m in zip(probs, multipliers)) * 100


def mw_issues(number_range: int, entry: float, multipliers, prize: float = None,
              referral: float = 0) -> list:
    issues = []
    if not (MW_MIN_RANGE <= number_range <= MW_MAX_RANGE):
        issues.append(f"Number range must be {MW_MIN_RANGE:,}–{MW_MAX_RANGE:,}")
    if entry < MIN_ENTRY:
        issues.append(f"Entry price must be at least ${MIN_ENTRY}")
    if prize is not None and prize < MIN_PRIZE:
        issues.append(f"Prize pool must be at least ${MIN_PRIZE:,}")
    m = list(multipliers)
    if any(x <= 0 for x in m):
        issues.append("Every tier needs a payout above 0")
    if any(m[i] >= m[i + 1] for i in range(3)):
        issues.append("Each tier must pay more than the one below it (4 matches pays the most)")
    if MW_MIN_RANGE <= number_range <= MW_MAX_RANGE:
        rtp = mw_rtp(number_range, m)
        if rtp < MW_MIN_RTP:
            issues.append(f"RTP {rtp:.1f}% is below the {MW_MIN_RTP}% minimum")
    if prize is not None and entry > 0 and m and prize < m[-1] * entry:
        issues.append(f"Prize pool is smaller than the top payout (${m[-1] * entry:,.2f})")
    if referral > MAX_REFERRAL:
        issues.append(f"Referral commission can be at most {MAX_REFERRAL}%")
    return issues


def mw_creator_margin(number_range: int, entry: float, multipliers, referral: float = 0,
                      tier: str = "standard") -> dict:
    """Expected creator result per entry (revenue share minus expected payouts from the pool)."""
    f = fees(tier)
    rtp = mw_rtp(number_range, multipliers) / 100
    share = entry * (1 - f["platform"] - referral / 100)
    expected_payout = entry * rtp
    return {"share": share, "expected_payout": expected_payout, "margin": share - expected_payout,
            "margin_pct": (share - expected_payout) / entry * 100 if entry else 0.0}
