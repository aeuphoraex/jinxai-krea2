"""LSL sanity checks that PyCharm-free editing keeps losing: braces, function order,
handler placement, text-box listens and the heap held by HTTP buffers.

LSL has no forward declarations, so a helper defined below its first caller is a compile
error — exactly the shape a Stack-Heap-Collision repair leaves behind when the body is
reordered. Run:  python tools/lsl_lint.py lsl/*.lsl
"""
import re
import sys
from pathlib import Path


def strip(src: str) -> str:
    """Remove comments and string literals, keeping offsets aligned with the original."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src[i:i + 2] == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j; continue
        if src[i:i + 2] == "/*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(c if c == "\n" else " " for c in src[i:j])); i = j; continue
        if src[i] == '"':
            j = i + 1
            while j < n:
                if src[j] == "\\": j += 2; continue
                if src[j] == '"': j += 1; break
                j += 1
            out.append('""' + " " * (j - i - 2)); i = j; continue
        out.append(src[i]); i += 1
    return "".join(out)


def depth_at(code: str, offset: int) -> int:
    return code.count("{", 0, offset) - code.count("}", 0, offset)


def check(path: Path) -> int:
    # A generated bundle holds several whole scripts in one document on purpose (LSL needs one
    # script per prim item), so it has five `default` states and helpers that belong to
    # different files. Linting it would report every cross-file reference as a fault.
    if path.name.endswith("_ALL.lsl"):
        print(f"{path}\n  skipped: generated bundle — lint the individual scripts instead")
        return 0
    src = path.read_text(encoding="utf-8")
    code = strip(src)
    problems = []

    if code.count("{") != code.count("}"):
        problems.append("braces %d open / %d close" % (code.count("{"), code.count("}")))
    if code.count("(") != code.count(")"):
        problems.append("parens %d open / %d close" % (code.count("("), code.count(")")))

    # user functions: `name(args) {` at brace depth 0
    defs = {}
    for m in re.finditer(r"\n([A-Za-z_]\w*)\s*\(([^)]*)\)\s*\{", code):
        if depth_at(code, m.start()) == 0:
            defs[m.group(1)] = m.start()

    for name, at in defs.items():
        for call in re.finditer(r"\b%s\s*\(" % re.escape(name), code):
            if call.start() - 1 == at + 1:      # the definition itself
                continue
            if call.start() < at:
                problems.append("'%s' called at %d but defined at %d (LSL needs it first)"
                                % (name, call.start(), at))
                break

    # event handlers and user states must live inside a state block
    # LSL demands a `default` state, and any `state foo {` before it is a syntax error
    if not re.search(r"^default[ \t]*\{", code, re.M):
        problems.append("no `default` state — LSL rejects every named state before one exists")

    # [ \t]* not \s*: \s would swallow the newline before the state and read as an indent
    for m in re.finditer(r"^([ \t]*)(default|state\s+(\w+))[ \t]*\{", code, re.M):
        if m.group(1):
            problems.append("state %r is indented — nested states never compile" % m.group(2))

    # every llTextBox needs an active listen opened before it in the same state block
    for m in re.finditer(r"\bllTextBox\s*\(", code):
        start = code.rfind("state_entry", 0, m.start())
        block = code[start:m.start()] if start != -1 else ""
        if "llListen" not in block:
            problems.append("llTextBox at %d has no llListen before it — the box never answers"
                            % m.start())

    # A dialog silently drops every button past the twelfth and truncates a label at
    # 24 bytes, so a literal button list is worth checking before it ships.
    for m in re.finditer(r"\[\s*((?:\"[^\"]*\"\s*(?:,\s*|$))+)\]", code):
        window = code[max(0, m.start() - 500):m.start()]
        if "llDialog" not in window and "llDialog" not in code[m.end():m.end() + 200]:
            continue
        labels = re.findall(r'"([^"]*)"', m.group(1))
        if len(labels) > 12:
            problems.append("dialog with %d literal buttons at %d — LSL shows 12"
                            % (len(labels), m.start()))
        for label in labels:
            if len(label) > 24:
                problems.append("dialog label %r is %d bytes at %d — LSL cuts at 24"
                                % (label, len(label), m.start()))
        if len(set(labels)) != len(labels):
            problems.append("duplicate dialog labels at %d — the reply cannot say which"
                            % m.start())

    # a state that arms a timer without a timer() handler never fires it
    for st in re.finditer(r"^(?:state (\w+) \{|default \{)(.*?)(?=^state |^default \{|\Z)",
                          code, re.S | re.M):
        name = st.group(1) or "default"
        if "llSetTimerEvent(" in st.group(2) and "timer(" not in st.group(2):
            problems.append("state %r calls llSetTimerEvent but defines no timer()" % name)

    # heap held by HTTP buffers, worst case per state
    buffers = [(int(m.group(1)), m.start()) for m in
               re.finditer(r"HTTP_BODY_MAXLENGTH,\s*(\d+)", code)]
    for size, at in buffers:
        if size > 4096:
            problems.append("HTTP_BODY_MAXLENGTH %d at %d exceeds LSL's 4096 ceiling" % (size, at))
    print("  buffers: %s  total %d B" % ([b for b, _ in buffers], sum(b for b, _ in buffers)))

    for p in problems:
        print("  PROBLEM: " + p)
    return len(problems)


def main(argv):
    files = [Path(a) for a in argv] or sorted(Path("lsl").glob("*.lsl"))
    bad = 0
    for path in files:
        print(path.as_posix())
        bad += check(path)
    print("clean" if not bad else "%d problem(s)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
