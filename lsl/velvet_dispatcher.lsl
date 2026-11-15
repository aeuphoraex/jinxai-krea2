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
