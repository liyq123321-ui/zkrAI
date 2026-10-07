#!/usr/bin/env python3
"""Render Mermaid diagrams to HTML, SVG, or PNG.

HTML format uses Mermaid CDN for client-side rendering (no dependencies).
SVG/PNG formats use the mermaid.ink service.

Usage:
    python render_mermaid.py --input diagram.mmd --output diagram.html --format html
    python render_mermaid.py --input diagram.mmd --output diagram.svg --format svg
    python render_mermaid.py --input diagram.mmd --output diagram.png --format png
    echo "graph TD; A-->B" | python render_mermaid.py --output diagram.html --format html -
"""

import argparse
import base64
import sys
import urllib.parse
import urllib.request
from pathlib import Path

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"
MERMAID_INK_BASE = "https://mermaid.ink"


def read_input(input_path: str | None) -> str:
    if input_path is None or input_path == "-":
        return sys.stdin.read()
    return Path(input_path).read_text(encoding="utf-8")


def render_html(code: str, output_path: str) -> None:
    """Generate a self-contained HTML file with Mermaid CDN."""
    encoded = code.replace("</script>", "<\\/script>")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mermaid Diagram</title>
    <style>
        body {{
            margin: 0;
            padding: 24px;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            min-height: 100vh;
            background: #f8f9fa;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }}
        .container {{
            background: white;
            padding: 32px;
            border-radius: 12px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.08);
            max-width: 95vw;
            overflow: auto;
        }}
        .mermaid {{
            text-align: center;
        }}
        .mermaid svg {{
            max-width: 100%;
            height: auto;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="mermaid">
{encoded}
        </div>
    </div>
    <script type="module">
        import mermaid from '{MERMAID_CDN}';
        mermaid.initialize({{ startOnLoad: true, theme: 'default' }});
    </script>
</body>
</html>
"""
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"HTML saved to: {output_path}")


def render_remote(code: str, output_path: str, fmt: str) -> None:
    """Render via mermaid.ink service."""
    encoded = base64.b64encode(
        f'{{"code": {code!r}, "mermaid": {{"theme": "default"}}}}'.encode()
    ).decode()
    url = f"{MERMAID_INK_BASE}/{fmt}/{encoded}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mermaid-skill/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        Path(output_path).write_bytes(data)
        print(f"{fmt.upper()} saved to: {output_path} ({len(data)} bytes)")
    except Exception as e:
        print(f"Error rendering via mermaid.ink: {e}", file=sys.stderr)
        print("Falling back to HTML format...", file=sys.stderr)
        html_path = Path(output_path).with_suffix(".html")
        render_html(code, str(html_path))
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Mermaid diagrams")
    parser.add_argument("--input", "-i", default=None,
                        help="Input .mmd file (default: stdin)")
    parser.add_argument("--output", "-o", required=True,
                        help="Output file path")
    parser.add_argument("--format", "-f", choices=["html", "svg", "png"],
                        default="html", help="Output format (default: html)")
    args = parser.parse_args()

    code = read_input(args.input)

    if args.format == "html":
        render_html(code, args.output)
    elif args.format in ("svg", "png"):
        render_remote(code, args.output, args.format)


if __name__ == "__main__":
    main()
