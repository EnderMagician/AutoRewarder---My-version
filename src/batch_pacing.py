"""Human-like pacing for the multi-account "Run all accounts" batch.

A batch used to run every account back to back in the same order with the
same search counts, PC first, with half a second between browser sessions.
Nobody uses their accounts like that. This module plans a batch the way a
person would actually get through it:

* the account order changes from run to run,
* each account gets a pause of several minutes before it starts,
* the search counts move a little from day to day (never below the configured
  value, so no points are lost),
* PC and Mobile sessions swap order on some runs, with a short pause between
  them, as if switching to a different device.

Everything here is pure and takes an optional ``random.Random`` so tests can
pin the outcome. The API layer does the sleeping and the running.
"""

import math
import random
from dataclasses import dataclass

# Same caps as the search count inputs and AccountMetaManager.
MAX_PC_QUERIES = 130
MAX_MOBILE_QUERIES = 99

# Bounds for the settings, mirrored by the Settings modal.
MAX_ACCOUNT_GAP_MINUTES = 240.0
MAX_PHASE_GAP_SECONDS = 1800.0
MAX_COUNT_VARIANCE_PERCENT = 50

DEFAULT_BATCH_PACING = {
    # Master switch. Off restores the old fixed-order, back-to-back batch.
    "enabled": True,
    # Run the accounts in a different order each batch.
    "shuffle_accounts": True,
    # Pause before each account after the first, in minutes.
    "account_gap_min": 4.0,
    "account_gap_max": 15.0,
    # Extra searches added on top of each account's count, up to this percent.
    "count_variance": 15,
    # Run Mobile before PC on roughly half the accounts.
    "randomize_phase_order": True,
    # Pause between the PC and Mobile sessions of one account, in seconds.
    "phase_gap_min": 30.0,
    "phase_gap_max": 180.0,
}


def default_batch_pacing():
    """Return a fresh copy of the default pacing settings."""
    return dict(DEFAULT_BATCH_PACING)


def _bounded_float(value, default, upper):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number < 0 or number > upper:
        return default
    return number


def _bounded_range(raw, min_key, max_key, upper):
    low = _bounded_float(raw.get(min_key), DEFAULT_BATCH_PACING[min_key], upper)
    high = _bounded_float(raw.get(max_key), DEFAULT_BATCH_PACING[max_key], upper)
    if low > high:
        return DEFAULT_BATCH_PACING[min_key], DEFAULT_BATCH_PACING[max_key]
    return low, high


def normalize_batch_pacing(raw):
    """
    Return a valid pacing dict, replacing anything malformed with defaults.

    Args:
        raw (dict | None): stored or user-supplied pacing values.

    Returns:
        dict: every key of DEFAULT_BATCH_PACING, validated.
    """
    raw = raw if isinstance(raw, dict) else {}
    pacing = default_batch_pacing()

    for key in ("enabled", "shuffle_accounts", "randomize_phase_order"):
        if key in raw:
            pacing[key] = bool(raw[key])

    pacing["account_gap_min"], pacing["account_gap_max"] = _bounded_range(
        raw, "account_gap_min", "account_gap_max", MAX_ACCOUNT_GAP_MINUTES
    )
    pacing["phase_gap_min"], pacing["phase_gap_max"] = _bounded_range(
        raw, "phase_gap_min", "phase_gap_max", MAX_PHASE_GAP_SECONDS
    )

    try:
        variance = int(raw.get("count_variance", pacing["count_variance"]))
    except (TypeError, ValueError):
        variance = DEFAULT_BATCH_PACING["count_variance"]
    if not 0 <= variance <= MAX_COUNT_VARIANCE_PERCENT:
        variance = DEFAULT_BATCH_PACING["count_variance"]
    pacing["count_variance"] = variance

    return pacing


def validate_batch_pacing(raw):
    """
    Return the pacing dict when every supplied value is valid, else None.

    Unlike normalize_batch_pacing, this never swaps in a default, so a bad
    value typed in Settings is rejected instead of saved as something else.
    """
    if not isinstance(raw, dict):
        return None
    normalized = normalize_batch_pacing(raw)
    for key, value in raw.items():
        if key not in DEFAULT_BATCH_PACING:
            continue
        if isinstance(DEFAULT_BATCH_PACING[key], bool):
            continue
        try:
            supplied = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isclose(supplied, float(normalized[key])):
            return None
    return normalized


def sample_pause(low, high, rng=None):
    """
    Pick a pause between `low` and `high`, skewed toward the shorter end.

    Real pauses aren't uniform: most are short, and a few run long. A
    Beta(2, 4) draw keeps the result inside the range with its peak around
    the first fifth and a tail toward `high`.
    """
    rng = rng or random
    low = max(0.0, float(low))
    high = max(low, float(high))
    if high == low:
        return low
    return low + (high - low) * rng.betavariate(2, 4)


def vary_count(base, variance_percent, ceiling, rng=None):
    """
    Return `base` plus up to `variance_percent` percent more, capped at `ceiling`.

    The count never drops below `base`, so the account still reaches the
    target the user set, and a phase set to 0 stays off.
    """
    rng = rng or random
    base = max(0, int(base))
    if base == 0 or variance_percent <= 0:
        return min(base, ceiling)
    extra = rng.randint(0, math.ceil(base * variance_percent / 100))
    return min(ceiling, base + extra)


@dataclass
class AccountPlan:
    """How one account runs inside a batch."""

    account: dict
    pc_count: int
    mobile_count: int
    # Seconds to wait before this account starts (0 for the first one).
    gap_before: float
    mobile_first: bool


def plan_batch(accounts, counts, pacing, rng=None):
    """
    Decide order, counts, phase order and waits for every account in a batch.

    Args:
        accounts (list[dict]): runnable accounts, in their listed order.
        counts (dict): account id -> (pc_count, mobile_count) as configured.
        pacing (dict): normalized pacing settings.
        rng (random.Random | None): randomness source, for tests.

    Returns:
        list[AccountPlan]: accounts in the order they should run.
    """
    rng = rng or random
    pacing = normalize_batch_pacing(pacing)
    ordered = list(accounts)

    if not pacing["enabled"]:
        return [
            AccountPlan(
                account=account,
                pc_count=counts[account["id"]][0],
                mobile_count=counts[account["id"]][1],
                gap_before=0.0,
                mobile_first=False,
            )
            for account in ordered
        ]

    if pacing["shuffle_accounts"]:
        rng.shuffle(ordered)

    plans = []
    for index, account in enumerate(ordered):
        pc, mobile = counts[account["id"]]
        gap = 0.0
        if index > 0:
            gap = 60.0 * sample_pause(
                pacing["account_gap_min"], pacing["account_gap_max"], rng
            )
        plans.append(
            AccountPlan(
                account=account,
                pc_count=vary_count(pc, pacing["count_variance"], MAX_PC_QUERIES, rng),
                mobile_count=vary_count(
                    mobile, pacing["count_variance"], MAX_MOBILE_QUERIES, rng
                ),
                gap_before=gap,
                mobile_first=bool(
                    pacing["randomize_phase_order"] and rng.random() < 0.5
                ),
            )
        )
    return plans


def format_duration(seconds):
    """Format seconds as '45s', '7m 05s' or '1h 02m' for log lines."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
