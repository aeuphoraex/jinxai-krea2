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

// THE BASE, NOT THE BARE DOMAIN. The site answers under `/aigf` (Caddy strips it before the
// app sees the path), so `https://dubwarz.vip/api/wallet` is a 404 from Caddy's file server
// while `https://dubwarz.vip/aigf/api/wallet` is the route. Every URL below is this base plus
// `/api/...`, and the order half opens pages from the same base. For a direct modal.run
// install, use `https://xzyorb--orbital.us-east.modal.run` with no suffix.
string SITE_URL     = "https://dubwarz.vip/aigf";
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
/** The wallet URL carries the resident's NAME beside the key.

    The key is what the server books against, but the name is what an operator can read out
    of a chat log. The server records the pair on every read, so an L$ payment that landed
    while no wallet row existed can still be traced back to a client and credited.
*/
string walletUrl() {
    return SITE_URL + "/api/wallet?client=" + llEscapeURL((string)owner)
        + "&name=" + llEscapeURL(residentName);
}

askWallet() {
    walletId = llHTTPRequest(walletUrl(),
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
        healthId = llHTTPRequest(walletUrl(),
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
