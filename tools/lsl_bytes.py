"""What an LSL script costs in the 64 KB Mono heap before it runs a single line.

Everything a script's string literals hold is heap, and every pending llHTTPRequest with
HTTP_BODY_MAXLENGTH reserves its buffer there too. A Stack-Heap Collision is therefore a
budget that was spent at load time; this tool prints the budget per script so the next
repair argues from bytes instead of from prose.

  python tools/lsl_bytes.py lsl/*.lsl
"""
import re
import sys
from pathlib import Path

LITERAL = re.compile(r'"([^"\n]*)"')
DECL = re.compile(r"^(?:string|integer|float|key|list)\s+[^;]+;", re.M)
STATE = re.compile(r"^(?:state \w+ \{|default \{)", re.M)
BUFFER = re.compile(r"HTTP_BODY_MAXLENGTH,\s*(\d+)")


def report(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    lits = LITERAL.findall(src)
    literal_bytes = sum(len(x) + 1 for x in lits)      # Mono keeps the terminating NUL
    buffers = [int(b) for b in BUFFER.findall(src)]
    print("%s" % path.as_posix())
    print("  source %6d B | literals %3d = %5d B | pending-buffer ceiling %5d B"
          % (len(src.encode()), len(lits), literal_bytes, max(buffers) if buffers else 0))
    print("  globals %2d | states %2d | http calls %d"
          % (len(DECL.findall(src)), len(STATE.findall(src)), len(buffers)))
    for text in sorted(lits, key=len, reverse=True)[:5]:
        one_line = text.replace("\n", " ")
        print("    %4d B  %s%s" % (len(text), one_line[:64], "..." if len(one_line) > 64 else ""))


def main(argv):
    files = [Path(a) for a in argv] or sorted(Path("lsl").glob("*.lsl"))
    for path in files:
        report(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
