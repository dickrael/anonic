"""Level system — 210+ levels computed from total messages (sent + received)."""

TIERS = [
    "Newbie", "Curious", "Whisperer", "Explorer", "Confidant",
    "Shadow", "Mystic", "Phantom", "Specter", "Oracle",
    "Enigma", "Sentinel", "Cipher", "Wraith", "Sage",
    "Nexus", "Sovereign", "Eclipse", "Immortal", "Legend",
    "Transcendent",
]

ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def xp_for_level(n: int) -> int:
    """Cumulative XP (total messages) needed to reach level n. Level 1 = 0."""
    if n <= 1:
        return 0
    return (n - 1) ** 2


def get_level(xp: int) -> tuple[int, str]:
    """Return (level_number, title_str) for a given XP total.

    Examples: (1, "Newbie I"), (42, "Confidant II"), (211, "Transcendent I").
    """
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
    tier_idx = (level - 1) // 10
    sub = (level - 1) % 10
    if tier_idx >= len(TIERS):
        tier_name = TIERS[-1]
    else:
        tier_name = TIERS[tier_idx]
    roman = ROMAN[sub]
    return level, f"{tier_name} {roman}"


def get_level_progress(xp: int) -> dict:
    """Return level info dict with progress toward next level."""
    level, title = get_level(xp)
    current_threshold = xp_for_level(level)
    next_threshold = xp_for_level(level + 1)
    xp_in_level = xp - current_threshold
    xp_needed = next_threshold - current_threshold
    progress = xp_in_level / xp_needed if xp_needed > 0 else 0.0
    return {
        "level": level,
        "level_title": title,
        "level_progress": round(progress, 4),
        "xp": xp,
        "xp_in_level": xp_in_level,
        "xp_for_next": xp_needed,
    }
