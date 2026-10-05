"""Keep the HTML preview self-contained while maintaining separate source files."""
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "hub/static"


def embed():
    path = STATIC / "index.html"
    original_html = path.read_text(encoding="utf-8")
    html = original_html
    assets = [
        ("styles", "style.css", "style", '<link rel="stylesheet" href="./style.css">'),
        ("script", "app.js", "script", '<script src="./app.js" defer></script>'),
    ]
    for name, filename, tag, original in assets:
        content = (STATIC / filename).read_text(encoding="utf-8").rstrip("\n")
        content = re.sub(rf"</{tag}", lambda match: "<\\/" + match.group()[2:], content, flags=re.I)
        start, end = f"<!-- hub:{name}:start -->", f"<!-- hub:{name}:end -->"
        block = f'{start}\n  <{tag} id="hub-{name}">\n{content}\n  </{tag}>\n  {end}'
        # JavaScript runs after the document's elements have been parsed.
        if name == "script" and start not in html:
            if original not in html:
                raise RuntimeError("Script source placeholder is missing")
            html = html.replace("  " + original + "\n", "")
            html = html.replace("</body>", "  " + block + "\n</body>")
        elif start in html:
            pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
            html = pattern.sub(lambda _: block, html, count=1)
        elif original in html:
            html = html.replace(original, block, 1)
        else:
            raise RuntimeError(f"{name} source placeholder is missing")
    if html != original_html:
        path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    embed()
