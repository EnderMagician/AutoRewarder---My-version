"""Shared utility helpers for AutoRewarder."""

import time
import random
import requests

from .config import CURRENT_VERSION, REPO

# Neighbouring keys on a QWERTY layout, for slips that get corrected.
_QWERTY_ROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm")


def _neighbour_key(char):
    """Return a key next to `char` on a QWERTY keyboard, or None."""
    lower = char.lower()
    for row_index, row in enumerate(_QWERTY_ROWS):
        col = row.find(lower)
        if col < 0:
            continue
        candidates = [row[c] for c in (col - 1, col + 1) if 0 <= c < len(row)]
        # Rows are staggered: the row above sits half a key to the left, so
        # "g" touches "t" and "y" above it and "v" and "b" below it.
        for other_index, cols in (
            (row_index - 1, (col, col + 1)),
            (row_index + 1, (col - 1, col)),
        ):
            if 0 <= other_index < len(_QWERTY_ROWS):
                other = _QWERTY_ROWS[other_index]
                candidates += [other[c] for c in cols if 0 <= c < len(other)]
        return random.choice(candidates) if candidates else None
    return None


def keystroke_delay(prev_char, char, tempo=1.0):
    """
    Seconds to wait before typing `char` after `prev_char`.

    People type in bursts: a lognormal gap around ~0.14 s per key, longer
    before the first letter of a word and after a space, shorter inside
    common pairs of letters, and now and then a hesitation.
    """
    delay = random.lognormvariate(-2.0, 0.35)  # median ~0.135 s
    if prev_char == " ":
        delay += random.uniform(0.05, 0.25)
    if char == " ":
        delay += random.uniform(0.02, 0.12)
    if prev_char and (prev_char + char).lower() in _FAST_PAIRS:
        delay *= 0.7
    if random.random() < 0.03:
        delay += random.uniform(0.3, 1.0)
    return delay * tempo


# Letter pairs typed fastest in English (alternating hands or rolls).
_FAST_PAIRS = {
    "th",
    "he",
    "in",
    "er",
    "an",
    "re",
    "on",
    "at",
    "en",
    "nd",
    "ti",
    "es",
    "or",
    "te",
    "of",
    "ed",
    "is",
    "it",
    "al",
    "ar",
}


def human_typing(element, text, tempo=1.0, slip_chance=0.025):
    """
    Type `text` into a web element the way a person does.

    Keystroke timing comes from keystroke_delay. With `slip_chance` per
    letter, a neighbouring key is hit first, noticed after a moment and
    erased with Backspace. These are typos that get fixed; the typos that
    stay in the query come from humanize_queries.

    Args:
        element: The web element to send keys to.
        text: The text to type into the element.
        tempo (float): multiplier on every delay; >1 is a slower typist.
        slip_chance (float): chance per letter of a corrected slip.
    """
    from selenium.webdriver.common.keys import Keys

    prev = ""
    for char in text:
        time.sleep(keystroke_delay(prev, char, tempo))
        if char.isalnum() and random.random() < slip_chance:
            wrong = _neighbour_key(char)
            if wrong:
                element.send_keys(wrong)
                # Noticing a slip takes longer than typing a key.
                time.sleep(random.uniform(0.15, 0.5) * tempo)
                element.send_keys(Keys.BACKSPACE)
                time.sleep(random.uniform(0.08, 0.25) * tempo)
        element.send_keys(char)
        prev = char


def humanize_queries(queries):
    """
    Substitute character by keyboard distance to simulate human typing errors.

    Args:
        queries (list): A list of query strings.
    Returns:
        humanized_queries (list): Modified query strings with simulated typing errors.
    """
    import nlpaug.augmenter.char as nac  # type: ignore

    mod = nac.KeyboardAug(
        # 100% probability because the 20% overall chance
        # handled outside by the `random` module
        aug_char_p=1.0,
        aug_word_p=1.0,
        # Only one typo per query
        aug_char_max=1,
        aug_word_max=1,
        # Ignore short words
        min_char=4,
        include_upper_case=False,
        include_special_char=False,
        include_numeric=True,
    )

    humanized_queries = []

    for query in queries:
        if random.random() < 0.2:
            modified_query = mod.augment(query)
            typo_query = (
                modified_query[0]
                if isinstance(modified_query, list)
                else modified_query
            )
            humanized_queries.append(typo_query)
        else:
            humanized_queries.append(query)

    return humanized_queries


def check_for_updates(logger=None):
    """
    Check GitHub API for the latest release and compare it to the current version.

    Args:
        logger (callable, optional): A function to log messages. Defaults to None.

    Returns:
        tuple: (is_update_available (bool), latest_version (str or None))
    """
    try:
        headers = {"User-Agent": "AutoRewarder-App"}

        response = requests.get(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers=headers,
            timeout=5,
        )
        if response.status_code == 200:
            latest = response.json().get("tag_name")
            if latest:
                return latest != CURRENT_VERSION, latest
        elif response.status_code == 429:
            if logger:
                logger("[WARNING] GitHub API rate limit reached (429).")
                logger("Try again later or check manually for updates.")
        elif response.status_code == 403:

            is_rate_limit = response.headers.get("X-Ratelimit-Remaining") == "0"

            if logger:
                if is_rate_limit:
                    logger(
                        "[WARNING] GitHub API rate limit exceeded (403). Try again later."
                    )
                else:
                    logger(
                        "[WARNING] GitHub access forbidden (403). Check your VPN or connection."
                    )
        else:
            if logger:
                logger(
                    f"[WARNING] GitHub update check failed. Status: {response.status_code}"
                )

    except requests.exceptions.RequestException as e:
        if logger:
            logger(f"[WARNING] Network error while checking for updates: {e}")
    except Exception as e:
        if logger:
            logger(f"[ERROR] Unexpected error while checking for updates: {e}")

    return False, None
