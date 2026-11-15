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

// The base includes the subfolder the site lives under (see velvet_client_wallet.lsl):
// `https://dubwarz.vip/api/...` is a 404 from Caddy, `/aigf/api/...` is the route.
string SITE_URL  = "https://dubwarz.vip/aigf";
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

        // LOCAL PRE-CHECK: a client who cannot afford the order is told here, before any
        // request leaves the prim. The server refuses a short wallet anyway, but a refusal
        // arriving after the popup — or after an L$ debit from an older HUD copy — reads as
        // "I paid and got nothing". This makes the shortfall impossible to discover late.
        if (walletL < priceL) {
            llOwnerSay("Velvet HUD: your wallet has L$" + (string)walletL + " and a " + whatWord()
                + " is L$" + (string)priceL + ". Nothing has been charged — touch me and choose "
                + "TOP UP WALLET first, then order.");
            state default;
            return;
        }

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
        // `/v/<id>` under the base — the server also returns an absolute openUrl once it
        // answers, and absUrl leaves an absolute link untouched.
        lastOpenUrl = absUrl("/v/" + orderId);
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
        polls++;
        if (polls > 40) {
            // NEVER abandon a paid order. A cold GPU start plus a queue can pass ten minutes,
            // and leaving this state used to hide the only live link the client had — which
            // is how "still generating" became a dead end. Slow down instead of stopping.
            if ((polls - 40) % 20 == 1) {
                llOwnerSay("Velvet HUD: still generating — your order stands and your wallet is paid. I check every 30 s until it lands. Your page:\n" + lastOpenUrl);
            }
            pollId = llHTTPRequest(SITE_URL + "/api/commission/" + llEscapeURL(orderId),
                [HTTP_METHOD, "GET", HTTP_MIMETYPE, "application/json",
                 HTTP_BODY_MAXLENGTH, 1024], "");
            llSetTimerEvent(30.0);
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
