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
