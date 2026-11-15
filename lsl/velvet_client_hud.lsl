// velvet_client_hud.lsl — the CLIENT's side of the Velvet rail, connected to Krea2.
//
// SUPERSEDED (2026-11-15) by the two-script split in the same folder:
//   velvet_client_wallet.lsl  — balance, one debit, top-up, lock, branding, the touch menu
//   velvet_client_order.lsl   — roster, prices, picture/video order, the delivery wait
//   they talk over llMessageLinked(LINK_THIS, ...) inside one prim, so each script gets its
//   own 64 KB instead of sharing one budget.
// This file stays as the single-script reference. DO NOT install it beside the two halves:
// three scripts answering one touch is the collision-and-double-dialog bug all over again.
//
// This is the split client script, revised (2026-11-15) so the render leg reaches the
// Krea2 studio server instead of the old rail-only path.
//
//   MONEY stays on the Velvet server (`SITE_URL`, the 127.0.0.1:8792 rail behind Caddy):
//     /api/wallet, /api/wallet/topup, /api/roster, /api/commissions, /api/commission/<id>.
//     That is where the L$ wallet lives, where a generation is booked, and where a
//     failed generation is refunded. Nothing about that moved.
//
//   THE CHARACTER comes from the Krea2 server (`KREA_URL`, the /aigf app): her profile
//     sku, which is the only thing that makes the render hers. The studio picks her
//     adapter from it — never from the request's style fields, which the server now
//     ignores outright.
//
// WHY THIS SCRIPT READS A HUD-SHAPED ENDPOINT: an llHTTPRequest can hold 4096 bytes.
//     /aigf/api/gallery is 10 KB of prompts and culture blocks and arrives as "" here,
//     and /aigf/api/generate answers with ~1 MB of base64 pixels, which no LSL script can
//     read at all. /aigf/api/hud/roster is the same catalog cut down to sku + name + tag,
//     so the HUD can learn the skus and the render itself is still done, stored and
//     published by the server.
//
//   CLIENT (and the studio owner testing, and an escort buying for herself)
//     • touch → "TOP UP WALLET": ONE automatic L$ debit (PERMISSION_DEBIT asked once;
//       after that the top-up is silent) goes to the studio treasury and opens her
//       wallet balance on the site. From then on she buys AS MANY pictures and videos
//       as she wants from that balance — each order taps "Buy media", picks the girl,
//       the picture/video, a note, presses "Yes — order it", and the L$ leaves the
//       wallet at once. If a generation fails the server refunds the wallet.
//
//   THE LOCK — an escort hands her clients copies of THIS script's object
//     The lock lives in the object DESCRIPTION as her SL agent key (a UUID).
//     A locked copy: brands on attach as "Velvet HUD — <her>'s Client", buys ONLY
//     from her (checked in the HUD by her agent key in the roster and again on the
//     server), profits only her ledger, and offers nothing else.
//     An empty description is the open HUD, which buys from the whole roster.
//
// This script NEVER answers a collect and NEVER asks for payouts: that is the
// escort script's job. The client-side rail is top-up, buy, and the refund on fail.
//
// One-time setup:
//   SITE_URL must equal SLGF_PUBLIC_URL; CLAIM_KEY must equal the server's
//   SLGF_CLAIM_SECRET. Set the script no-mod before handing copies out —
//   the claim key lives inside.

string SITE_URL     = "https://dubwarz.vip";                    // the Velvet rail: wallet, orders, refunds
string KREA_URL     = "https://dubwarz.vip/aigf";               // the Krea2 studio: roster + render identity
                                                                 // (direct: https://xzyorb--orbital.us-east.modal.run)
string CLAIM_KEY    = "GETJINXED_COMMON_TEST";   // must equal the server's SLGF_CLAIM_SECRET
key    TREASURY_KEY = "f8169878-4033-4b02-b6e7-dd1816f31293";  // avatar key that receives wallet top-ups

integer DEBUG_MEM = FALSE;           // reference build only — the split halves are what ships now
integer DIALOG_CHANNEL = -7031192;   // dialogs are filtered to the wearer — no per-owner tweak, no hex math
integer LOCK_CHANNEL   = -7031195;   // the dispenser's claim/lock channel (velvet_dispatcher.lsl)

key    owner;
string residentName;
key    lockUuid;
string lockName;
integer clientLocked;       // TRUE once lockUuid is set — the copy is a client-only HUD
integer haveDebit;
integer dlgListen;
integer claimListen;        // the attach-time lock handshake, an UNLOCKED copy only

// ── wallet rail ──
key     walletId;
integer walletL;
integer menuPending;        // TRUE while a touch waits for the FRESH balance before the dialog draws
integer lastFetchFailed;    // the last wallet fetch did not answer — the dialog must qualify its figure

// ── roster / order rail ──
list    roster;             // [id0, name0, uuid0, id1, name1, uuid1, …] — resident dropped: only the lock match needs it
list    pageNames;
list    pageIds;
integer rosterPage;
integer priceImageL;
integer priceVideoL;
integer priceTestL;        // the L$100 test picture — cheap, private, instantly delivered
string  orderPerformerId;
string  orderPerformerName;
string  orderKind;          // "image" | "video"   ·   this source is V4 (2026-11-15, Krea2 leg)
string  orderNote;
string  orderId;
integer priceL;
integer topupL;
// Priced dialog labels: the button shows the AMOUNT ONLY (an LSL label is cut at 24 bytes,
// so words in a priced button were stealing the space). The handler compares against the
// exact string it built, so a price can never fall through to the wrong order kind.
string  dlgPicture;
string  dlgTest;
string  dlgVideo;
key     healthId;   // the dead-site guard before any L$ debit
integer siteDown;   // TRUE while the backend is unreachable — speak once, stay quiet after
integer pollFails;  // consecutive dead polls on an order the wallet already paid
key     rosterId;
key     orderPostId;
key     pollId;
key     topupPostId;
integer polls;

// ── Krea2 rail: the skus that make a render hers ──
key     kreaRosterId;
list    kreaSkus;   // [sku, sku, …] from /aigf/api/hud/roster — the ids the studio renders by
integer kreaSeen;   // TRUE once the Krea2 roster answered — the menu can say she has a profile

// ── helpers ─────────────────────────────────────────────────────────────────

closeListen() {
    if (dlgListen) { llListenRemove(dlgListen); dlgListen = 0; }
}

/** HEAP TELEMETRY: a collision kills the script silently, so the only useful report is
    the number printed while it is still alive. Speaks only when the budget is genuinely
    low, and names the point in the flow that spent it. */
remember(string where) {
    integer free = llGetFreeMemory();
    if (free < 16000) llOwnerSay("Velvet HUD: " + where + " left " + (string)free + " bytes.");
}

/** MEMORY TRACE (DEBUG_MEM). The probe proved the PRIM gives 59 KB free and holds no
    stale script, so whatever collides is spent by THIS script after it loads. These lines
    bracket every big allocation in the order flow; the last MEM printed before the crash
    names the allocation that killed it, because the failure itself prints nothing.
    Turn DEBUG_MEM off before handing copies out — it is dialogue the client did not ask for. */
mem(string where) {
    if (!DEBUG_MEM) return;
    llOwnerSay("MEM " + where + " free=" + (string)llGetFreeMemory());
}

/** Server-down fallback, client side: one notice when the backend goes dark and one
    when it is back. The wallet LIVES on the site, so downtime cannot steal the L$
    already debited — orders are settled or refunded by the ledger the moment the
    server returns, and the debit guard below stops new L$ from moving at all. */
siteDownSay(string where) {
    if (siteDown) return;
    siteDown = TRUE;
    llOwnerSay("Velvet HUD: the site is unreachable (" + where + "). Nothing has been charged today — your wallet sits on the site, and the debited money is settled or refunded the moment the server is back.");
}
siteUpSay() {
    if (siteDown) { siteDown = FALSE; llOwnerSay("Velvet HUD: the site is back."); }
}

/** The LOCK: only a real agent key in the description locks a copy. */
readLockFromDescription() {
    string desc = llGetObjectDesc();
    lockUuid = NULL_KEY;
    lockName = "";
    clientLocked = FALSE;
    if (llStringLength(desc) == 36 && llGetSubString(desc, 8, 8) == "-" && llGetSubString(desc, 13, 13) == "-") {
        key probe = (key)desc;
        if (probe != NULL_KEY) {
            lockUuid = probe;
            clientLocked = TRUE;
            lockName = llKey2Name(probe);
        }
    }
}

/** Personally branded object name: wearer on an open HUD, HER client on a locked one. */
brandNow() {
    if (clientLocked) {
        string who = lockName;
        if (who == "") who = llGetSubString((string)lockUuid, 0, 7);
        llSetObjectName("Velvet — " + llGetSubString(who, 0, 18) + "'s Client");
    } else {
        llSetObjectName("Velvet — " + residentName);
    }
}

/** Strip the characters that would break the JSON body or the ledger — one parse. */
string sane(string s) {
    s = llDumpList2String(llParseStringKeepNulls(s, ["\"", "\\", "|", "\r", "\n"], []), "");
    return llStringTrim(llGetSubString(s, 0, 240), STRING_TRIM);
}

/** Website-bought copies arrive unlocked and call the dispenser to encode themselves:
    shout CLAIMME, wait for the signature, verify it with the claim key, and only then
    stamp HER agent key into the description — the script is the only writer here. */
askLock() {
    if (clientLocked || claimListen) return;
    claimListen = llListen(LOCK_CHANNEL, "", NULL_KEY, "");
    llShout(LOCK_CHANNEL, "VELVET|CLAIMME|" + (string)llGetKey());
    llSetTimerEvent(60.0);       // 60 s of listening, then the claim window closes
}

string whatWord() {
    string what;
    if (orderKind == "image") what = "picture";
    else if (orderKind == "test") what = "test picture";
    else what = "video";
    return what;
}

/** The wallet balance refresh — one copy of the request used by touch and attach. */
askWallet() {
    walletId = llHTTPRequest(SITE_URL + "/api/wallet?client=" + llEscapeURL((string)owner),
        [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
         HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
         HTTP_BODY_MAXLENGTH, 512], "");
}

/** The Krea2 roster: her profile skus, and nothing else. Small enough for LSL.
    The money rail never depends on this answering — a dark studio only means the
    order carries no character and the server falls back to its own mapping. */
askKreaRoster() {
    kreaRosterId = llHTTPRequest(KREA_URL + "/api/hud/roster",
        [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
         HTTP_BODY_MAXLENGTH, 3072], "");   // 1.2 KB today for 10 escorts (~123 B each) and ~2.9 KB at the 24 cap — 4096 was 1 KB of heap reserved for nothing
}

/** Her Krea2 profile sku for a roster id: the studio renders her adapter from it.
    Empty means she has no profile on the render server yet, and the server decides. */
string skuFor(string id) {
    if (id == "") return "";
    string dw = "dw-" + llToLower(id);
    if (llListFindList(kreaSkus, [dw]) != -1) return dw;
    if (llListFindList(kreaSkus, [id]) != -1) return id;
    return "";
}

/** The roster dialog pages: nine names fit with Prev/Next/Cancel. Never called on a locked copy. */
showRosterPage() {
    integer per = 9;
    integer total = llGetListLength(roster) / 3;
    integer pages = (total + per - 1) / per;
    if (pages < 1) { state default; return; }
    rosterPage = ((rosterPage % pages) + pages) % pages;

    pageNames = [];
    pageIds = [];
    integer k;
    integer last = rosterPage * per + per;
    if (last > total) last = total;
    for (k = rosterPage * per; k < last; k++) {
        pageNames += llGetSubString(llList2String(roster, k * 3 + 1), 0, 22);
        pageIds   += llList2String(roster, k * 3);
    }
    closeListen();               // every menu open removes the previous listen first — leaks were the old crash
    dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
    llDialog(owner,
        "Who are you commissioning?\n( " + (string)(rosterPage * per + 1) + "-" + (string)last + " of " + (string)total
        + " )\nBought from your wallet — no payment dialog per order.",
        pageNames + ["Next", "Prev", "Cancel"],
        DIALOG_CHANNEL);
    llSetTimerEvent(120.0);
}

// the touch menu lives in its own step so the wallet refresh and the dialog share one flow
waitMenu() {
    closeListen();
    dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
    string text;
    string note;
    if (lastFetchFailed) note = "\n\n(last figure — the site is not answering; top-ups wait until it is back)";    if (clientLocked) {
        string who = lockName;
        if (who == "") who = "this escort's";
        text = "Velvet HUD — " + who + "'s client. Every picture or video you buy comes from her and nobody else — every L$ profits her.\n\n"
            + "Wallet balance: L$" + (string)walletL + ". Top it up once — one automatic debit — then buy as many as you like.";
    } else {
        text =            "Velvet HUD — wallet L$" + (string)walletL
            + ".\nTop up once, then buy as many pictures or videos as you like from the balance. Failed generations are refunded.";
    }
    llDialog(owner, text + note,
        ["TOP UP WALLET", "Buy media", "NOT NOW"],
        DIALOG_CHANNEL);
    llSetTimerEvent(90.0);
}

// ── default: the worn hub — the client menu, the wallet refresh ─────────────

default {
    state_entry() {
        owner = llGetOwner();
        residentName = llKey2Name(owner);
        readLockFromDescription();
        brandNow();
        integer left = llGetFreeMemory();
        if (left != -1 && left < 20000) llOwnerSay("Velvet HUD: client script memory running low (" + (string)left + " B) — tell the studio.");
        mem("load");
    }

    attach(key id) {
        if (id != NULL_KEY) {
            owner = llGetOwner();
            residentName = llKey2Name(owner);
            readLockFromDescription();
            brandNow();
            remember("attach");
            askWallet();
            // A website-delivered copy (blank description) encodes its lock on first attach:
            // the dispenser signs her agent key in and the copy rebrands as her client's HUD.
            askLock();
        }
    }

    touch_start(integer n) {
        owner = llGetOwner();
        residentName = llKey2Name(owner);
        llOwnerSay("Velvet HUD v4 — touches everywhere, live-balance menus, Krea2 renders — " + (string)llGetFreeMemory() + " bytes free.");
        // the menu shows the FRESH balance: open it on the wallet response, not in
        // the same breath as the request — the old way drew the dialog with the
        // previous poll's number, so a just-finished top-up looked unfunded
        menuPending = TRUE;
        askWallet();
        llSetTimerEvent(6.0);      // site lagging? still open the menu with the last known figure
    }

    timer() {
        llSetTimerEvent(0.0);
        if (menuPending) {
            menuPending = FALSE;       // the wallet refresh lost the race: open with what we know
            waitMenu();
            return;
        }
        if (claimListen) { llListenRemove(claimListen); claimListen = 0; }   // claim window ran out
        closeListen();
        llOwnerSay("Velvet HUD: menu timed out — touch again when you are ready.");
    }

    listen(integer channel, string name, key id, string msg) {
        // the claim: only the dispenser's signed reply, and only OUR object, ever moves a lock
        if (channel == LOCK_CHANNEL && claimListen) {
            list parts = llParseStringKeepNulls(msg, ["|"], []);
            if (llList2String(parts, 1) == "CLAIMYOU" && (key)llList2String(parts, 2) == owner) {
                askLock();      // nudge to a copy ALREADY worn by its owner — re-broadcast the claim
                return;
            }
            if (llList2String(parts, 1) == "LOCK" && llList2String(parts, 2) == (string)llGetKey()) {
                key escort = (key)llList2String(parts, 3);
                string sig = llList2String(parts, 4);
                string want = llSHA256String(CLAIM_KEY + "|" + (string)escort + "|" + (string)llGetKey() + "|" + (string)owner);
                if (sig != want || escort == NULL_KEY) return;   // unsigned or zeroed — ignore
                llListenRemove(claimListen); claimListen = 0;
                llSetObjectDesc((string)escort);                 // the encoding: her key into the description
                readLockFromDescription();
                brandNow();
                string who = lockName;
                if (who == "") who = "her";
                llOwnerSay("Velvet HUD: website delivery locked. Every L$ you spend through me now profits " + who + " (" + (string)escort + ") — nobody else.");
            }
            return;
        }
        if (channel != DIALOG_CHANNEL || id != owner) return;
        closeListen();
        llSetTimerEvent(0.0);

        if (msg == "TOP UP WALLET") state topup_amounts;
        else if (msg == "Buy media") state commission_pick;
        else if (msg == "NOT NOW") llOwnerSay("Velvet HUD: wallet L$" + (string)walletL + " — touch me whenever you are ready.");
    }

    http_response(key id, integer status, list meta, string body) {
        if (id != walletId) return;
        llSetTimerEvent(0.0);
        if (status == 200) {
            siteUpSay();
            lastFetchFailed = FALSE;
            integer fresh = (integer)llJsonGetValue(body, ["balanceL"]);
            if (fresh != walletL) walletL = fresh;      // the truth, every time the site speaks
        } else {
            siteDownSay("HTTP " + (string)status);
            lastFetchFailed = TRUE;      // keep the last-known figure for display, but the dialog says so
        }
        if (menuPending) {
            menuPending = FALSE;
            waitMenu();
        }
    }

    changed(integer change) {
        if (change & CHANGED_OWNER) llResetScript();
    }
}

// ── top-up: the ONE debit that opens the wallet ─────────────────────────────

state topup_amounts {
    state_entry() {
        closeListen();
        dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
        llDialog(owner,
            "TOP UP YOUR WALLET\nOne automatic debit to the treasury opens your balance. Later buys are paid from it — never asked again.",
            ["L$100", "L$300", "L$500", "L$1000", "Custom", "Back"],
            DIALOG_CHANNEL);
        llSetTimerEvent(120.0);
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        llSetTimerEvent(0.0);
        closeListen();

        if (msg == "Back") { state default; return; }
        if (msg == "Custom") { state topup_custom; return; }
        topupL = (integer)llGetSubString(msg, 2, -1);
        if (topupL < 1) { state default; return; }
        state topup_pay;      // ask permission FIRST — every top-up debits
    }

    touch_start(integer n) {           // touch re-opens THIS menu — no state is ever a dead end
        state topup_amounts;
    }

    timer() {
        llSetTimerEvent(0.0);
        closeListen();
        llOwnerSay("Velvet HUD: top-up timed out — touch again.");
        state default;
    }
}

state topup_custom {
    state_entry() {
        // llTextBox only delivers its answer to an ACTIVE listen on the same channel;
        // the previous state closed its listen, so without this line the box never answers
        // and the client sits here until the timer. Both text boxes need it.
        closeListen();
        dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
        llTextBox(owner,
            "How much Linden should your wallet hold? A whole number, e.g. 250",
            DIALOG_CHANNEL);
        llSetTimerEvent(180.0);
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        llSetTimerEvent(0.0);
        closeListen();
        if (llGetSubString(msg, 0, 1) == "L$") msg = llGetSubString(msg, 2, -1);
        topupL = (integer)llStringTrim(msg, STRING_TRIM);
        if (topupL < 1 || topupL > 20000) {
            llOwnerSay("Velvet HUD: type a whole number of Linden between 1 and 20000.");
            state topup_amounts;
            return;
        }
        state topup_pay;      // ask permission FIRST — every top-up debits
    }

    touch_start(integer n) {           // touch re-opens the amount box
        state topup_custom;
    }

    timer() {
        llSetTimerEvent(0.0);
        closeListen();
        llOwnerSay("Velvet HUD: top-up timed out — touch again.");
        state default;
    }
}

state topup_pay {
    state_entry() {
        if (haveDebit) state topup_check;
        else {
            llRequestPermissions(owner, PERMISSION_DEBIT);
            llSetTimerEvent(90.0);
        }
    }

    run_time_permissions(integer perms) {
        haveDebit = (perms & PERMISSION_DEBIT) != 0;
        llSetTimerEvent(0.0);
        if (haveDebit) state topup_check;
        else {
            llOwnerSay("Velvet HUD: no debit permission — the wallet cannot be topped up without it. Touch me again and grant it.");
            state default;
        }
    }

    touch_start(integer n) {           // the grant dialog may have been missed — ask again
        if (haveDebit) { state topup_check; return; }
        llRequestPermissions(owner, PERMISSION_DEBIT);
        llSetTimerEvent(90.0);
    }

    timer() {
        llSetTimerEvent(0.0);
        llOwnerSay("Velvet HUD: debit permission timed out — touch again.");
        state default;
    }
}

// The dead-site guard: no L$ ever leaves the wearer while the backend is down or
// answering garbage — the health ping precedes the debit, always.
state topup_check {
    state_entry() {
        healthId = llHTTPRequest(SITE_URL + "/api/wallet?client=" + llEscapeURL((string)owner),
            [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
             HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
             HTTP_BODY_MAXLENGTH, 512], "");
        llSetTimerEvent(20.0);
    }

    http_response(key id, integer status, list meta, string body) {
        if (id != healthId) return;
        llSetTimerEvent(0.0);
        healthId = NULL_KEY;
        if (status != 200) {
            llOwnerSay("Velvet HUD: the site is not answering (HTTP " + (string)status + ") — NOTHING has been debited, your Linden and wallet are untouched. Try again in a few minutes.");
            state default;
            return;
        }
        // the response doubles as a free balance refresh
        integer fresh = (integer)llJsonGetValue(body, ["balanceL"]);
        if (fresh > 0) walletL = fresh;
        state topup_give;
    }

    touch_start(integer n) {           // touch = ping the site again immediately
        state topup_check;
    }

    timer() {
        llSetTimerEvent(0.0);
        healthId = NULL_KEY;
        llOwnerSay("Velvet HUD: the site did not answer in time — NOTHING has been debited. Try again in a few minutes.");
        state default;
    }
}

state topup_give {
    state_entry() {
        if (!haveDebit) { state topup_pay; }   // streamed again mid-life: re-ask, never debit blind
        llOwnerSay("Velvet HUD: debit — L$" + (string)topupL + " to the studio treasury, topping up your wallet…");
        // LSL allows state changes only inside event handlers, so the debit and its
        // booking both live right here in state_entry.
        if (llGiveMoney(TREASURY_KEY, topupL)) {
            string body = llJsonSetValue("{}", ["clientResident"], residentName);
            body = llJsonSetValue(body, ["clientUuid"], (string)owner);
            body = llJsonSetValue(body, ["amountL"], (string)topupL);
            topupPostId = llHTTPRequest(SITE_URL + "/api/wallet/topup",
                [HTTP_METHOD, "POST", HTTP_MIMETYPE, "application/json",
                 HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
                 HTTP_BODY_MAXLENGTH, 512],
                body);
            llSetTimerEvent(45.0);
        } else {
            llOwnerSay("Velvet HUD: the L$ payment failed — you were NOT debited, the wallet untouched. Touch again when ready.");
            state default;
        }
    }

    touch_start(integer n) {           // money already in flight: reassure, never interrupt
        llOwnerSay("Velvet HUD: L$" + (string)topupL + " is paid — the booking is reaching the site now. I will report the moment it answers.");
    }

    timer() {
        llSetTimerEvent(0.0);
        llOwnerSay("Velvet HUD: the site did not answer the top-up — your L$ arrived; booking may need a studio nudge.");
        state default;
    }

    http_response(key id, integer status, list meta, string body) {
        if (id != topupPostId) return;
        llSetTimerEvent(0.0);
        if (status == 200) {
            walletL = (integer)llJsonGetValue(body, ["balanceL"]);
            llOwnerSay("Velvet HUD: wallet topped up — balance is now L$" + (string)walletL
                + ". Buy pictures and videos with 'Buy media': the price leaves the wallet, no payment dialog, ever.");
        } else {
            string err = llJsonGetValue(body, ["error"]);
            if (err == "" || err == JSON_INVALID) err = "HTTP " + (string)status;
            llOwnerSay("Velvet HUD: the top-up paid but booking failed — " + err + " Log it with the studio; your L$ is in the treasury either way.");
        }
        state default;
    }
}

// ── order: pick the girl (a locked HUD skips this for her, only her) ─────────

state commission_pick {
    state_entry() {
        roster = [];
        pageNames = [];
        pageIds = [];
        rosterPage = 0;
        mem("pick entry");
        llOwnerSay("Velvet HUD: fetching today's roster…");
        // Both legs at once: the rail knows who can be paid and what she costs, the
        // studio knows which profile sku makes the render hers. The menu opens on the
        // rail's answer; a late studio answer still lands in time for the order.
        askKreaRoster();
        rosterId = llHTTPRequest(SITE_URL + "/api/roster",
            [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
             HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
             HTTP_BODY_MAXLENGTH, 3072], "");   // the buffer is pre-real — a fat one steals heap straight off the top. The rail roster is ~240 B for one performer and ~2 KB at the 24 cap, so 6 KB here was 3 KB of heap thrown away while the 4 KB Krea2 answer was still in flight.
        llSetTimerEvent(45.0);
    }

    touch_start(integer n) {           // touch = pull the roster and prices from the server again
        state commission_pick;
    }

    state_exit() {
        // the roster and its pages are the biggest things this script holds — free them
        roster = [];
        pageNames = [];
        pageIds = [];
        // kreaSkus stays: the order is posted several states later and needs her sku
    }

    http_response(key id, integer status, list meta, string body) {
        if (id == kreaRosterId) {
            // the studio leg: skus only. A dark or oversized answer leaves the list empty
            // and the order simply carries no character — the rail still settles it.
            kreaRosterId = NULL_KEY;
            if (status == 200) {
                mem("krea reply");
                kreaSkus = [];
                kreaSeen = TRUE;
                list arr = llJson2List(llJsonGetValue(body, ["escorts"]));
                integer n = llGetListLength(arr);
                integer q;
                if (n > 24) n = 24;
                for (q = 0; q < n; q++) kreaSkus += llJsonGetValue(llList2String(arr, q), ["sku"]);
            }
            return;
        }
        if (id != rosterId) return;
        if (status != 200) {
            siteDownSay("HTTP " + (string)status + ", loading the roster");
            state default;
            return;
        }
        siteUpSay();

        list arr = llJson2List(llJsonGetValue(body, ["performers"]));
        integer cnt = llGetListLength(arr);
        if (cnt > 24) cnt = 24;          // the server already caps the roster here — match it exactly
        integer i;
        roster = [];
        for (i = 0; i < cnt; i++) {
            string who = llList2String(arr, i);
            roster += [ llJsonGetValue(who, ["id"]), llJsonGetValue(who, ["name"]),
                        llJsonGetValue(who, ["uuid"]) ];
        }
        priceImageL = (integer)((float)llJsonGetValue(body, ["pricing", "imageUSD"]) * (float)llJsonGetValue(body, ["pricing", "lindenRate"]) + 0.5);
        priceVideoL = (integer)((float)llJsonGetValue(body, ["pricing", "videoUSD"]) * (float)llJsonGetValue(body, ["pricing", "lindenRate"]) + 0.5);
        priceTestL  = (integer)((float)llJsonGetValue(body, ["pricing", "testUSD"])  * (float)llJsonGetValue(body, ["pricing", "lindenRate"]) + 0.5);
        // both big things are parsed out now — free them BEFORE the menu is built
        body = "";
        arr = [];
        remember("roster reply");
        mem("roster parsed");

        // A locked HUD buys from one girl and nobody else: jump straight to her.
        if (clientLocked) {
            string want = llToLower((string)lockUuid);
            integer total = llGetListLength(roster) / 3;
            integer pos = -1;
            integer j;
            for (j = 0; j < total && pos == -1; j++) {
                if (llToLower(llList2String(roster, j * 3 + 2)) == want) pos = j;
            }
            if (pos == -1) {
                string who = lockName;
                if (who == "") who = "the escort this HUD is locked to";
                llOwnerSay("Velvet HUD: " + who + " is not on the roster yet — her own HUD has to register her first. Nothing was charged.");
                state default;
                return;
            }
            orderPerformerId   = llList2String(roster, pos * 3);
            orderPerformerName = llList2String(roster, pos * 3 + 1);
            state commission_kind;
            return;
        }

        if (!llGetListLength(roster)) {
            llOwnerSay("Velvet HUD: the roster is empty — the girls sign up free; the first one is bound to come.");
            state default;
            return;
        }
        showRosterPage();
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        llSetTimerEvent(0.0);
        closeListen();

        if (msg == "Next") { rosterPage++; showRosterPage(); return; }
        if (msg == "Prev") { rosterPage--; showRosterPage(); return; }
        if (msg == "Cancel") { state default; return; }
        integer at = llListFindList(pageNames, [msg]);
        if (at != -1) {
            orderPerformerId   = llList2String(pageIds, at);
            orderPerformerName = msg;
            state commission_kind;
        }
    }

    timer() {
        llSetTimerEvent(0.0);
        closeListen();
        llOwnerSay("Velvet HUD: the roster did not answer — touch again.");
        state default;
    }
}

// ── order: picture or video, at the L$ price shown on the button ─────────────

state commission_kind {
    state_entry() {
        closeListen();
        dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
        mem("kind entry");
        dlgPicture = "L$" + (string)priceImageL;
        dlgTest    = "L$" + (string)priceTestL;
        dlgVideo   = "L$" + (string)priceVideoL;
        llDialog(owner,
            "A commission from " + orderPerformerName + ".\n"
            + "Picture " + dlgPicture + " · Test pic " + dlgTest + " · Video " + dlgVideo + "\n"
            + "Generated fresh from her avatar — her adapter and look are picked for her, never by you.\n"
            + "Leaves your wallet (L$" + (string)walletL + " now) when you confirm. Test pic: same render, never shown publicly.",
            [dlgPicture, dlgTest, dlgVideo, "Cancel"],
            DIALOG_CHANNEL);
        llSetTimerEvent(120.0);
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        llSetTimerEvent(0.0);
        closeListen();

        if (msg == "Cancel") { state default; return; }

        // the buttons ARE the prices now, so match the exact label built above
        if (msg == dlgPicture)    { orderKind = "image"; priceL = priceImageL; }
        else if (msg == dlgTest)  { orderKind = "test";  priceL = priceTestL; }
        else if (msg == dlgVideo) { orderKind = "video"; priceL = priceVideoL; }
        else return;

        // a roster with no pricing block parses as L$0 — that would offer a free order
        // the server then refuses after the client already confirmed it
        if (priceL < 1) {
            llOwnerSay("Velvet HUD: the studio price list did not load — the wallet was not touched. Touch me and try again.");
            state default;
            return;
        }

        state commission_note;
    }

    touch_start(integer n) {           // touch re-opens the kind menu
        state commission_kind;
    }

    timer() {
        llSetTimerEvent(0.0);
        closeListen();
        llOwnerSay("Velvet HUD: commission timed out — touch again.");
        state default;
    }
}

// ── the note the client writes for the studio (optional) ────────────────────

state commission_note {
    state_entry() {
        // the text box needs its own listen — the kind menu closed the previous one
        closeListen();
        dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
        llTextBox(owner,
            "What should " + orderPerformerName + " be doing in your " + whatWord()
            + "? Her limits always rule.\nLeave empty and press Submit for the studio's choice.",
            DIALOG_CHANNEL);
        llSetTimerEvent(180.0);
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        llSetTimerEvent(0.0);
        closeListen();
        orderNote = sane(msg);
        state commission_confirm;
    }

    touch_start(integer n) {           // touch re-opens the note box
        state commission_note;
    }

    timer() {
        llSetTimerEvent(0.0);
        llOwnerSay("Velvet HUD: note timed out — touch again.");
        state default;
    }
}

// ── the payment confirm popup: the wallet, the price, the refund, plainly ────

state commission_confirm {
    state_entry() {
        closeListen();
        dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
        llDialog(owner,
            "PAYMENT CONFIRM — " + orderPerformerName
            + "\n\nOrder: a " + whatWord() + " — L$" + (string)priceL
            + "\nWallet: L$" + (string)walletL
            + "\nPaid when you confirm. Failed generation refunds the wallet.",
            ["Yes — order it", "No — cancel"],
            DIALOG_CHANNEL);
        llSetTimerEvent(120.0);
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        llSetTimerEvent(0.0);
        closeListen();

        if (msg != "Yes — order it") {
            llOwnerSay("Velvet HUD: commission canceled before the order — the wallet was not touched.");
            state default;
            return;
        }

        mem("confirm click");
        string body = llJsonSetValue("{}", ["performer"], orderPerformerId);
        body = llJsonSetValue(body, ["kind"], orderKind);
        body = llJsonSetValue(body, ["note"], orderNote);
        body = llJsonSetValue(body, ["via"], "hud");
        // KREA2 LEG: the profile sku is what names her adapter on the render call. The
        // server resolves it again from the performer; this is the HUD telling it plainly
        // which profile it means, so a renamed roster id cannot silently lose her face.
        string sku = skuFor(orderPerformerId);
        if (sku != "") body = llJsonSetValue(body, ["character"], sku);
        if (clientLocked) body = llJsonSetValue(body, ["lockUuid"], (string)lockUuid);
        body = llJsonSetValue(body, ["clientResident"], residentName);
        body = llJsonSetValue(body, ["clientUuid"], (string)owner);

        orderPostId = llHTTPRequest(SITE_URL + "/api/commissions",
            [HTTP_METHOD, "POST", HTTP_MIMETYPE, "application/json",
             HTTP_BODY_MAXLENGTH, 3072], body);
        state commission_post;
    }

    touch_start(integer n) {           // touch re-opens the confirm popup
        state commission_confirm;
    }

    timer() {
        llSetTimerEvent(0.0);
        closeListen();
        llOwnerSay("Velvet HUD: confirmation timed out — the wallet was not touched. Touch again.");
        state default;
    }
}

// ── the order: the wallet is debited, generation starts immediately ──────────

state commission_post {
    state_entry() {
        llSetTimerEvent(45.0);
    }

    state_exit() {
        // the note and the performer id lived only for the order itself
        orderNote = "";
        orderPerformerId = "";
        kreaSkus = [];          // her sku has been handed over; nothing else needs it
    }

    touch_start(integer n) {           // the order is in flight: reassure, never interrupt
        llOwnerSay("Velvet HUD: confirm your order — the L$ leaves the wallet the moment you say yes.");
    }

    timer() {
        llSetTimerEvent(0.0);
        llOwnerSay("Velvet HUD: the site did not take the order — the wallet was not touched.");
        state default;
    }

    http_response(key id, integer status, list meta, string body) {
        if (id != orderPostId) return;
        llSetTimerEvent(0.0);

        if (status == 200) {
            orderId = llJsonGetValue(body, ["orderId"]);
            walletL = (integer)llJsonGetValue(body, ["balanceL"]);
            llOwnerSay("Velvet HUD: order " + orderId + " in — L$" + (string)priceL
                + " left your wallet (L$" + (string)walletL + " left). Generating your "
                + whatWord() + " of " + orderPerformerName + ".");
            state commission_wait;
        } else {
            string err = llJsonGetValue(body, ["error"]);
            if (err == "" || err == JSON_INVALID) err = "HTTP " + (string)status;
            llOwnerSay("Velvet HUD: the order was refused — " + err + " The wallet keeps its money.");
            state default;
        }
    }
}

// ── wait for the settlement: the server books or refunds; the file is offered ──
// The wearer is NEVER held blind here: every cycle speaks, touch forces a fresh
// check, and every outcome — delivered, failed, site-dark — says itself out loud.

state commission_wait {
    state_entry() {
        polls = 0;
        pollFails = 0;
        llOwnerSay("Velvet HUD: the studio is generating your " + whatWord() + " of " + orderPerformerName + " — L$" + (string)priceL + " is paid from your wallet. I will say the second it lands or fails (refund is automatic). Touch me any time to check.");
        pollId = llHTTPRequest(SITE_URL + "/api/commission/" + llEscapeURL(orderId),
            [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
             HTTP_BODY_MAXLENGTH, 1024], "");   // status + openUrl: never more than a few hundred bytes
        llSetTimerEvent(15.0);
    }

    touch_start(integer n) {           // “untouchable” no more: touch = immediate status check
        owner = llGetOwner();
        pollId = llHTTPRequest(SITE_URL + "/api/commission/" + llEscapeURL(orderId),
            [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
             HTTP_BODY_MAXLENGTH, 1024], "");
        llOwnerSay("Velvet HUD: checking your order right now…");
        llSetTimerEvent(15.0);
    }

    state_exit() {
        // the order id died with the wait, whichever way we left the state
        orderId = "";
    }

    timer() {
        if (polls++ > 40) {
            llSetTimerEvent(0.0);
            llOwnerSay("Velvet HUD: still generating — your order stands, your wallet is already paid. Touch later to check.");
            state default;
            return;
        }
        // PROGRESS ALERTS: quiet polls are how “indefinitely processing” lies are born —
        // this state talks every few checks so the wait always has a pulse.
        if (polls == 5) llOwnerSay("Velvet HUD: still rendering — check 5/40. Your L$" + (string)priceL + " is held by the order; any failure refunds it.");
        if (polls == 12 || polls == 25) llOwnerSay("Velvet HUD: still rendering — check " + (string)polls + "/40. Touch me later if you like — touching forgets nothing.");
        pollId = llHTTPRequest(SITE_URL + "/api/commission/" + llEscapeURL(orderId),
            [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
             HTTP_BODY_MAXLENGTH, 1024], "");
    }

    http_response(key id, integer status, list meta, string body) {
        if (id != pollId) return;
        if (status != 200) {
            // the order stands, the wallet is already debited on the site; when the server
            // is back it settles or refunds the ledger — nothing the client does now helps.
            pollFails++;
            if (pollFails == 3) llOwnerSay("Velvet HUD: the site went dark while your " + whatWord() + " was being made. Nothing new is charged — the ledger books it or refunds the wallet when the site is back. I keep checking quietly.");
            else if (pollFails == 12) llOwnerSay("Velvet HUD: the site is STILL dark (check 12). The order stands — I stop nagging and keep polling.");
            return;
        }
        pollFails = 0;

        string st = llJsonGetValue(body, ["status"]);
        llSetTimerEvent(0.0);

        if (st == "paid" || st == "ready") {
            // One link, the server decides: her profile page with the new media on it
            // (pictures and videos are published on her profile the moment the money
            // books), or the signed private file for the L$100 test grade.
            string openUrl = llJsonGetValue(body, ["openUrl"]);
            if (openUrl == "" || openUrl == JSON_INVALID) openUrl = llJsonGetValue(body, ["url"]);
            llOwnerSay("Velvet HUD: delivered — " + orderPerformerName + "'s " + whatWord() + " is yours. Wallet L$" + (string)walletL + ".");
            if (openUrl != "" && openUrl != JSON_INVALID) {
                if (llGetSubString(openUrl, 0, 0) == "/") openUrl = SITE_URL + openUrl;   // relative path from the server → absolute
                llLoadURL(owner, "Opening her page — your " + whatWord() + " is already on it:", openUrl);
            }
            state default;
        } else if (st == "failed") {
            llOwnerSay("Velvet HUD: the generation failed — L$" + (string)priceL + " has been refunded to your wallet automatically. Touch again to re-order.");
            state default;
        } else if (st == "pending" || st == "approved") {
            llSetTimerEvent(15.0);      // still generating — keep polling, progress alerts above speak
        } else {
            // an unknown status is never silent — silence is the bug the client felt today
            llOwnerSay("Velvet HUD: the order is in an unrecognized state (\"" + st + "\"). Touch me to re-check, or ask the studio.");
            state default;
        }
    }
}
