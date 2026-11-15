"""Velvet promoter for XZYMION — drives her in-world chat with on-brand spots.

She already has a brain and a chat path; this does not replace either. It is the *schedule*:
a small set of on-brand lines, rotated, rate-limited and logged, sent through the engine's own
`send_chat` tool so they are indistinguishable from anything else she says.

    python tools/velvet_promoter.py --dry-run          # what the next 8 spots look like
    python tools/velvet_promoter.py --once             # one spot, respects the gap
    python tools/velvet_promoter.py --once --force     # one spot now, ignore the gap
    python tools/velvet_promoter.py --loop --gap 900   # keep posting every 15 min
    python tools/velvet_promoter.py --selftest         # the rules, offline

Rules that are enforced here rather than trusted to a mood:

  * a minimum gap between spots (default 600 s) and a hard cap per hour (6);
  * the same category never follows itself, so a visitor does not get the price three times;
  * every line is short enough to read in Second Life's chat window and carries at most one
    link — the gallery domain, which is the only URL she is allowed to drop;
  * nothing about prices that the rail does not charge (L$100 test / L$250 picture / L$750
    video at 250 L$/USD), because a promoter who invents a price is a refund queue.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

ENGINE = os.environ.get("XZYMION_ENGINE", "http://127.0.0.1:12047").rstrip("/")
# Bare domain, not a URL: her wire guard refuses anything with a scheme before it is published
# (`blocked: ["network"]`), and a line that gets blocked is a spot nobody ever reads. Second Life
# chat does not hyperlink anyway, so `dubwarz.vip` is what a resident copies — the scheme adds
# nothing but a refusal. The full URL stays in SITE_URL for pages and docs, never in chat.
SITE = os.environ.get("VELVET_SITE", "dubwarz.vip")
SITE_URL = "https://dubwarz.vip/"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "out", "velvet_promoter.state.json")
LOG = os.path.join(HERE, "out", "velvet_promoter.log")

MIN_GAP = int(os.environ.get("VELVET_GAP_SECONDS", "600") or "600")
MAX_PER_HOUR = int(os.environ.get("VELVET_MAX_PER_HOUR", "6") or "6")

# `{site}` is filled at send time so the URL is always one token in the source.
LINES: dict[str, list[str]] = {
    "greet": [
        "hey {name} — welcome in. If you want a picture of your avi that actually looks like you, "
        "that's what I do. {site}",
        "welcome back {name}. Music's mine tonight, pictures are the other half of the job.",
        "{name}! good timing. I was about to put the studio link in chat anyway.",
    ],
    "what": [
        "quick one: Velvet is the studio — you order a still or a clip from a HUD you wear, "
        "and it's rendered for your avatar, not pulled off a shelf. {site}",
        "I don't sell stock photos. Every frame is generated for the person who ordered it. "
        "Gallery's free to look at: {site}",
    ],
    "price": [
        "prices are honest and in L$: L$100 for a private test frame, L$250 for a picture, "
        "L$750 for a video. Top up once, spend it on whatever you want. {site}",
        "L$250 gets you a full picture. L$100 if you just want to see what your avi looks like "
        "in one frame first. {site}",
    ],
    "gallery": [
        "if you're not ready to order, the gallery is free — no login, no wallet, just look. {site}",
        "the gallery has the distilled characters on it, and they're free to render. "
        "Start there if you're curious: {site}",
    ],
    "night": [
        "tonight's set is on, and every picture ordered during it goes up on the wall. "
        "Come watch your avi get made. {site}",
        "drop by the set — I'm DJing, the studio's open, and the frames are live on screen. {site}",
    ],
    "performer": [
        "if you perform, you can carry your own Velvet HUD and keep a share of every sale you "
        "bring in. Ask me in IM. {site}",
        "performers: the HUD is branded to you, your clients order from it, you get your cut. "
        "That's the whole deal. {site}",
    ],
}

ORDER = ("greet", "what", "price", "gallery", "night", "performer")


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def next_category(state: dict) -> str:
    """Round-robin with a memory: never the same category twice, and lowest-used first."""
    counts = state.get("counts") or {}
    last = str(state.get("lastCategory") or "")
    pool = [c for c in ORDER if c != last] or list(ORDER)
    pool.sort(key=lambda c: (int(counts.get(c) or 0), ORDER.index(c)))
    return pool[0]


def pick_line(state: dict, category: str, name: str = "") -> str:
    rows = LINES.get(category) or LINES["what"]
    used = state.get("used") or []
    fresh = [line for line in rows if line not in used] or rows
    line = fresh[0] if fresh else random.choice(rows)
    return line.replace("{site}", SITE).replace("{name}", name or "friend")


def allowed(state: dict, now: float | None = None, force: bool = False) -> tuple[bool, str]:
    """(may post, why not). The gap and the hourly cap are the whole spam policy."""
    if force:
        return True, "forced"
    now = now or time.time()
    last = float(state.get("lastSent") or 0)
    if last and now - last < MIN_GAP:
        return False, f"gap: {int(MIN_GAP - (now - last))}s left"
    sent = [float(t) for t in (state.get("sent") or []) if now - float(t) < 3600]
    if len(sent) >= MAX_PER_HOUR:
        return False, f"cap: {len(sent)} posts in the last hour"
    return True, "ok"


def post(line: str, engine: str = ENGINE) -> dict:
    """Send through her own chat tool; fall back to the console command path."""
    body = json.dumps({"message": line}).encode("utf-8")
    req = urllib.request.Request(engine + "/mcp/tool/send_chat", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 405):
            raise
    body = json.dumps({"text": "!say " + line, "from": "promoter",
                       "say_in_world": True}).encode("utf-8")
    req = urllib.request.Request(engine + "/command", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def health(engine: str = ENGINE) -> str:
    try:
        with urllib.request.urlopen(engine + "/mcp/status", timeout=6) as resp:
            got = json.loads(resp.read().decode("utf-8") or "{}")
        sl = got.get("sl") or got.get("session") or {}
        online = sl.get("online") if isinstance(sl, dict) else None
        return f"engine up · sl online={online}"
    except Exception as exc:
        return f"engine DOWN ({type(exc).__name__})"


def record(state: dict, category: str, line: str, now: float) -> dict:
    counts = dict(state.get("counts") or {})
    counts[category] = int(counts.get(category) or 0) + 1
    sent = [float(t) for t in (state.get("sent") or []) if now - float(t) < 3600]
    sent.append(now)
    used = list(state.get("used") or [])[-40:]
    used.append(line)
    return {**state, "counts": counts, "sent": sent, "used": used,
            "lastCategory": category, "lastSent": now, "total": int(state.get("total") or 0) + 1}


def one_spot(state: dict, force: bool = False, name: str = "", dry: bool = False) -> dict:
    ok, why = allowed(state, force=force)
    if not ok:
        return {"sent": False, "reason": why}
    category = next_category(state)
    line = pick_line(state, category, name)
    if dry:
        return {"sent": False, "dry": True, "category": category, "line": line}
    got = post(line)
    state = record(state, category, line, time.time())
    save_state(state)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": int(time.time()), "category": category, "line": line,
                            "ok": bool(got.get("ok", True))}) + "\n")
    return {"sent": True, "category": category, "line": line, "engine": got}


def selftest() -> int:
    fails = []
    state: dict = {}
    cats = []
    for _ in range(6):
        # Mirrors production: the category chosen is recorded, so the next choice sees it.
        cat = next_category(state)
        cats.append(cat)
        state = record(state, cat, "x", time.time())
    if cats[0] != "greet":
        fails.append(f"first spot should be greet, got {cats[0]}")
    for a, b in zip(cats, cats[1:]):
        if a == b:
            fails.append(f"same category twice: {a}")
    ok, why = allowed({"lastSent": time.time() - 10})
    if ok:
        fails.append("gap not enforced")
    ok, why = allowed({"sent": [time.time() - i for i in range(6)]})
    if ok:
        fails.append("hourly cap not enforced")
    ok, _ = allowed({"lastSent": time.time() - 1000})
    if not ok:
        fails.append("a spot after the gap was refused")
    line = pick_line({}, "price")
    if "{site}" in line or SITE not in line:
        fails.append("site token not substituted")
    if "http" in line or "://" in line:
        fails.append("a line carries a URL scheme — her wire guard will block it")
    if line.count(SITE) != 1:
        fails.append("a line should name the domain exactly once")
    for cat, rows in LINES.items():
        for row in rows:
            rendered = row.replace("{site}", SITE).replace("{name}", "friend")
            if len(rendered) > 240:
                fails.append(f"{cat} line over 240 chars")
            if "http" in rendered:
                fails.append(f"{cat} line contains a scheme and would be blocked")
    for name in ("greet", "what", "price", "gallery", "night", "performer"):
        if name not in LINES:
            fails.append(f"missing category {name}")
    if fails:
        for f in fails:
            print("FAIL", f)
        return 1
    print(f"ok: {sum(len(v) for v in LINES.values())} lines across {len(LINES)} categories, "
          f"gap {MIN_GAP}s, cap {MAX_PER_HOUR}/h")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--name", default="")
    ap.add_argument("--gap", type=int, default=0, help="override the minimum gap, seconds")
    ap.add_argument("--count", type=int, default=8, help="how many lines to preview")
    args = ap.parse_args()

    global MIN_GAP
    if args.gap:
        MIN_GAP = args.gap

    if args.selftest:
        return selftest()

    state = load_state()
    if args.dry_run:
        print(health())
        preview = dict(state)
        for index in range(max(1, args.count)):
            got = one_spot(preview, force=True, name=args.name, dry=True)
            print(f"  [{index + 1}] {got['category']:<9} {got['line']}")
            preview = record(preview, got["category"], got["line"], time.time())
        return 0

    if args.once:
        got = one_spot(state, force=args.force, name=args.name)
        print(json.dumps(got, indent=2)[:600])
        return 0 if got.get("sent") or got.get("reason", "").startswith(("gap", "cap")) else 1

    if args.loop:
        print(health())
        while True:
            got = one_spot(load_state(), force=False, name=args.name)
            print(time.strftime("%H:%M:%S"), got.get("category") or got.get("reason"),
                  "-", (got.get("line") or "")[:70])
            time.sleep(30)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
