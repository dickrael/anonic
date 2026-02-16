"""Level system — 1000+ levels computed from total messages (sent + received).

200 tiers across 20 thematic eras, each with 5 sub-levels (I–V) = 1000 base
levels.  Tier 201 ("Transcendent") repeats I–V infinitely for 1001+.
"""

TIERS = [
    # ── Era 1 · Dawn (1–50) ──
    "Newcomer", "Curious", "Wanderer", "Seeker", "Scout",
    "Novice", "Pilgrim", "Drifter", "Roamer", "Wayfarer",

    # ── Era 2 · Rise (51–100) ──
    "Whisperer", "Explorer", "Pathfinder", "Confidant", "Companion",
    "Voyager", "Trailblazer", "Pioneer", "Adventurer", "Ranger",

    # ── Era 3 · Depth (101–150) ──
    "Shadow", "Mystic", "Phantom", "Specter", "Shade",
    "Lurker", "Stalker", "Nightwalker", "Duskweaver", "Gloom",

    # ── Era 4 · Wisdom (151–200) ──
    "Oracle", "Visionary", "Prophet", "Sage", "Enlightened",
    "Scholar", "Philosopher", "Mentor", "Luminary", "Savant",

    # ── Era 5 · Mystery (201–250) ──
    "Enigma", "Cipher", "Riddle", "Puzzle", "Paradox",
    "Mirage", "Illusion", "Labyrinth", "Cryptic", "Arcanum",

    # ── Era 6 · Power (251–300) ──
    "Sentinel", "Guardian", "Warden", "Protector", "Champion",
    "Enforcer", "Vanguard", "Bastion", "Bulwark", "Titan",

    # ── Era 7 · Dark (301–350) ──
    "Wraith", "Revenant", "Duskborn", "Apparition", "Banshee",
    "Lich", "Ghoul", "Harbinger", "Dread", "Netherbane",

    # ── Era 8 · Ascension (351–400) ──
    "Sovereign", "Eclipse", "Immortal", "Legend", "Eternal",
    "Ascendant", "Paragon", "Apex", "Zenith", "Pinnacle",

    # ── Era 9 · Elemental (401–450) ──
    "Ember", "Tempest", "Torrent", "Glacier", "Inferno",
    "Cyclone", "Avalanche", "Tsunami", "Magma", "Thunder",

    # ── Era 10 · Celestial (451–500) ──
    "Starborn", "Nebula", "Comet", "Pulsar", "Quasar",
    "Nova", "Solaris", "Lunar", "Cosmos", "Astral",

    # ── Era 11 · Spirit (501–550) ──
    "Wisp", "Wrathling", "Poltergeist", "Eidolon", "Seraph",
    "Djinn", "Nephilim", "Sylph", "Nymph", "Reverie",

    # ── Era 12 · Myth (551–600) ──
    "Griffin", "Phoenix", "Hydra", "Leviathan", "Chimera",
    "Basilisk", "Wyvern", "Kraken", "Behemoth", "Cerberus",

    # ── Era 13 · Arcane (601–650) ──
    "Sorcerer", "Warlock", "Enchanter", "Conjurer", "Alchemist",
    "Runekeeper", "Spellbinder", "Hexweaver", "Thaumaturge", "Invoker",

    # ── Era 14 · Rogue (651–700) ──
    "Rogue", "Outlaw", "Shadowstep", "Saboteur", "Assassin",
    "Marauder", "Corsair", "Smuggler", "Mercenary", "Bounty",

    # ── Era 15 · Nature (701–750) ──
    "Bloom", "Thornwood", "Wildheart", "Verdant", "Rootwalker",
    "Beastcaller", "Stormleaf", "Mossgrave", "Fernwhisper", "Briar",

    # ── Era 16 · Void (751–800) ──
    "Void", "Hollow", "Oblivion", "Abyss", "Nullborn",
    "Entropy", "Riftwalker", "Voidtouched", "Nether", "Darkrift",

    # ── Era 17 · Iron (801–850) ──
    "Ironclad", "Steelheart", "Forgeborn", "Anvil", "Warbringer",
    "Siegebreaker", "Battleforged", "Hammerfall", "Shieldwall", "Warmonger",

    # ── Era 18 · Crown (851–900) ──
    "Noble", "Monarch", "Emperor", "Overlord", "Warlord",
    "Archon", "Regent", "Tyrant", "Conqueror", "Dynasty",

    # ── Era 19 · Ethereal (901–950) ──
    "Dreamwalker", "Mistveil", "Twilight", "Ethereal", "Phantasm",
    "Sleepless", "Somnium", "Lullaby", "Spearsoul", "Whisperwind",

    # ── Era 20 · Omega (951–1000) ──
    "Mythic", "Godlike", "Primordial", "Infinite", "Absolute",
    "Omniscient", "Celestial", "Timeless", "Boundless", "Omega",

    # ── ∞ (1001+) ──
    "Transcendent",
]

ROMAN = ["I", "II", "III", "IV", "V"]
_SUB_COUNT = len(ROMAN)  # 5


def xp_for_level(n: int) -> int:
    """Cumulative XP (total messages) needed to reach level n. Level 1 = 0."""
    if n <= 1:
        return 0
    return (n - 1) ** 2


def get_level(xp: int) -> tuple[int, str]:
    """Return (level_number, title_str) for a given XP total.

    Examples: (1, "Newcomer I"), (1000, "Omega V"), (1001, "Transcendent I").
    """
    level = 1
    while xp >= xp_for_level(level + 1):
        level += 1
    tier_idx = (level - 1) // _SUB_COUNT
    sub = (level - 1) % _SUB_COUNT
    if tier_idx >= len(TIERS):
        tier_name = TIERS[-1]  # Transcendent
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
