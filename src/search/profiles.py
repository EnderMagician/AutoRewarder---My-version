"""Per-account search profiles: themed, related queries instead of random ones.

A random sample from a 10,000-line list reads like nobody in particular: a
1970s film, then a Kenyan university, then a League champion build. Each
account instead gets one *search profile* (assets/search_profiles.json): a
handful of interests a single person could plausibly have, such as a game, a
film series, a kind of manga and one everyday subject. Accounts get different
profiles, so their histories don't look alike.

A run is built the way people actually search:

* it moves in short streaks of 1-3 related queries about one thing ("how to
  beat malenia elden ring" → "malenia weakness elden ring"),
* each run leans toward some interests more than others, and phones lean
  toward the everyday questions,
* queries the account searched recently are held back while fresher ones are
  left,
* a few searches each run come from the general list, because nobody only
  searches their hobbies.

Everything here is pure and takes an optional ``random.Random``.
"""

import json
import random
from datetime import date, datetime, timedelta

# Stored in an account's meta.json instead of a profile id.
AUTO_PROFILE = "auto"  # assign a profile no other account is using
RANDOM_PROFILE = "random"  # the old behavior: random sample of queries.json

# Share of a run that comes from the general query list.
GENERAL_MIX_SHARE = (0.05, 0.15)

# How many queries in a row stay on one thing, and how likely the next streak
# stays on the same interest.
STREAK_LENGTHS = (1, 2, 3)
STREAK_WEIGHTS = (35, 40, 25)
STAY_ON_INTEREST = 0.35

# Phones get more of the everyday questions.
MOBILE_GENERAL_WEIGHT = 1.6

# Days a query is held back after being searched. When a profile runs short,
# the window shrinks until there is enough to search.
RECENT_WINDOWS = (10, 5, 2, 0)

_HISTORY_DATE_FORMAT = "%m-%d-%Y"  # HistoryManager's record format

_cache: dict[str, list[dict]] = {}


def load_profiles(path):
    """
    Load and validate the search profiles file. Never raises.

    Returns:
        list[dict]: valid profiles, in file order; [] if the file is missing
        or malformed.
    """
    if path in _cache:
        return _cache[path]
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        profiles = [p for p in data.get("profiles", []) if _is_valid_profile(p)]
    except (OSError, ValueError, AttributeError, TypeError):
        profiles = []
    _cache[path] = profiles
    return profiles


def _is_valid_profile(profile):
    if not isinstance(profile, dict) or not profile.get("id"):
        return False
    interests = profile.get("interests")
    if not isinstance(interests, list) or not interests:
        return False
    for interest in interests:
        if not isinstance(interest, dict) or not interest.get("name"):
            return False
        if not isinstance(interest.get("queries", []), list):
            return False
        for topic in interest.get("topics", []):
            if not isinstance(topic.get("items"), list):
                return False
            if not all("{}" in t for t in topic.get("templates", [])):
                return False
    return True


def find_profile(profiles, profile_id):
    """Return the profile with `profile_id`, or None."""
    for profile in profiles:
        if profile["id"] == profile_id:
            return profile
    return None


def profile_summary(profile):
    """'Elden Ring · The Lord of the Rings · …' for labels and logs."""
    return " · ".join(interest["name"] for interest in profile["interests"])


def describe_profiles(profiles):
    """Return [{id, name, summary, interests}] for the Settings UI."""
    return [
        {
            "id": p["id"],
            "name": p.get("name", p["id"]),
            "summary": profile_summary(p),
            "interests": [i["name"] for i in p["interests"]],
        }
        for p in profiles
    ]


def choose_profile(profiles, taken, rng=None):
    """
    Pick a profile for an account that has none yet.

    Args:
        profiles (list[dict]): available profiles.
        taken (list[str]): profile ids other accounts already use; repeats
            count, so with more accounts than profiles the least used wins.

    Returns:
        str | None: a profile id, or None if there are no profiles.
    """
    rng = rng or random
    if not profiles:
        return None
    usage = {p["id"]: 0 for p in profiles}
    for profile_id in taken:
        if profile_id in usage:
            usage[profile_id] += 1
    fewest = min(usage.values())
    return rng.choice([pid for pid, used in usage.items() if used == fewest])


def recent_queries(history, days, today=None):
    """
    Return the lowercased queries searched within the last `days` days.

    Args:
        history (list[dict]): HistoryManager records ({date, query, ...}).
        days (int): window size; 0 returns an empty set.
    """
    if days <= 0 or not history:
        return set()
    today = today or date.today()
    cutoff = today - timedelta(days=days)
    recent = set()
    for record in history:
        try:
            searched = datetime.strptime(record["date"], _HISTORY_DATE_FORMAT).date()
            query = str(record["query"]).strip().lower()
        except (KeyError, TypeError, ValueError):
            continue
        if searched > cutoff:
            recent.add(query)
    return recent


def _interest_clusters(interest):
    """
    Group an interest's queries into clusters about one thing.

    Every item of a topic gives one cluster (that item through each
    template); each loose query is a cluster of its own.
    """
    clusters = []
    for topic in interest.get("topics", []):
        templates = topic.get("templates", [])
        for item in topic.get("items", []):
            clusters.append([template.replace("{}", item) for template in templates])
    clusters.extend([query] for query in interest.get("queries", []))
    return clusters


def _available_clusters(profile, recent):
    """Per interest, the clusters still holding queries not in `recent`."""
    available = []
    for interest in profile["interests"]:
        clusters = []
        for cluster in _interest_clusters(interest):
            fresh = [q for q in cluster if q.lower() not in recent]
            if fresh:
                clusters.append(fresh)
        available.append(clusters)
    return available


def _interest_weights(profile, mobile, rng):
    """This run's leaning: every interest gets a random share, phones favor everyday ones."""
    weights = []
    for interest in profile["interests"]:
        weight = rng.uniform(0.5, 1.6)
        if mobile and interest.get("kind") == "general":
            weight *= MOBILE_GENERAL_WEIGHT
        weights.append(weight)
    return weights


def build_profile_streaks(
    profile, count, mobile=False, history=None, rng=None, today=None
):
    """
    Build up to `count` related queries from `profile` for one phase.

    Args:
        profile (dict): a profile from load_profiles.
        count (int): how many queries are wanted.
        mobile (bool): whether this is the Mobile phase.
        history (list[dict] | None): this account's search history.
        rng (random.Random | None): randomness source, for tests.

    Returns:
        list[list[str]]: streaks of related queries, in search order, with
        `count` distinct queries in all. Fewer only when the whole profile
        holds fewer queries.
    """
    rng = rng or random
    if count <= 0:
        return []

    available = None
    for window in RECENT_WINDOWS:
        available = _available_clusters(profile, recent_queries(history, window, today))
        if sum(len(c) for clusters in available for c in clusters) >= count:
            break

    weights = _interest_weights(profile, mobile, rng)
    streaks = []
    seen = set()
    current = None

    while len(seen) < count:
        open_interests = [i for i, clusters in enumerate(available) if clusters]
        if not open_interests:
            break
        if current not in open_interests or rng.random() >= STAY_ON_INTEREST:
            current = rng.choices(
                open_interests, weights=[weights[i] for i in open_interests]
            )[0]

        clusters = available[current]
        cluster = clusters.pop(rng.randrange(len(clusters)))
        rng.shuffle(cluster)
        length = rng.choices(STREAK_LENGTHS, weights=STREAK_WEIGHTS)[0]
        streak = []
        for query in cluster[:length]:
            key = query.lower()
            if key in seen or len(seen) >= count:
                continue
            seen.add(key)
            streak.append(query)
        if streak:
            streaks.append(streak)

    return streaks


def general_mix_count(count, rng=None):
    """How many of `count` searches should come from the general query list."""
    rng = rng or random
    if count < 5:
        return 0
    share = rng.uniform(*GENERAL_MIX_SHARE)
    return max(1, round(count * share))


def blend_general_queries(streaks, general_queries, rng=None):
    """
    Slot general-list queries between a profile run's streaks.

    A general query never lands in the middle of a streak, so related
    searches about one thing stay together.

    Returns:
        list[str]: the flat search order.
    """
    rng = rng or random
    blocks = [list(streak) for streak in streaks]
    for query in general_queries:
        blocks.insert(rng.randint(0, len(blocks)), [query])
    return [query for block in blocks for query in block]
