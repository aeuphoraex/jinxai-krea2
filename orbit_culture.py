"""Pop-culture classification layer for the gallery.

Every card carries a career plus four classification axes:

  pop     the cultural archetype the card reads as (hyperpop idol, y2k mall-goth, ...)
  music   the sound of the persona
  art     the artistry / style lineage the render imitates
  film    the film quality, which the page applies as a front-layer CSS effect

There is exactly one character underneath all eight careers: the commander, VELA — the blonde
with pink dreadlocked twintails. The eight shipped cards are her phases, not different people.
Dynamics between phases (a pilot plus a navigator, a courier plus a dancer) recruit new
characters into factions; every recruit inherits the twintails and one trait from each parent.

Pure Python on purpose: importable by the app, testable without a deploy.
"""

from __future__ import annotations

# ---------------------------------------------------------------- taxonomy

POP = {
    "hyperpop": {"label": "hyperpop idol", "family": "neon"},
    "y2k": {"label": "y2k mall-goth", "family": "neon"},
    "cyber": {"label": "cyber-goth command", "family": "chrome"},
    "industrial": {"label": "industrial punk", "family": "chrome"},
    "cabaret": {"label": "cabaret noir", "family": "velvet"},
    "trip": {"label": "trip-hop diva", "family": "velvet"},
    "bedroom": {"label": "bedroom-pop soft", "family": "soft"},
    "citypop": {"label": "city-pop summer", "family": "sunline"},
    # Groups opened after the Second Life roster: women who were never an SL avatar.
    "void": {"label": "void-pop hymn", "family": "hollow"},
    "pastoral": {"label": "machine pastoral", "family": "orchard"},
    "tidal": {"label": "tidal noir", "family": "salt"},
    "command": {"label": "command chrome", "family": "vela_command"},
}

MUSIC = {
    "hyperpop": "hyperpop sprite",
    "trip": "trip-hop chanteuse",
    "industrial": "industrial siren",
    "shoegaze": "shoegaze drift",
    "cabaret": "dark cabaret",
    "citypop": "city-pop crooner",
    "lofi": "lo-fi bedroom pop",
    "electro": "electro-industrial",
    "hymn": "choral drone",
    "orch": "organic techno",
    "tide": "tidal dub",
}

ART = {
    "chrome": "chrome airbrush",
    "nebula": "nebula bloom",
    "brutal": "brutalist signage",
    "rain": "wet-plate rain",
    "silver": "silver-gelatin glamour",
    "sunfade": "sun-faded airbrush",
    "riso": "riso print warmth",
    "crtvec": "CRT vector",
    "ash": "ash-and-gold leaf",
    "loam": "loam and brass",
    "salt": "salt-bloom cyanotype",
}

# css is the class the gallery page applies to the card's image as a front-layer effect.
FILM = {
    "neon": {"label": "anamorphic neon haze", "css": "q-neon"},
    "halation": {"label": "halation soft-focus", "css": "q-halation"},
    "grain": {"label": "16mm grain", "css": "q-grain"},
    "vhs": {"label": "VHS scanline", "css": "q-vhs"},
    "mono": {"label": "bleach-bypass mono", "css": "q-mono"},
    "golden": {"label": "golden-hour bloom", "css": "q-golden"},
    "super8": {"label": "super-8 warmth", "css": "q-super8"},
    "crt": {"label": "CRT chromatic split", "css": "q-crt"},
}

FACTIONS = {
    "vela_command": {"name": "VELA COMMAND",
                     "doctrine": "the commander's own group: the hyper-selection above the deck",
                     "line": "Opened for women with no Second Life body at all — the first non-SL"
                             " recruits, rendered from a prompt instead of a profile."},
    "neon": {"name": "NEON COURT", "doctrine": "maximum colour, maximum signal, no quiet hours",
             "line": "The deck's original signal: the SL roster at its loudest."},
    "chrome": {"name": "CHROME GUILD", "doctrine": "precision first; the deck is a machine and so is she",
               "line": "Engineering phases, where the roster was built rather than born."},
    "velvet": {"name": "VELVET DECK", "doctrine": "low light, slow tempo, everything on purpose",
               "line": "The night-deck phases — the ones the membership sells."},
    "soft": {"name": "SOFT SIGNAL", "doctrine": "warmth over wattage; small rooms, real skin",
             "line": "Domestic phases; the quiet half of the roster."},
    "sunline": {"name": "SUNLINE", "doctrine": "chase the last hour of daylight and shoot it",
                "line": "Rooftop phases, shot at the only hour that forgives anything."},
    "hollow": {"name": "HOLLOW CHOIR", "doctrine": "voices first, faces after; the unreleased takes",
               "line": "Non-SL recruits who arrived as sound before they arrived as bodies."},
    "orchard": {"name": "IRON ORCHARD", "doctrine": "grow it, machine it, feed the deck",
                "line": "Non-SL recruits from the station's grow ring — pastoral, not nautical."},
    "salt": {"name": "SALT GARDEN", "doctrine": "tide logic; nothing here stays where it was put",
             "line": "Non-SL recruits from the tidal deck: the roster's first waterborne group."},
}

# Group order on the gallery: the commander's group first, then the shipped deck, then the
# groups opened for non-SL women.
FACTION_ORDER = ("vela_command", "neon", "chrome", "velvet", "soft", "sunline",
                 "hollow", "orchard", "salt")

# Master groups. The women inside a faction are close enough in style that a wall of cards
# reads as one look, so each master is compressed to a SINGLE master image (a contact sheet
# of its members) and the faction groups nest inside it.
MASTERS = {
    "neon-line": {"name": "NEON LINE", "groups": ("vela_command", "neon"),
                  "doctrine": "the commander's own line: maximum colour, maximum signal",
                  "line": "VELA COMMAND and NEON COURT — the same blonde, the loudest shifts, "
                          "compressed into one sheet."},
    "chrome-line": {"name": "CHROME LINE", "groups": ("chrome",),
                    "doctrine": "precision, machines, cold light",
                    "line": "Engineering and dock shifts, where the deck was built rather than born."},
    "velvet-line": {"name": "VELVET LINE", "groups": ("velvet", "soft"),
                    "doctrine": "low light, slow tempo, real skin",
                    "line": "The night deck and the domestic half: what the membership actually sells."},
    "sun-line": {"name": "SUN LINE", "groups": ("sunline",),
                 "doctrine": "one hour of daylight, spent well",
                 "line": "Rooftop phases, shot at the only hour that forgives anything."},
    "off-world-line": {"name": "OFF-WORLD LINE", "groups": ("hollow", "orchard", "salt"),
                       "doctrine": "no Second Life body; rendered from a prompt",
                       "line": "The non-SL hyper-selection: choir, grow ring and tide deck."},
}
MASTER_ORDER = ("neon-line", "chrome-line", "velvet-line", "sun-line", "off-world-line")

COMMANDER = {
    "id": "vela",
    "name": "VELA",
    "epithet": "the Twintail Commander",
    "look": ("one face, eight careers: blonde hair in pink dreadlocked twintails with an extra high "
             "ponytail, blunt bangs, pointed elf ears, freckles, glossy pale skin, glitter bokeh, "
             "crystals and nebula sigils on the horizon"),
    "pop": "hyperpop",
    "music": "hyperpop",
    "art": "chrome",
    "film": "neon",
    "faction": "neon",
    "doctrine": "Every card is the same woman on a different shift. The twintails are the uniform.",
}

# ---------------------------------------------------------------- the phases

MEMBERS = {
    # The commander's own phase: this is the card the VELA COMMAND group previews with.
    "dw-kessa": {"career": "Dock Pilot", "phase": "the shift she never talks about",
                 "pop": "hyperpop", "music": "hyperpop", "art": "chrome", "film": "neon",
                 "family": "vela_command",
                 "duty": "flies the deck, opens the command", "axis": ["dw-nyra", "dw-vesper"]},
    "dw-nyra": {"career": "Night Navigator", "phase": "the one who reads the sky",
                "pop": "trip", "music": "trip", "art": "nebula", "film": "halation",
                "duty": "plots the route nobody else can see", "axis": ["dw-sable", "dw-kessa"]},
    "dw-orla": {"career": "Dockmaster", "phase": "the one who says no",
                "pop": "industrial", "music": "industrial", "art": "brutal", "film": "grain",
                "duty": "holds the dock, counts the cargo", "axis": ["dw-tamsin", "dw-mira"]},
    "dw-sable": {"career": "Courier", "phase": "the one who is already gone",
                 "pop": "y2k", "music": "shoegaze", "art": "rain", "film": "vhs",
                 "duty": "moves the package, never the story", "axis": ["dw-vesper", "dw-nyra"]},
    "dw-vesper": {"career": "Deck Dancer", "phase": "the one who performs the truth",
                  "pop": "cabaret", "music": "cabaret", "art": "silver", "film": "mono",
                  "duty": "runs the night deck and the room's mood", "axis": ["dw-sable", "dw-kessa"]},
    "dw-iris": {"career": "Rooftop Pilot", "phase": "the one who left first",
                "pop": "citypop", "music": "citypop", "art": "sunfade", "film": "golden",
                "duty": "scouts the ring city from above", "axis": ["dw-mira", "dw-tamsin"]},
    "dw-mira": {"career": "Galley Crew", "phase": "the one who keeps them fed",
                "pop": "bedroom", "music": "lofi", "art": "riso", "film": "super8",
                "duty": "runs the galley, hears every secret", "axis": ["dw-iris", "dw-orla"]},
    "dw-tamsin": {"career": "Signals Officer", "phase": "the one who hears everything",
                  "pop": "cyber", "music": "electro", "art": "crtvec", "film": "crt",
                  "duty": "owns the console, the logs and the silence", "axis": ["dw-orla", "dw-iris"]},
}

ERA = {"y2k": "y2k", "velvet90s": "velvet 90s", "chrome80s": "chrome 80s",
       "hyper2016": "2016 hyperpop", "sun70s": "sun-bleached 70s",
       "soft10s": "soft 2010s", "crt90s": "crt 90s"}

MEDIUM = {"filmstill": "film still", "cdinsert": "cd insert", "magcover": "magazine cover",
          "cdrom": "cd-rom manual", "tradingcard": "trading card", "poster": "poster",
          "vhs": "vhs sleeve", "zine": "photo zine"}

ARCHETYPE = {"idol": "idol", "antiidol": "anti-idol", "muse": "muse", "rival": "rival",
             "mentor": "mentor", "wildcard": "wildcard", "strategist": "strategist",
             "caretaker": "caretaker"}

# Third axis set: era/scene, the medium the card imitates, and the fandom archetype.
AXES = {
    "dw-kessa": {"era": "y2k", "medium": "filmstill", "archetype": "idol"},
    "dw-nyra": {"era": "velvet90s", "medium": "magcover", "archetype": "muse"},
    "dw-orla": {"era": "chrome80s", "medium": "tradingcard", "archetype": "mentor"},
    "dw-sable": {"era": "y2k", "medium": "zine", "archetype": "wildcard"},
    "dw-vesper": {"era": "velvet90s", "medium": "poster", "archetype": "antiidol"},
    "dw-iris": {"era": "sun70s", "medium": "filmstill", "archetype": "rival"},
    "dw-mira": {"era": "soft10s", "medium": "zine", "archetype": "caretaker"},
    "dw-tamsin": {"era": "crt90s", "medium": "cdrom", "archetype": "strategist"},
}
COMMANDER_AXES = {"era": "hyper2016", "medium": "magcover", "archetype": "idol"}

for _sku, _axes in AXES.items():
    MEMBERS[_sku].update(_axes)

# Tag fallback so distilled characters inherit the nearest phase's culture.
TAG_TO_PHASE = {"pilot": "dw-kessa", "navigator": "dw-nyra", "dockmaster": "dw-orla",
                "courier": "dw-sable", "dancer": "dw-vesper", "crew": "dw-mira",
                "officer": "dw-tamsin", "engineer": "dw-orla", "medic": "dw-mira",
                "scavenger": "dw-sable", "aristocrat": "dw-vesper"}

# ---------------------------------------------------------------- dynamics → recruits

RECIPES = [
    {"pair": ("dw-kessa", "dw-nyra"), "name": "Aster Vane", "career": "Wing Navigator",
     "pop": "hyperpop", "music": "trip", "art": "nebula", "film": "neon",
     "gift": "reads a route in flight, which is not supposed to be possible"},
    {"pair": ("dw-orla", "dw-tamsin"), "name": "Maren Quill", "career": "Dock Signals",
     "pop": "industrial", "music": "electro", "art": "crtvec", "film": "grain",
     "gift": "hears a container lie over the radio"},
    {"pair": ("dw-sable", "dw-vesper"), "name": "Nyx Sway", "career": "Night Courier Cabaret",
     "pop": "cabaret", "music": "cabaret", "art": "rain", "film": "mono",
     "gift": "delivers in costume and never breaks character"},
    {"pair": ("dw-mira", "dw-iris"), "name": "Juno Halo", "career": "Rooftop Galley",
     "pop": "citypop", "music": "lofi", "art": "sunfade", "film": "golden",
     "gift": "feeds a whole shift from a rooftop burner"},
    {"pair": ("dw-kessa", "dw-vesper"), "name": "Rue Kite", "career": "Flight Deck Cabaret",
     "pop": "hyperpop", "music": "cabaret", "art": "chrome", "film": "mono",
     "gift": "briefs a crew like a stage manager"},
    {"pair": ("dw-nyra", "dw-sable"), "name": "Cinder Pace", "career": "Vector Courier",
     "pop": "y2k", "music": "shoegaze", "art": "rain", "film": "vhs",
     "gift": "runs a route that only exists between two chart readings"},
    {"pair": ("dw-orla", "dw-mira"), "name": "Bracken Moss", "career": "Galley Dockmaster",
     "pop": "bedroom", "music": "lofi", "art": "riso", "film": "super8",
     "gift": "runs the dock on soup and stubbornness"},
    {"pair": ("dw-iris", "dw-tamsin"), "name": "Sera Rook", "career": "Signal Pilot",
     "pop": "cyber", "music": "electro", "art": "crtvec", "film": "crt",
     "gift": "flies the signal instead of the ship"},
    # Recruits for the groups opened after the SL roster — the hyper-selection.
    {"pair": ("dw-vesper", "dw-nyra"), "name": "Odile Vow", "career": "Choir Navigator",
     "pop": "void", "music": "hymn", "art": "ash", "film": "mono",
     "gift": "sings the route before anyone has flown it"},
    {"pair": ("dw-mira", "dw-orla"), "name": "Loam Tess", "career": "Orchard Engineer",
     "pop": "pastoral", "music": "orch", "art": "loam", "film": "grain",
     "gift": "grows a season inside one shift"},
    {"pair": ("dw-iris", "dw-sable"), "name": "Marée Sable", "career": "Tidal Courier",
     "pop": "tidal", "music": "tide", "art": "salt", "film": "halation",
     "gift": "swims a package through the tide deck"},
    {"pair": ("dw-kessa", "dw-tamsin"), "name": "Vela-9", "career": "Command Ghost",
     "pop": "command", "music": "electro", "art": "chrome", "film": "crt",
     "gift": "answers on the command channel before she is called"},
]


def groups() -> list[dict]:
    """The gallery's groups, in display order, each with its own preview card.

    One group per faction: VELA COMMAND first (the commander's own), then the shipped deck,
    then the groups opened for women who never had a Second Life body. `preview` is the sku
    whose 1K frame the collapsed group shows, so a minimised gallery is still identifiable.
    """
    rows = []
    for key in FACTION_ORDER:
        faction = FACTIONS[key]
        members = [sku for sku, m in MEMBERS.items()
                   if (m.get("family") or POP[m["pop"]]["family"]) == key]
        rows.append({
            "key": key,
            "name": faction["name"],
            "doctrine": faction["doctrine"],
            "line": faction.get("line", ""),
            "count": len(members),
            "preview": members[0] if members else "",
            "members": members,
        })
    return rows


def _blend(a: dict, b: dict) -> dict:
    """A recruit's classification: pop/art from one parent, music/film from the other."""
    return {"pop": a["pop"], "music": b["music"], "art": a["art"], "film": b["film"]}


def for_sku(sku: str, tag: str = "") -> dict | None:
    """Classification block for one card, or None when it cannot be placed."""
    member = MEMBERS.get(sku)
    if member is None:
        member = MEMBERS.get(TAG_TO_PHASE.get(str(tag or "").lower(), ""))
    if member is None:
        return None
    family = member.get("family") or POP[member["pop"]]["family"]
    return {
        "career": member.get("career", ""),
        "phase": member.get("phase", ""),
        "duty": member.get("duty", ""),
        "pop": POP[member["pop"]]["label"],
        "music": MUSIC.get(member["music"], member["music"]),
        "art": ART.get(member["art"], member["art"]),
        "film": dict(FILM[member["film"]], key=member["film"]),
        "era": ERA.get(member.get("era", ""), member.get("era", "")),
        "medium": MEDIUM.get(member.get("medium", ""), member.get("medium", "")),
        "archetype": ARCHETYPE.get(member.get("archetype", ""), member.get("archetype", "")),
        "faction": dict(FACTIONS[family], key=family),
        "commander": COMMANDER["name"],
    }


def recruits() -> list:
    out = []
    for recipe in RECIPES:
        a, b = (MEMBERS[s] for s in recipe["pair"])
        cls = _blend(a, b)
        family = POP[cls["pop"]]["family"]
        out.append({
            "name": recipe["name"],
            "career": recipe["career"],
            "gift": recipe["gift"],
            "parents": list(recipe["pair"]),
            "pop": POP[cls["pop"]]["label"],
            "music": MUSIC.get(cls["music"], cls["music"]),
            "art": ART.get(cls["art"], cls["art"]),
            "film": FILM[cls["film"]]["label"],
            "era": ERA.get(a.get("era", ""), ""),
            "medium": MEDIUM.get(b.get("medium", ""), ""),
            "archetype": ARCHETYPE.get(a.get("archetype", ""), ""),
            "faction": FACTIONS[family]["name"],
        })
    return out


def masters() -> list[dict]:
    """The master groups: several factions compressed into one image, in display order.

    `members` is what the backend composites into the single master image; `groups` is what
    the gallery nests inside it. A master with no members yet still exists, so the group is
    visible as open/recruiting instead of disappearing.
    """
    by_key = {g["key"]: g for g in groups()}
    out = []
    for key in MASTER_ORDER:
        master = MASTERS[key]
        members = [sku for gk in master["groups"]
                   for sku in (by_key.get(gk) or {}).get("members", [])]
        out.append({
            "key": key,
            "name": master["name"],
            "doctrine": master["doctrine"],
            "line": master["line"],
            "groups": list(master["groups"]),
            "members": members,
            "count": len(members),
        })
    return out


def relations() -> list:
    out = []
    for sku, member in MEMBERS.items():
        for other in member.get("axis", []):
            if sku < other:
                out.append({"a": sku, "b": other,
                            "note": f"{member['career']} × {MEMBERS[other]['career']}"})
    return out


def command() -> dict:
    members = []
    for sku, member in MEMBERS.items():
        block = for_sku(sku) or {}
        members.append({"sku": sku, "career": member["career"], "phase": member["phase"],
                        "duty": member["duty"], "faction": block.get("faction", {}).get("name", ""),
                        "pop": block.get("pop", ""), "music": block.get("music", ""),
                        "art": block.get("art", ""), "film": block.get("film", {})})
    return {
        "commander": {**COMMANDER, **{k: (ERA if k == "era" else MEDIUM if k == "medium" else ARCHETYPE)[v]
                                    for k, v in COMMANDER_AXES.items()},
                      "pop": POP[COMMANDER["pop"]]["label"],
                      "music": MUSIC[COMMANDER["music"]], "art": ART[COMMANDER["art"]],
                      "film": FILM[COMMANDER["film"]]},
        "faction": FACTIONS[COMMANDER["faction"]],
        "members": members,
        "groups": groups(),
        "masters": masters(),
        "relations": relations(),
        "recruits": recruits(),
        "taxonomy": {
            "pop": {k: v["label"] for k, v in POP.items()},
            "music": MUSIC, "art": ART,
            "film": {k: v["label"] for k, v in FILM.items()},
            "era": ERA, "medium": MEDIUM, "archetype": ARCHETYPE,
        },
    }
