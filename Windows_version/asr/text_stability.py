from __future__ import annotations


def split_stable_text(prev: str, curr: str) -> tuple[str, str]:
    """Split a partial result into stable and unstable parts.

    The rule is intentionally conservative: take the longest common prefix
    between previous and current partials, then keep the last two characters
    unstable so the UI/input layer can tolerate ASR tail rewrites.
    """
    prev = prev or ""
    curr = curr or ""
    limit = min(len(prev), len(curr))
    index = 0
    while index < limit and prev[index] == curr[index]:
        index += 1
    stable_end = max(0, index - 2)
    return curr[:stable_end], curr[stable_end:]

