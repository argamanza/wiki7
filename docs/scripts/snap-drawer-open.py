#!/usr/bin/env python3
"""Fetch the home page, force the drawer <details> open, save to a temp
file, then screenshot it. CSS/JS still resolve via a <base> tag pointing
back at the wiki, so absolute and relative resource refs both work.

Useful for PR-review evidence on a re-fork: the drawer-open state is
otherwise hard to capture without a CDP-scripted browser.

Usage:
    python3 snap-drawer-open.py [output.png]

Defaults: hits http://localhost:8080/ and writes
/tmp/wiki7-shots/drawer_open.png. The Chrome path below is the
macOS default; override CHROME_BIN env var on other platforms.

See docs/wiki7-skin-customization.md for context.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

WIKI_URL = os.environ.get("WIKI_URL", "http://localhost:8080/")
SHOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/wiki7-shots/drawer_open.png")
TEMP_HTML = SHOT.with_suffix(".html")
CHROME = os.environ.get(
    "CHROME_BIN",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
SHOT.parent.mkdir(parents=True, exist_ok=True)


def main() -> int:
    html = subprocess.check_output(["curl", "-s", WIKI_URL]).decode()

    # Inject <base> so absolute/relative resource refs resolve back to the wiki.
    html = html.replace(
        "<head>",
        '<head><base href="http://localhost:8080/">',
        1,
    )

    # Force the *drawer* <details> open (NOT the search one). Both use the same
    # class; the drawer summary is the one whose aria-details points at
    # wiki7-drawer__card. Use a tolerant regex on the surrounding block.
    pattern = re.compile(
        r'<details class="wiki7-dropdown-details">\s*'
        r'(<summary[^>]*aria-details="wiki7-drawer__card")',
        re.DOTALL,
    )
    new_html, n = pattern.subn(
        r'<details class="wiki7-dropdown-details" open>\1',
        html,
        count=1,
    )
    if n != 1:
        print(f"WARN: expected to rewrite 1 drawer details, rewrote {n}", file=sys.stderr)
        return 2

    TEMP_HTML.write_text(new_html, encoding="utf-8")

    proc = subprocess.run(
        [
            CHROME,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--window-size=1440,2000",
            f"--screenshot={SHOT}",
            f"file://{TEMP_HTML}",
        ],
        capture_output=True,
        text=True,
    )
    if not SHOT.exists():
        print("STDERR:", proc.stderr, file=sys.stderr)
        return 1
    print(f"OK: {SHOT} ({SHOT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
