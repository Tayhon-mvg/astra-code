"""Render the offline demo into docs/demo.svg + docs/demo.html + docs/demo.txt."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from astra.ui.demo import run_demo  # noqa: E402


def main() -> None:
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    console = Console(
        record=True,
        width=96,
        force_terminal=True,
        color_system="truecolor",
        file=open(docs / "demo.ansi", "w", encoding="utf-8", errors="replace"),
    )
    asyncio.run(run_demo(console, theme="aurora", live_enabled=False))
    console.save_svg(str(docs / "demo.svg"), title="Astra Code — offline demo", clear=False)
    console.save_html(str(docs / "demo.html"), inline_styles=True, clear=False)

    html = (docs / "demo.html").read_text(encoding="utf-8")
    html = html.replace(
        "<body>",
        '<body style="background:#0b1020;color:#e2e8f0;padding:24px;">',
    )
    html = html.replace(
        "</head>",
        "<style>pre{white-space:pre-wrap;font-size:13px;line-height:1.35;} "
        "body{font-family:'Cascadia Code',Consolas,monospace;}</style></head>",
    )
    (docs / "demo.html").write_text(html, encoding="utf-8")

    # Plain-text version for the README block.
    plain = console.export_text(styles=False, clear=False)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    (docs / "demo.txt").write_text(plain, encoding="utf-8")
    console.file.close()
    print(f"wrote {docs / 'demo.svg'}")
    print(f"wrote {docs / 'demo.html'}")
    print(f"wrote {docs / 'demo.txt'} ({len(plain)} chars)")


if __name__ == "__main__":
    main()
