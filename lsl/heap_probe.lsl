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
