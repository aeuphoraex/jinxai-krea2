// ══════════════════════════════════════════════════════════════════════════════
// VELVET RAIL — COMPLETE LSL SET (generated from the files on disk, verbatim)
//
// LSL has one script per prim ITEM, so this bundle is the whole rail in one document:
// copy each FILE block below into its own new script and its own prim item. The bundle
// itself is not pastable as a single script — it has five `default` states by design.
//
// INSTALL
//   client prim  : velvet_client_wallet.lsl + velvet_client_order.lsl (same prim, both)
//                  delete the old single DUBWARZ CLIENT SLAVE item first
//   escort prim  : velvet_escort_hud.lsl
//   dispenser    : velvet_dispatcher.lsl
//   probe        : heap_probe.lsl (only when diagnosing memory)
//
// BEFORE HANDING COPIES OUT
//   Confirm SITE_URL and CLAIM_KEY are identical in every script, and keep DEBUG_MEM FALSE.
//   Generated from: lsl/*.lsl — regenerate this file rather than editing it.
// ══════════════════════════════════════════════════════════════════════════════

// ────────────────────────────────────────────────────────────────────────────
// FILE: velvet_client_wallet.lsl
// CLIENT HUD — the money (balance, one debit, top-up, lock, branding, touch menu). Script item 1 of 2 in the client prim.
// ────────────────────────────────────────────────────────────────────────────

// velvet_client_wallet.lsl — HALF of the client HUD: THE MONEY.
//
// The single client script was ~43 KB of source and collided in a prim that the heap probe
// measured at 59,612 B free with Mono — so the budget was being spent by the script itself,
// not by the object. The rail already knows the cure: split at the seam where the flow
// already divides. This half owns the wallet; velvet_client_order.lsl owns the render.
//
//   THIS SCRIPT: the L$ wallet, the ONE debit, the top-up, the refund message, the LOCK
//   (description branding + claim handshake), and the touch menu that starts both halves.
//
//   THE OTHER HALF: roster, prices, picture/video/test order, the wait and the delivery.
//
// HOW THEY TALK: both scripts live in the SAME prim (a prim holds many script items) and
// use llMessageLinked(LINK_THIS, ...), which delivers to sibling scripts in that prim.
// No linkset, no sensors, no chat channel. Dialogs stay on separate channels — one shared
// channel would hand the same button press to both scripts.
//
//     ORDER_START  -> the client pressed "Buy media"; the order half takes over
//     ORDER_DONE   -> the order half is idle again; touches come back to this menu
//     WALLET_ASK   -> the order half wants the current balance
//     WALLET_NOW   -> this half answers with (string)walletL
//     WALLET_SET   -> the order half reports the balance its order response returned
//     LOCK_STATE   -> this half tells the order half which escort the copy is locked to
//
// One-time setup: SITE_URL must equal SLGF_PUBLIC_URL; CLAIM_KEY must equal the server's
// SLGF_CLAIM_SECRET. Set the script no-mod before handing copies out — the key is inside.

string SITE_URL     = "https://dubwarz.vip";
string CLAIM_KEY    = "GETJINXED_COMMON_TEST";   // must equal the server's SLGF_CLAIM_SECRET
key    TREASURY_KEY = "f8169878-4033-4b02-b6e7-dd1816f31293";

integer DIALOG_CHANNEL = -7031192;   // this half's dialogs (the order half uses -7031193)
integer LOCK_CHANNEL   = -7031195;   // the dispenser's claim/lock channel
integer DEBUG_MEM      = FALSE;      // diagnosed: loads with 29,036 B free — keep OFF in shipping

// link-message numbers, identical in velvet_client_order.lsl
integer MSG_ORDER_START = 1000;
integer MSG_ORDER_DONE  = 1001;
integer MSG_WALLET_ASK  = 1002;
integer MSG_WALLET_NOW  = 1003;
integer MSG_WALLET_SET  = 1004;
integer MSG_LOCK_STATE  = 1005;

key    owner;
string residentName;
key    lockUuid;
string lockName;
integer clientLocked;       // TRUE once lockUuid is set — the copy is a client-only HUD
integer haveDebit;
integer dlgListen;
integer claimListen;        // the attach-time lock handshake, an UNLOCKED copy only
integer orderActive;        // TRUE while the order half owns the dialogs

key     walletId;
integer walletL;
integer menuPending;        // TRUE while a touch waits for the FRESH balance before the dialog draws
integer lastFetchFailed;    // the last wallet fetch did not answer — the dialog must qualify its figure
key     healthId;           // the dead-site guard before any L$ debit
integer siteDown;           // TRUE while the backend is unreachable — speak once, stay quiet after
integer topupL;
key     topupPostId;

// ── helpers (defined before every caller: LSL has no forward declarations) ──

closeListen() {
    if (dlgListen) { llListenRemove(dlgListen); dlgListen = 0; }
}

mem(string where) {
    if (!DEBUG_MEM) return;
    llOwnerSay("MEM wallet/" + where + " free=" + (string)llGetFreeMemory());
}

/** Server-down fallback: one notice when the backend goes dark and one when it is back.
    The wallet LIVES on the site, so downtime cannot steal the L$ already debited — the
    ledger settles or refunds it the moment the server returns. */
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

pushLock() {
    llMessageLinked(LINK_THIS, MSG_LOCK_STATE, lockName, lockUuid);
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

/** Website-bought copies arrive unlocked and call the dispenser to encode themselves:
    shout CLAIMME, wait for the signature, verify it with the claim key, and only then
    stamp HER agent key into the description — the script is the only writer here. */
askLock() {
    if (clientLocked || claimListen) return;
    claimListen = llListen(LOCK_CHANNEL, "", NULL_KEY, "");
    llShout(LOCK_CHANNEL, "VELVET|CLAIMME|" + (string)llGetKey());
    llSetTimerEvent(60.0);       // 60 s of listening, then the claim window closes
}

/** The wallet balance refresh — one copy of the request used by touch and attach. */
askWallet() {
    walletId = llHTTPRequest(SITE_URL + "/api/wallet?client=" + llEscapeURL((string)owner),
        [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
         HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
         HTTP_BODY_MAXLENGTH, 512], "");
}

/** The touch menu. The order half is a message away, not a state machine in this script. */
waitMenu() {
    closeListen();
    dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
    string text;
    string note;
    if (lastFetchFailed) note = "\n\n(last figure — the site is not answering; top-ups wait until it is back)";
    if (clientLocked) {
        string who = lockName;
        if (who == "") who = "this escort's";
        text = "Velvet HUD — " + who + "'s client. Every picture or video you buy comes from her and nobody else — every L$ profits her.\n\n"
            + "Wallet balance: L$" + (string)walletL + ". Top it up once — one automatic debit — then buy as many as you like.";
    } else {
        text = "Velvet HUD — wallet L$" + (string)walletL
            + ".\nTop up once, then buy as many pictures or videos as you like from the balance. Failed generations are refunded.";
    }
    llDialog(owner, text + note, ["TOP UP WALLET", "Buy media", "NOT NOW"], DIALOG_CHANNEL);
    llSetTimerEvent(90.0);
}

// ── default: the worn hub — the client menu, the wallet refresh ─────────────

default {
    state_entry() {
        owner = llGetOwner();
        residentName = llKey2Name(owner);
        readLockFromDescription();
        brandNow();
        pushLock();
        integer left = llGetFreeMemory();
        if (left != -1 && left < 20000) llOwnerSay("Velvet HUD: wallet script memory running low (" + (string)left + " B) — tell the studio.");
        mem("load");
    }

    attach(key id) {
        if (id != NULL_KEY) {
            owner = llGetOwner();
            residentName = llKey2Name(owner);
            readLockFromDescription();
            brandNow();
            pushLock();
            mem("attach");
            askWallet();
            // A website-delivered copy (blank description) encodes its lock on first attach:
            // the dispenser signs her agent key in and the copy rebrands as her client's HUD.
            askLock();
        }
    }

    touch_start(integer n) {
        owner = llGetOwner();
        residentName = llKey2Name(owner);
        if (orderActive) {
            // the order half is mid-flow and owns the dialogs; its own touch_start re-opens
            // the step it is on, so this menu must not draw over it
            return;
        }
        llOwnerSay("Velvet HUD v5 — wallet L$" + (string)walletL + ".");
        // the menu shows the FRESH balance: open it on the wallet response, not in the
        // same breath as the request — the old way drew the dialog with the previous
        // poll's number, so a just-finished top-up looked unfunded
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
                pushLock();
                string who = lockName;
                if (who == "") who = "her";
                llOwnerSay("Velvet HUD: website delivery locked. Every L$ you spend through me now profits " + who + " (" + (string)escort + ") — nobody else.");
            }
            return;
        }
        if (channel != DIALOG_CHANNEL || id != owner) return;
        closeListen();
        llSetTimerEvent(0.0);

        if (msg == "TOP UP WALLET") { state topup_amounts; return; }
        if (msg == "Buy media") {
            // hand the whole render flow to the sibling script: it fetches, prices and asks
            // its own questions. This half only hands over the balance it already knows.
            orderActive = TRUE;
            llMessageLinked(LINK_THIS, MSG_ORDER_START, (string)walletL, owner);
            return;
        }
        if (msg == "NOT NOW") llOwnerSay("Velvet HUD: wallet L$" + (string)walletL + " — touch me whenever you are ready.");
    }

    link_message(integer sender, integer num, string str, key id) {
        if (num == MSG_WALLET_ASK) {
            llMessageLinked(LINK_THIS, MSG_WALLET_NOW, (string)walletL, owner);
        } else if (num == MSG_WALLET_SET) {
            walletL = (integer)str;          // the order response is the truth, every time
            lastFetchFailed = FALSE;
        } else if (num == MSG_ORDER_DONE) {
            orderActive = FALSE;
        }
    }

    http_response(key id, integer status, list meta, string body) {
        if (id != walletId) return;
        llSetTimerEvent(0.0);
        if (status == 200) {
            siteUpSay();
            lastFetchFailed = FALSE;
            walletL = (integer)llJsonGetValue(body, ["balanceL"]);
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
            body = "";
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

// ────────────────────────────────────────────────────────────────────────────
// FILE: velvet_client_order.lsl
// CLIENT HUD — the render (roster, prices, picture/test/video order, wait, link delivery). Script item 2 of 2 in the client prim.
// ────────────────────────────────────────────────────────────────────────────

// velvet_client_order.lsl — HALF of the client HUD: THE RENDER.
//
// The other half is velvet_client_wallet.lsl (balance, top-up, debit, lock, branding).
// Both script items live in the SAME prim and talk over llMessageLinked(LINK_THIS, ...),
// so this half needs no linkset, no sensor and no chat channel to reach the wallet.
//
//   THIS SCRIPT: the roster and its prices, the picture/video/test order, the note, the
//   confirm, the POST to /api/commissions, and the wait for the delivered file.
//
//   DIALOGS: this half listens on its OWN channel (-7031193). Sharing the wallet's channel
//   would deliver the same button press to both scripts and draw two dialogs over each
//   other — the exact reason the split needs two channels, not one.
//
//   BALANCE: it never owns the number. It asks the wallet half (WALLET_ASK → WALLET_NOW)
//   and reports back what the order response returned (WALLET_SET), so the figure shown
//   and the figure spent cannot drift apart.
//
//   LOCK: the wallet half owns the description and broadcasts it (LOCK_STATE). A locked
//   copy buys from one performer and nobody else, decided here from that broadcast.

string SITE_URL  = "https://dubwarz.vip";
string KREA_URL  = "https://dubwarz.vip/aigf";   // the studio: roster skus + render identity
string CLAIM_KEY = "GETJINXED_COMMON_TEST";

integer DIALOG_CHANNEL = -7031193;   // this half only — the wallet half uses -7031192
integer DEBUG_MEM      = FALSE;      // the wallet half proved the split fits; stripe the traces for shipping

// link-message numbers, identical in velvet_client_wallet.lsl
integer MSG_ORDER_START = 1000;
integer MSG_ORDER_DONE  = 1001;
integer MSG_WALLET_ASK  = 1002;
integer MSG_WALLET_NOW  = 1003;
integer MSG_WALLET_SET  = 1004;
integer MSG_LOCK_STATE  = 1005;

key    owner;
string residentName;
integer dlgListen;
integer walletL;            // pushed by the wallet half; never authoritative on its own
key     lockUuid;
string  lockName;
integer clientLocked;

list    roster;             // [id0, name0, uuid0, id1, name1, uuid1, …]
list    pageNames;
list    pageIds;
integer rosterPage;
integer priceImageL;
integer priceVideoL;
integer priceTestL;
string  orderPerformerId;
string  orderPerformerName;
string  orderKind;
string  orderNote;
string  orderId;
integer priceL;
string  dlgPicture;
string  dlgTest;
string  dlgVideo;
key     rosterId;
key     kreaRosterId;
list    kreaSkus;
string  lastOpenUrl;         // the delivered page, kept so a touch can hand it over again
key     orderPostId;
key     pollId;
integer polls;
integer pollFails;
integer siteDown;

// ── helpers (all above their first caller) ──

closeListen() {
    if (dlgListen) { llListenRemove(dlgListen); dlgListen = 0; }
}

mem(string where) {
    if (!DEBUG_MEM) return;
    llOwnerSay("MEM order/" + where + " free=" + (string)llGetFreeMemory());
}

siteDownSay(string where) {
    if (siteDown) return;
    siteDown = TRUE;
    llOwnerSay("Velvet HUD: the site is unreachable (" + where + "). Nothing has been charged today — your wallet sits on the site, and the debited money is settled or refunded the moment the server is back.");
}

siteUpSay() {
    if (siteDown) { siteDown = FALSE; llOwnerSay("Velvet HUD: the site is back."); }
}

/** Strip the characters that would break the JSON body — one parse. */
string sane(string s) {
    s = llDumpList2String(llParseStringKeepNulls(s, ["\"", "\\", "|", "\r", "\n"], []), "");
    return llStringTrim(llGetSubString(s, 0, 240), STRING_TRIM);
}

/** The order page as an absolute link. Second Life only makes a URL clickable in chat
    when it carries its scheme, so a relative path from the server is completed here. */
string absUrl(string u) {
    if (u == "" || u == JSON_INVALID) return "";
    if (llGetSubString(u, 0, 0) == "/") return SITE_URL + u;
    return u;
}

string whatWord() {
    if (orderKind == "image") return "picture";
    if (orderKind == "test") return "test picture";
    return "video";
}

/** Her Krea2 profile sku for a roster id: the studio renders her adapter from it. */
string skuFor(string id) {
    if (id == "") return "";
    string dw = "dw-" + llToLower(id);
    if (llListFindList(kreaSkus, [dw]) != -1) return dw;
    if (llListFindList(kreaSkus, [id]) != -1) return id;
    return "";
}

/** The Krea2 roster: her profile skus, and nothing else. A dark studio only means the
    order carries no character and the server falls back to its own mapping. */
askKreaRoster() {
    kreaRosterId = llHTTPRequest(KREA_URL + "/api/hud/roster",
        [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
         HTTP_BODY_MAXLENGTH, 3072], "");
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
    closeListen();               // every menu open removes the previous listen first
    dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
    llDialog(owner,
        "Who are you commissioning?\n( " + (string)(rosterPage * per + 1) + "-" + (string)last + " of " + (string)total
        + " )\nBought from your wallet — no payment dialog per order.",
        pageNames + ["Next", "Prev", "Cancel"],
        DIALOG_CHANNEL);
    llSetTimerEvent(120.0);
}

// ── default: idle, waiting for the wallet half to hand over a "Buy media" ───
// LSL requires a `default` state and rejects any named state declared before it, which is
// why this is `default` and not `idle`: `state idle {` was a syntax error on its own.

default {
    state_entry() {
        closeListen();
        owner = llGetOwner();
        residentName = llKey2Name(owner);
        // Hand the dialogs back to the wallet half: it must stop ignoring touches now that
        // no order question is on screen. Sent on every return to idle, including startup.
        llMessageLinked(LINK_THIS, MSG_ORDER_DONE, "", owner);
        roster = [];
        pageNames = [];
        pageIds = [];
        kreaSkus = [];
        mem("idle");
    }

    link_message(integer sender, integer num, string str, key id) {
        if (num == MSG_LOCK_STATE) {
            lockUuid = id;
            lockName = str;
            clientLocked = (id != NULL_KEY);
            return;
        }
        if (num == MSG_WALLET_NOW) {
            walletL = (integer)str;
            return;
        }
        if (num == MSG_ORDER_START) {
            owner = llGetOwner();
            residentName = llKey2Name(owner);
            walletL = (integer)str;
            llMessageLinked(LINK_THIS, MSG_WALLET_ASK, "", owner);
            state commission_pick;
        }
    }
}

// ── order: pick the girl (a locked HUD skips this for her, only her) ─────────

state commission_pick {
    state_entry() {
        roster = [];
        pageNames = [];
        pageIds = [];
        rosterPage = 0;
        owner = llGetOwner();
        residentName = llKey2Name(owner);
        mem("pick entry");
        llOwnerSay("Velvet HUD: fetching today's roster…");
        // Both legs at once: the rail knows who can be paid and what she costs, the
        // studio knows which profile sku makes the render hers. The menu opens on the
        // rail's answer; a late studio answer still lands in time for the order.
        askKreaRoster();
        rosterId = llHTTPRequest(SITE_URL + "/api/roster",
            [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
             HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
             HTTP_BODY_MAXLENGTH, 3072], "");
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
            kreaRosterId = NULL_KEY;
            if (status == 200) {
                mem("krea reply");
                kreaSkus = [];
                list arr = llJson2List(llJsonGetValue(body, ["escorts"]));
                integer n = llGetListLength(arr);
                integer q;
                if (n > 24) n = 24;
                for (q = 0; q < n; q++) kreaSkus += llJsonGetValue(llList2String(arr, q), ["sku"]);
                arr = [];
            }
            body = "";
            return;
        }
        if (id != rosterId) return;
        rosterId = NULL_KEY;
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
        mem("confirm entry");
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
            llMessageLinked(LINK_THIS, MSG_WALLET_SET, (string)walletL, owner);   // keep the wallet half honest
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
        // The link is handed over NOW, not only at the end: the order page is live, it
        // reloads itself while the studio renders, and it survives this HUD being detached.
        // Clients copy it, keep it, and open it on a phone — so it is spoken in chat where
        // the viewer makes it clickable, and stored for every later touch.
        lastOpenUrl = absUrl("/aigf/v/" + orderId);
        llOwnerSay("Velvet HUD: the studio is generating your " + whatWord() + " of " + orderPerformerName + " — L$" + (string)priceL + " is paid from your wallet. I will say the second it lands or fails (refund is automatic). Touch me any time to check, and this is your page — it fills in when the picture lands:\n" + lastOpenUrl);
        pollId = llHTTPRequest(SITE_URL + "/api/commission/" + llEscapeURL(orderId),
            [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
             HTTP_BODY_MAXLENGTH, 1024], "");
        llSetTimerEvent(15.0);
    }

    touch_start(integer n) {           // touch = immediate status check, and the link again
        owner = llGetOwner();
        pollId = llHTTPRequest(SITE_URL + "/api/commission/" + llEscapeURL(orderId),
            [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
             HTTP_BODY_MAXLENGTH, 1024], "");
        // A client who lost the popup, cleared their chat or relogged gets the link back
        // here — the URL lives in the script, not in a window they may have closed.
        if (lastOpenUrl != "") {
            llOwnerSay("Velvet HUD: checking your order right now. Your page:\n" + lastOpenUrl);
        } else {
            llOwnerSay("Velvet HUD: checking your order right now…");
        }
        llSetTimerEvent(15.0);
    }

    state_exit() {
        orderId = "";
    }

    timer() {
        if (polls++ > 40) {
            llSetTimerEvent(0.0);
            llOwnerSay("Velvet HUD: still generating — your order stands, your wallet is already paid. Touch later to check. Your page:\n" + lastOpenUrl);
            state default;
            return;
        }
        // PROGRESS ALERTS: quiet polls are how "indefinitely processing" lies are born —
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
            // One link, the server decides: her profile page with the new media on it, or
            // the signed private file for the L$100 test grade.
            string openUrl = absUrl(llJsonGetValue(body, ["openUrl"]));
            if (openUrl == "") openUrl = absUrl(llJsonGetValue(body, ["url"]));
            if (openUrl == "") openUrl = lastOpenUrl;
            if (openUrl != "") lastOpenUrl = openUrl;
            llOwnerSay("Velvet HUD: delivered — " + orderPerformerName + "'s " + whatWord() + " is yours. Wallet L$" + (string)walletL + ".\nYour page — keep this link, it is the picture:\n" + openUrl);
            if (openUrl != "") {
                // Popup for the click-through, and the same link already sits in the chat
                // line above for a client whose viewer blocks the popup.
                llLoadURL(owner, "Opening your " + whatWord() + " of " + orderPerformerName + ":", openUrl);
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

// ────────────────────────────────────────────────────────────────────────────
// FILE: velvet_escort_hud.lsl
// ESCORT HUD — her side: roster registration, earnings, the client copy she hands over.
// ────────────────────────────────────────────────────────────────────────────

// velvet_escort_hud.lsl — the ESCORT's side of the Velvet rail (SPLIT SCRIPT).
//
// The other half of what used to be velvet_payout_hud.lsl. The monolith died on its
// first run with a Stack-Heap Collision — it needed ~73 KB against the 64 KB Mono
// budget — so the rail is three scripts: this one serves the ESCORT, the client script
// serves her clients, and the studio owner's HUD is the third.
//
//   ESCORT (the girl herself — her master HUD)
//     • on attach and on a quiet timer, it asks the site for her unclaimed share and
//       SPEAKS UP the second money becomes collectible — she never checks by hand.
//     • touch → "Escorts' payouts" calls for the funding sender in range
//       (velvet_payout_sender.lsl), which pays her in L$ and books the claim.
//     • touch → "Register": one-tap signup, then the HUD ASKS HER LIMITS — what
//       must never appear in her generated pictures or videos — stored with her
//       record. Every render refuses past them.
//     • touch → "Lock to me": stamps HER agent key into the object description;
//       copies of the CLIENT HUD (or this one) taken after that are locked to her.
//
// This script NEVER debits anybody and NEVER buys for a client: the sender is the only
// script in the rail that pays a performer, and buying is the client script's job.
// Her ledger on the site is the ceiling — neither a forged message nor a double
// collect can overpay her.
//
// HEAP RULES (the collision that killed the monolith, and every way back to it):
//   * A user function must be DEFINED BEFORE it is called — LSL has no forward
//     declarations, so `waitMenu()` may only sit below `closeListen()` and
//     `armTicker()`. Reordering helpers under the menu is a compile error, not a style
//     choice.
//   * Every llHTTPRequest with HTTP_BODY_MAXLENGTH preallocates that many bytes for as
//     long as the request is pending. Ask for what the answer actually is: the ledger
//     is ~120 B, so 1024 is already 8x headroom.
//   * Big strings and parsed lists must be freed the moment their last use is behind
//     them (see `register_limits`), because LSL's heap only ever grows.
//
// One-time setup:
//   SITE_URL must equal SLGF_PUBLIC_URL; CLAIM_KEY must equal the server's
//   SLGF_CLAIM_SECRET. Set the script no-mod — the claim key lives inside.

string SITE_URL  = "https://dubwarz.vip";
string CLAIM_KEY = "GETJINXED_COMMON_TEST";   // must equal the server's SLGF_CLAIM_SECRET
integer PAIR_CHANNEL   = -7031189;   // same constant in velvet_payout_sender.lsl
integer DIALOG_CHANNEL = -7031192;   // dialogs are filtered to the wearer
integer TICK           = 120;        // seconds between quiet ledger polls

key    owner;
string residentName;

// ── ledger rail ──
key     payoutId;
integer waitManual;         // 1 while a by-hand ledger fetch is pending
integer menuPending;        // TRUE while a touch is fetching the live ledger before its menu opens
float   pendingUSD;
integer pendingL;
integer lastAlertL;         // the quiet alert only speaks when this changes
integer pairListen;
integer knowsPerformer;     // FALSE after a 404 — quiet polls skip until a manual one runs
integer siteDown;           // TRUE while the backend is unreachable — speak once, stay quiet after
integer dlgListen;

// ── register rail ──
string regGig;
string regLimits;
key    rosterId;

// ── helpers (ALL of them above the first caller: LSL needs them defined first) ──

closeListen() {
    if (dlgListen) { llListenRemove(dlgListen); dlgListen = 0; }
}

armTicker() {
    llSetTimerEvent(TICK);
}

/** Strip the characters that would break the JSON body or the ledger — one parse. */
string sane(string s) {
    s = llDumpList2String(llParseStringKeepNulls(s, ["\"", "\\", "|", "\r", "\n"], []), "");
    return llStringTrim(llGetSubString(s, 0, 240), STRING_TRIM);
}

/** HEAP TELEMETRY. Every string literal in this script lives in the same 64 KB Mono heap
    as the stack, so a collision is a budget event that has already happened — the script
    dies and says nothing. This prints the number while it is still alive, so the next
    report names the state that ate the heap instead of guessing at it. Threshold sits
    below the load-time figure, so it only speaks when something is genuinely wrong. */
remember(string where) {
    integer free = llGetFreeMemory();
    if (free < 16000) llOwnerSay("Velvet HUD: " + where + " left " + (string)free + " bytes.");
}

/** The ledger fetch behind both the manual check and the quiet alert poll.
    1024 bytes for a ~120 B answer: enough for a bigger pending block, small enough that
    the buffer never becomes the heap's biggest tenant. */
askLedger() {
    payoutId = llHTTPRequest(SITE_URL + "/api/payouts/claim?resident=" + llEscapeURL(residentName),
        [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
         HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
         HTTP_BODY_MAXLENGTH, 1024], "");
}

/** One-time notice when the backend goes dark, and one on recovery. A dead site may sit
    above the money for a while: her shares are pledged on the site only when they were
    collected in-world, so the ledger CANNOT gain a claim she has not been paid —
    downtime can never take money from her, only delay the alert. */
siteDownSay() {
    if (siteDown) return;
    siteDown = TRUE;
    llOwnerSay("Velvet HUD: the site is unreachable right now. Nothing of yours can be lost by waiting — every share is collected in-world BEFORE it is closed on the ledger. I will check quietly and shout when it is back.");
}

siteUpSay() {
    if (siteDown) { siteDown = FALSE; llOwnerSay("Velvet HUD: the site is back — checking your unclaimed share now."); }
}

/** The menu a touch asked for — opened once the ledger has spoken (or the 6-second
    fallback fired, using the last known figure). */
waitMenu() {
    closeListen();
    dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
    string head;
    if (pendingL >= 1) head = (string)pendingL + " L$ ($" + (string)pendingUSD + ") is unclaimed on the site right now.";
    else head = "Nothing unclaimed right now — every new sale puts your share here.";
    llDialog(owner,
        "Velvet HUD — yours. " + head + "\n\n"
        + "Payouts — call for the funding sender and take your share in L$.\n"
        + "Register — one tap, then tell me your LIMITS (what must never appear in your renders).\n"
        + "Lock to me — stamp your agent key into my description so client copies buy only from you.",
        ["Escorts' payouts", "Register", "Lock to me", "NOT NOW"],
        DIALOG_CHANNEL);
    llSetTimerEvent(90.0);                    // menu timeout; the ledger ticker re-arms after
}

// ── default: the worn hub — ledger polls, the alert, the escort menu ────────

default {
    state_entry() {
        owner = llGetOwner();
        residentName = llKey2Name(owner);
        knowsPerformer = TRUE;
        integer mem = llGetFreeMemory();
        if (mem != -1 && mem < 20000) llOwnerSay("Velvet HUD: escort script memory running low (" + (string)mem + " B) — tell the studio.");
        if (llGetAttached() != 0) armTicker();
    }

    attach(key id) {
        if (id != NULL_KEY) {
            owner = llGetOwner();
            residentName = llKey2Name(owner);
            askLedger();                 // an escort copy checks her unclaimed share
            llSetTimerEvent(0.0);
            armTicker();
            remember("attach");
        }
    }

    touch_start(integer n) {
        owner = llGetOwner();
        residentName = llKey2Name(owner);
        // the menu shows the FRESH unclaimed share: fetch the ledger first, open on
        // the response — the old way drew the dialog with the last poll's number
        menuPending = TRUE;
        if (waitManual != 1) askLedger();
        llSetTimerEvent(6.0);                 // site lagging? still open the menu with the last known figure
    }

    timer() {
        if (menuPending) {
            menuPending = FALSE;              // the ledger refresh lost the race: open with what we know
            waitMenu();
            return;
        }
        if (waitManual == 1) {
            askLedger();
        } else if (llGetAttached() != 0 && knowsPerformer) {
            askLedger();                      // the quiet instant-collectibility poll
        }
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        closeListen();
        llSetTimerEvent(0.0);
        waitManual = 0;

        if (msg == "Escorts' payouts") {
            waitManual = 1;
            armTicker();
            askLedger();
        } else if (msg == "Register") {
            state register;
        } else if (msg == "Lock to me") {
            // The escort stamps HER agent key into the description: copies taken
            // after this are locked to her UUID, profit only her, brand on attach.
            llSetObjectDesc((string)owner);
            llOwnerSay("Velvet HUD: locked to YOU (" + (string)owner + ") — my description carries your agent key now. Take copies of me (or of the client HUD) and hand them out: they will buy only from you, profit only you, and brand themselves as your client's HUD.");
            armTicker();
        } else if (msg == "NOT NOW") {
            llOwnerSay("Velvet HUD: L$" + (string)pendingL + " of yours is unclaimed — touch me again to collect. The ledger ticks every two minutes.");
            armTicker();
        }
    }

    http_response(key id, integer status, list meta, string body) {
        if (id != payoutId) return;

        if (status == 404) {
            knowsPerformer = FALSE;
            if (waitManual) {
                waitManual = 0;
                armTicker();
                llOwnerSay("Velvet HUD: " + residentName + " is not on the roster. Touch me → 'Register' to sign up in one tap.");
            }
            return;
        }
        knowsPerformer = TRUE;

        if (status != 200) {
            siteDownSay();
            string err = llJsonGetValue(body, ["error"]);
            if (err == "" || err == JSON_INVALID) err = "Could not reach the site (HTTP " + (string)status + ").";
            if (waitManual) { waitManual = 0; armTicker(); llOwnerSay("Velvet HUD: " + err); }
            return;
        }
        siteUpSay();

        pendingUSD = (float)llJsonGetValue(body, ["pendingUSD"]);
        pendingL   = (integer)llJsonGetValue(body, ["pendingL"]);
        string canonical = llJsonGetValue(body, ["resident"]);
        if (canonical != "" && canonical != JSON_INVALID) residentName = canonical;
        body = "";                                // the ledger text is parsed out — free it
        remember("ledger reply");

        if (menuPending) {
            menuPending = FALSE;                  // the touch asked for a fresh menu — open it with this figure
            waitMenu();
            return;
        }

        if (!waitManual) {
            // The instant-collectibility alert: speak only when this amount is new.
            if (pendingL >= 1 && pendingL != lastAlertL) {
                lastAlertL = pendingL;
                llOwnerSay("Velvet HUD: L$" + (string)pendingL + " ($" + (string)pendingUSD + ") is collectible — touch me and hit 'Escorts' payouts' to take it in-world.");
            }
            return;
        }
        waitManual = 0;

        if (pendingL < 1) {
            llOwnerSay("Velvet HUD: nothing unclaimed right now — every new sale or commission puts your share back on the HUD.");
            armTicker();
            return;
        }
        state collect;
    }

    changed(integer change) {
        if (change & CHANGED_OWNER) llResetScript();
    }
}

// ── register: the one-tap performer sign-up, ASKING HER LIMITS ───────────────

state register {
    state_entry() {
        closeListen();
        dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
        llDialog(owner,
            "One tap signs you up.\nChoosing a job below confirms: I am an adult, this avatar is mine,\nand I consent to it being built into an AI girlfriend character.",
            ["Stripper", "Escort", "Dancer", "Other", "Not now"],
            DIALOG_CHANNEL);
        llSetTimerEvent(90.0);
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        llSetTimerEvent(0.0);
        closeListen();

        if (msg == "Not now") {
            llOwnerSay("Velvet HUD: no problem — touch again whenever you are ready.");
            state default;
            return;
        }

        regGig = llToLower(msg);
        state register_limits;
    }

    touch_start(integer n) {           // touch re-opens the sign-up menu — no state is ever a dead end
        state register;
    }

    timer() {
        llSetTimerEvent(0.0);
        closeListen();
        llOwnerSay("Velvet HUD: sign-up timed out — touch again when you are ready.");
        state default;
    }
}

state register_limits {
    state_entry() {
        // llTextBox only delivers its answer to an ACTIVE listen on the same channel,
        // and the previous state closed its listen. Without this line the box never
        // answers and she sits here for the full 300 s.
        closeListen();
        dlgListen = llListen(DIALOG_CHANNEL, "", owner, "");
        llTextBox(owner,
            "Your LIMITS — what must NEVER appear in your generated pictures or videos?\nThese rule over every render and every client request.\nExample: nothing below the waist, never remove the mask, no other people in frame.\nLeave empty and press Submit if the tasteful default is enough.",
            DIALOG_CHANNEL);
        llSetTimerEvent(300.0);
    }

    listen(integer channel, string name, key id, string msg) {
        if (channel != DIALOG_CHANNEL || id != owner) return;
        llSetTimerEvent(0.0);
        closeListen();
        regLimits = sane(msg);

        string body = llJsonSetValue("{}", ["stageName"], residentName);
        body = llJsonSetValue(body, ["resident"], residentName);
        body = llJsonSetValue(body, ["uuid"], (string)owner);      // the agent key comes free
        body = llJsonSetValue(body, ["gig"], regGig);
        body = llJsonSetValue(body, ["limits"], regLimits);
        body = llJsonSetValue(body, ["look"], "auto-registered from the Velvet HUD — screenshots to follow");
        body = llJsonSetValue(body, ["consent"], "on");
        body = llJsonSetValue(body, ["hud"], "1");

        regLimits = "";   // the limit text rode on the body — free it now
        rosterId = llHTTPRequest(SITE_URL + "/api/performers",
            [HTTP_METHOD, "POST", HTTP_MIMETYPE, "application/json",
             HTTP_BODY_MAXLENGTH, 1024], body);   // the answer is an id: 1024, not 3072
        body = "";        // llHTTPRequest already copied it — drop the local copy too
        state register_post;
    }

    touch_start(integer n) {           // touch re-opens the limits box
        state register_limits;
    }

    timer() {
        llSetTimerEvent(0.0);
        closeListen();
        llOwnerSay("Velvet HUD: sign-up timed out — touch again when you are ready.");
        state default;
    }
}

state register_post {
    state_entry() {
        llSetTimerEvent(45.0);
    }

    touch_start(integer n) {           // the sign-up POST is in flight: reassure, never interrupt
        llOwnerSay("Velvet HUD: your sign-up is reaching the site — I will report the moment it answers.");
    }

    timer() {
        llSetTimerEvent(0.0);
        llOwnerSay("Velvet HUD: the site did not answer — check " + SITE_URL + " and touch again.");
        state default;
    }

    http_response(key id, integer status, list meta, string body) {
        if (id != rosterId) return;
        llSetTimerEvent(0.0);

        if (status == 200) {
            string pid = llJsonGetValue(body, ["performer", "id"]);
            llOwnerSay("Velvet HUD: " + residentName + " is on the roster — id " + pid + ". Your LIMITS are stored and rule every render."
                + " Touch me for 'Lock to me' when you want client copies that buy only from you.");
        } else {
            string err = llJsonGetValue(body, ["error"]);
            if (err == "" || err == JSON_INVALID) err = "HTTP " + (string)status;
            llOwnerSay("Velvet HUD: sign-up failed — " + err);
        }
        state default;
    }
}

// ── collect: the funding sender pays her in L$, the claim closes the revenue ──

state collect {
    state_entry() {
        if (pairListen == 0) pairListen = llListen(PAIR_CHANNEL, "", NULL_KEY, "");
        llShout(PAIR_CHANNEL, "VELVET|PAY|" + residentName + "|" + (string)pendingL + "|" + (string)pendingUSD);
        llSetTimerEvent(20.0);      // pair window: no sender answered
    }

    touch_start(integer n) {           // touch re-broadcasts the collect call — a sender may have walked in since
        state collect;
    }

    timer() {
        llSetTimerEvent(0.0);
        if (pairListen) { llListenRemove(pairListen); pairListen = 0; }
        llOwnerSay("Velvet HUD: no funding sender answered in range. Your share stays collectible — touch again when one is close.");
        state default;
    }

    listen(integer channel, string name, key senderId, string msg) {
        list parts = llParseStringKeepNulls(msg, ["|"], []);
        if (llList2String(parts, 0) != "VELVET" || llList2String(parts, 1) != "PAID") return;

        if (pairListen) { llListenRemove(pairListen); pairListen = 0; }
        llSetTimerEvent(0.0);
        llOwnerSay("Velvet HUD: collected L$" + llList2String(parts, 2) + ". That revenue is closed on the site — the next alert is already around the corner.");
        state default;
    }
}

// ────────────────────────────────────────────────────────────────────────────
// FILE: velvet_dispatcher.lsl
// DISPENSER — delivers copies, signs the claim/lock handshake, posts the delivery back.
// ────────────────────────────────────────────────────────────────────────────

// velvet_dispatcher.lsl — the STUDIO's HUD dispenser: website → in-world delivery.
//
// A client buys HER personal client HUD on her profile page on the site, naming the
// avatar that will receive it. The order lands on the site's pending queue; this object
// (rezzed in the club region by the studio owner) polls that queue, finds the requester
// with a sensor sweep, hands her a copy of the client-HUD object (kept inside this
// object's inventory), and stands by for the HUD's attach-time call:
//
//     VELVET|CLAIMME|<copy object key>          (shouted by the HUD on the lock channel)
//     VELVET|CLAIMYOU|<client key>              (nudge, for a copy the client ALREADY wears)
//
// For a copy owned by the very client who is on the queue, it answers:
//     VELVET|LOCK|<objKey>|<escortUuid>|<sig>   (sig = SHA-256(claim key|escort|obj|client))
//
// The HUD verifies that exact signature, stamps HER agent key into its own description
// (the encoding), rebrands as "Velvet — <her>'s Client", and only her ledger profits from
// every L$ spent through that copy. After the lock handshake the dispenser posts the
// client's UUID back to the site: the order is closed as delivered and leaves the queue.
//
// Anti-spoof, three layers: the queue answers only with claim-key-sealed calls; the
// signature needs the claim key back in the agent's own script; and a lock is only ever
// issued for a copy OWNED by the avatar named on the order — a random unlocked copy in
// the region can claim, never receive.
//
// One-time setup (studio owner):
//   1. DROP this script in any object you rez in the work region.
//   2. Copy the READY-TO-GO CLIENT HUD prim into that SAME object's inventory and name
//      the inventory item EXACTLY "VELVET HUD (CLIENTS)" — that string is HUD_ITEM below,
//      and a prim named anything else (including the old "Velvet Client HUD" this header
//      used to ask for) is never found, so no order can ever be delivered. Leave it
//      unlocked with a blank description: the encoder is the lock handshake, not the copy.
//   3. SITE_URL must equal SLGF_PUBLIC_URL; CLAIM_KEY must equal SLGF_CLAIM_SECRET — the
//      same claim key every rail script already carries.
//
// HEAP: the queue answer is parsed twice (string, then list). Both are dropped the moment
// the single row being worked has been read, because a dispenser that collides stops
// delivering and says nothing.

string SITE_URL  = "https://dubwarz.vip";
string CLAIM_KEY = "GETJINXED_COMMON_TEST";   // = server SLGF_CLAIM_SECRET

integer LOCK_CHANNEL = -7031195;              // the claim/lock channel, same constant in velvet_client_hud.lsl
integer POLL_SECONDS  = 45;                   // queue poll cadence; a sensor sweep is charged per cycle
integer RETRY_CYCLES = 6;                     // handout cycles to wait for the claimme before standing down (~4.5 min)

string HUD_ITEM       = "VELVET HUD (CLIENTS)";  // must exist in this object's inventory

key    pollId;
key    doneId;

// ── the one pending order being worked, in-world ──
list    cfg;            // [orderId, escortName, escortUuid, avatarName] — the row being delivered
key     clientKey;      // the requester as detected with the sensor sweep
integer haveCopy;       // TRUE once the copy went into her inventory; the lock is not booked yet
integer claimCycles;    // handout retries: llGiveInventory answers nothing, the claim does (or the timer retries)
integer siteDown;       // TRUE while the backend is unreachable — speak once, stay quiet after

// ── small helpers (globals only, so states stay cheap) ──

/** HEAP TELEMETRY: a collision is silent until it kills the dispenser, so print the
    number while it is still alive and name the point in the flow that spent it. */
remember(string where) {
    integer free = llGetFreeMemory();
    if (free < 16000) llOwnerSay("Velvet dispenser: " + where + " left " + (string)free + " bytes.");
}

/** The lock signature: the claim key plus all four concrete parties of the exchange. */
string lockSig(string escortUuid, string objKey, string whoKey) {
    return llSHA256String(CLAIM_KEY + "|" + escortUuid + "|" + objKey + "|" + whoKey);
}

/** Pull the next pending row off the site (claim-key sealed). */
askQueue() {
    pollId = llHTTPRequest(SITE_URL + "/api/hud-orders/pending",
        [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
         HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
         HTTP_BODY_MAXLENGTH, 3072], "");   // a row is 4 short fields; 4096 was 1 KB reserved for nothing
}

/** Stand down from the row being worked. LOCAL ONLY — this does not close the order on
    the site: only a signed claim POSTed to /api/hud-orders/delivered does that. An order
    dropped here stays pending and is picked up again on a later cycle, which is the
    correct outcome when nobody claimed the copy. */
markDelivered() {
    cfg = [];
    clientKey = NULL_KEY;
    haveCopy = FALSE;
    claimCycles = 0;
}

default {
    state_entry() {
        llListen(LOCK_CHANNEL, "", NULL_KEY, "");
        llSetTimerEvent(POLL_SECONDS);
        llOwnerSay("Velvet dispenser: watching the HUD-order queue every " + (string)POLL_SECONDS + "s — drop a '" + HUD_ITEM + "' prim in my inventory and I will deliver it.");
    }

    changed(integer change) {
        if (change & CHANGED_INVENTORY) {
            if (llGetInventoryType(HUD_ITEM) != INVENTORY_OBJECT) {
                llOwnerSay("Velvet dispenser: my inventory has no '" + HUD_ITEM + "' prim yet — put one in (any unlocked copy renamed to that exact name).");
            } else {
                llOwnerSay("Velvet dispenser: '" + HUD_ITEM + "' ready in inventory — I can deliver once an order arrives.");
            }
        }
    }

    timer() {
        if (!haveCopy) {
            askQueue();
            return;
        }
        // A copy is out but never claimed (she left the region, rez failed, HUD script
        // missing): back off to polling, the order stays pending on the site.
        claimCycles++;
        if (claimCycles >= RETRY_CYCLES) {
            llOwnerSay("Velvet dispenser: no claim from " + llList2String(cfg, 3) + " after " + (string)claimCycles + " cycles — standing down; the order stays pending and I will sweep again.");
            markDelivered();
        }
    }

    http_response(key id, integer status, list meta, string body) {
        if (id == pollId) {
            pollId = NULL_KEY;
            if (status != 200) {
                // delivery just pauses: an order pending on a dark site is not lost,
                // and the money for it is still on the site, not in a stranger's hands.
                if (!siteDown) {
                    siteDown = TRUE;
                    llOwnerSay("Velvet dispenser: the site is unreachable — HUD deliveries are on hold until it answers. Pending orders stand.");
                }
                return;
            }
            if (siteDown) { siteDown = FALSE; llOwnerSay("Velvet dispenser: the site is back — delivery resumes on the next cycle."); }
            list arr = llJson2List(llJsonGetValue(body, ["orders"]));
            if (!llGetListLength(arr)) { body = ""; arr = []; return; }

            // One order per cycle: take the oldest row.
            string who = llList2String(arr, 0);
            cfg   = [ llJsonGetValue(who, ["orderId"]),
                      llJsonGetValue(who, ["escortName"]),
                      llJsonGetValue(who, ["escortUuid"]),
                      llJsonGetValue(who, ["avatarName"]) ];
            string want = llList2String(cfg, 3);
            // the row is read: free the body and the parsed list before the sweep
            body = "";
            arr = [];
            who = "";
            remember("queue reply");
            llOwnerSay("Velvet dispenser: HUD order '" + (string)llList2String(cfg, 0) + "' — looking for " + want + " in region…");
            llSensor("", NULL_KEY, AGENT, 96, PI);   // find her before anything is handed out
            return;
        }
        if (id == doneId) {
            doneId = NULL_KEY;
            if (status == 200) {
                llOwnerSay("Velvet dispenser: order closed on the site — " + llList2String(cfg, 1) + "'s personal HUD is delivered to " + llList2String(cfg, 3) + ".");
                markDelivered();
                llSetTimerEvent(POLL_SECONDS);
                return;
            }
            // The order is still pending on the site, so the dispenser must NOT go quiet:
            // the old code stopped the timer here and only said it would re-ask, which
            // left the object alive, listening and polling nothing until it was reset.
            llOwnerSay("Velvet dispenser: the site refused the delivery confirmation (HTTP " + (string)status + ") — the order stays pending and I will re-ask.");
            markDelivered();
            llSetTimerEvent(POLL_SECONDS);
            return;
        }
    }

    /** Sensor sweep resolution: her, or nobody. */
    sensor(integer n) {
        if (!llGetListLength(cfg)) return;
        string want = llToLower(llList2String(cfg, 3));
        integer at = -1;
        integer i;
        for (i = 0; i < n && at == -1; i++) {
            string avName = llToLower(llKey2Name(llDetectedKey(i)));
            if (avName == want || (llStringLength(want) > 9 && llSubStringIndex(avName, llGetSubString(want, 0, 9)) == 0)) at = i;
        }
        if (at == -1) {
            llOwnerSay("Velvet dispenser: " + llList2String(cfg, 3) + " is not in this region — the order stays pending, I will sweep again next cycle.");
            cfg = [];
            return;
        }
        clientKey = llDetectedKey(at);

        if (llGetInventoryType(HUD_ITEM) != INVENTORY_OBJECT) {
            llOwnerSay("Velvet dispenser: no '" + HUD_ITEM + "' in my inventory — the order for " + llList2String(cfg, 3) + " stays pending. Studio owner, add the prim.");
            cfg = [];
            clientKey = NULL_KEY;
            return;
        }
        // llGiveInventory returns void — there is no success flag to branch on.
        // The claim handshake below is the real confirmation: either the copy calls in
        // (lock signed, order closed) or the timer stands down after RETRY_CYCLES.
        llGiveInventory(clientKey, HUD_ITEM);
        haveCopy = TRUE;
        claimCycles = 0;
        llOwnerSay("Velvet dispenser: handed a client HUD to " + llList2String(cfg, 3) + " (" + (string)clientKey + ") — waiting for her to attach and claim the lock. If she already wears an unlocked one, it should call in a moment.");
        // Nudge for the copy the client may ALREADY be wearing: the HUD re-broadcasts
        // its claim on hearing its owner addressed on the lock channel.
        llShout(LOCK_CHANNEL, "VELVET|CLAIMYOU|" + (string)clientKey);
    }

    no_sensor() {
        if (llGetListLength(cfg)) {
            llOwnerSay("Velvet dispenser: nobody matching " + llList2String(cfg, 3) + " here — order stays pending, sweep again next cycle.");
            cfg = [];
        }
    }

    /** The HUD's attach-time claim: lock it, but only for the ordered client. */
    listen(integer channel, string name, key senderId, string msg) {
        if (channel != LOCK_CHANNEL) return;
        list parts = llParseStringKeepNulls(msg, ["|"], []);
        string cmd = llList2String(parts, 1);

        if (cmd == "CLAIMME") {
            if (!llGetListLength(cfg)) return;                       // nothing being delivered right now
            string objKey    = llList2String(parts, 2);
            key    copyOwner = llGetOwnerKey((key)objKey);           // who owns that copy?
            if (copyOwner != clientKey) return;                      // a stranger's copy never gets a lock

            string escortUuid = llList2String(cfg, 2);
            if (escortUuid == "") { llOwnerSay("Velvet dispenser: order has no escort agent key on file — lock refused."); return; }

            // The signature is the site-side promise: SHA-256 over claim key + escort + copy + client.
            string sig = lockSig(escortUuid, objKey, (string)clientKey);
            llShout(LOCK_CHANNEL, "VELVET|LOCK|" + objKey + "|" + escortUuid + "|" + sig);
            llOwnerSay("Velvet dispenser: lock signed for " + llList2String(cfg, 3) + " — her copy now encodes " + llList2String(cfg, 1) + "'s agent key in its description.");

            string claim = llJsonSetValue("{}", ["orderId"], llList2String(cfg, 0));
            claim = llJsonSetValue(claim, ["clientUuid"], (string)clientKey);
            doneId = llHTTPRequest(SITE_URL + "/api/hud-orders/delivered",
                [HTTP_METHOD, "POST", HTTP_MIMETYPE, "application/json",
                 HTTP_CUSTOM_HEADER, "x-claim-key", CLAIM_KEY,
                 HTTP_BODY_MAXLENGTH, 1024],
                claim);
            claim = "";
            llSetTimerEvent(0.0);       // wait for the delivery POST before the next sweep
            return;
        }

        if (cmd == "CLAIMYOU") return;  // our own shout back — ignore
    }
}

// ────────────────────────────────────────────────────────────────────────────
// FILE: heap_probe.lsl
// DIAGNOSTIC — free bytes, LSO/Mono verdict, one PROBE line. Not part of the product.
// ────────────────────────────────────────────────────────────────────────────

// heap_probe.lsl — drop this in the object that reports "Stack-Heap Collision", then
// touch it (or reset it) and read the local chat. It answers the one question that
// decides the fix, which no amount of reading a source file can answer:
//
//   how much memory does this PRIM actually give a script, and is the script Mono?
//
// LSO (the old compiler) gives a script 16 KB total, shared between bytecode, stack and
// heap. Mono gives 64 KB. A client/server HUD of a few thousand lines fits Mono and
// cannot fit LSO — so an old object still compiling LSO collides on a script that is
// perfectly healthy in Mono, and no amount of trimming brings 40 KB of source under
// 16 KB.
//
// The same probe distinguishes the other half of the trap: a collision is STICKY. The
// server remembers the failed script state, so re-saving the same script into the same
// item keeps crashing until the script is reset (or the prim is duplicated in-world,
// not in inventory). If free memory reads healthy here while the other script still
// crashes, that script's saved state is the problem, not its budget.

report(string where) {
    integer free = llGetFreeMemory();
    string budget;
    string tag;
    if (free < 16384) { budget = "LSO — 16 KB ceiling, this is why it collides"; tag = "LSO"; }
    else if (free < 40000) { budget = "Mono, but tight"; tag = "MONO-TIGHT"; }
    else { budget = "Mono — 64 KB ceiling, plenty"; tag = "MONO"; }
    llOwnerSay("probe[" + where + "] '" + llGetScriptName() + "': "
        + (string)free + " B free · " + budget);
    // Which sibling scripts are in this prim: the crashed item is named in the error.
    integer total = llGetInventoryNumber(INVENTORY_SCRIPT);
    integer i;
    string names = "";
    for (i = 0; i < total; i++) {
        if (i) names += "|";
        names += llGetInventoryName(INVENTORY_SCRIPT, i);
    }
    // One line, no decoration: paste this back and it settles the diagnosis.
    llOwnerSay("PROBE free=" + (string)free + " verdict=" + tag
        + " scripts=" + (string)total + " [" + names + "]");
}

default {
    state_entry() {
        report("state_entry");
    }

    touch_start(integer n) {
        report("touch");
    }
}
