// Velvet notecard giver — the full https://dubwarz.vip/ link, in the one place chat cannot block it.
//
// Her in-world chat is guarded (anything with a URL scheme is refused before it is published),
// so the address she can say is the bare domain. A notecard is not chat: it carries the real
// link, the prices and the order steps, and it survives being copied into someone's inventory.
//
// Drop this script plus a notecard named exactly NOTECARD into the same prim (the HUD, a kiosk,
// a sign) and it hands that notecard out on touch or when someone says "velvet" nearby.
//
// Memory: no lists, no string building per event — two scalars, so it cannot collide the stack.

string  NOTECARD        = "Velvet — Price List and Gallery";
integer COOLDOWN        = 60;      // seconds per avatar
float   HEAR_RADIUS     = 10.0;    // how close someone has to be to be heard
string  PROMPT_TEXT     = "Velvet: touch for the price list and the gallery link.";

integer gPublicChannel  = 0;
key     gLastAvatar     = NULL_KEY;
integer gLastGiven      = 0;

integer ready()
{
    return llGetInventoryType(NOTECARD) == INVENTORY_NOTECARD;
}

// One action for both entry points: give the card, or say why it cannot.
give(key avatar)
{
    integer now = llGetUnixTime();
    if (avatar == gLastAvatar && now - gLastGiven < COOLDOWN)
    {
        llRegionSayTo(avatar, 0, "One moment — the card is already on its way to you.");
        return;
    }
    if (!ready())
    {
        // Never fail silently: the missing card is a build error, not the visitor's problem.
        llRegionSayTo(avatar, 0, "The price list is being restocked. The gallery is at dubwarz.vip.");
        llOwnerSay("[velvet] no notecard named '" + NOTECARD + "' in this object's contents.");
        return;
    }
    gLastAvatar = avatar;
    gLastGiven  = now;
    llGiveInventory(avatar, NOTECARD);
    llRegionSayTo(avatar, 0, "Velvet price list sent — the gallery link is in it.");
}

default {
    state_entry()
    {
        llListen(gPublicChannel, "", NULL_KEY, "");
        llSetText("VELVET\ntouch for prices + gallery", <1.0, 0.85, 1.0>, 1.0);
        if (!ready())
        {
            llOwnerSay("[velvet] '" + NOTECARD + "' is not in this object yet — touch will fall back to text.");
        }
    }

    touch_start(integer count)
    {
        integer i;
        for (i = 0; i < count; i++)
        {
            give(llDetectedKey(i));
        }
    }

    listen(integer channel, string name, key id, string message)
    {
        // The bot says the bare domain in chat; whoever is curious says "velvet" back.
        if (llVecDist(llGetPos(), llList2Vector(llGetObjectDetails(id, [OBJECT_POS]), 0)) > HEAR_RADIUS)
        {
            return;
        }
        string low = llToLower(message);
        if (llSubStringIndex(low, "velvet") == -1 && llSubStringIndex(low, "price list") == -1)
        {
            return;
        }
        give(id);
    }

    changed(integer change)
    {
        // A notecard dropped in after the script started must start working immediately.
        if (change & CHANGED_INVENTORY)
        {
            llOwnerSay("[velvet] contents changed — notecard " + (string)(ready() != 0 ? "ready" : "missing"));
        }
    }

    on_rez(integer start)
    {
        llResetScript();
    }
}
