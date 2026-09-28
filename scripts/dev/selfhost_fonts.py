"""Download the latin + latin-ext woff2 files behind the site's Google Fonts
link and emit a fonts.css with the same @font-face blocks pointing at
/fonts/… — so the rendered glyphs are byte-identical, just served by us.
"""

import re
import sys
import urllib.request
from pathlib import Path

css_path = Path(sys.argv[1])
out_dir = Path(sys.argv[2])
out_css = Path(sys.argv[3])
out_dir.mkdir(parents=True, exist_ok=True)

blocks = re.findall(r"/\* (\w[\w-]*) \*/\s*@font-face \{(.*?)\}", css_path.read_text(encoding="utf-8"), re.S)
keep = {"latin", "latin-ext"}
emitted = []
for subset, body in blocks:
    if subset not in keep:
        continue
    family = re.search(r"font-family: '([^']+)'", body).group(1)
    style = re.search(r"font-style: (\w+)", body).group(1)
    weight = re.search(r"font-weight: (\d+)", body).group(1)
    url = re.search(r"url\((https://[^)]+\.woff2)\)", body).group(1)
    unicode_range = re.search(r"unicode-range: ([^;]+);", body).group(1)
    fname = f"{family.lower()}-{style}-{weight}-{subset}.woff2"
    target = out_dir / fname
    if not target.exists():
        urllib.request.urlretrieve(url, target)
    emitted.append(
        "/* %s */\n@font-face {\n  font-family: '%s';\n  font-style: %s;\n  font-weight: %s;\n"
        "  font-display: swap;\n  src: url('/fonts/%s') format('woff2');\n  unicode-range: %s;\n}\n"
        % (subset, family, style, weight, fname, unicode_range)
    )
    print(f"{fname}  {target.stat().st_size:,} bytes")

header = (
    "/* Self-hosted copies of the Google Fonts the site was loading from\n"
    "   fonts.googleapis.com (Cinzel 400/700/900; Spectral 300-700 + 400 italic),\n"
    "   latin + latin-ext subsets, downloaded 2026-09-28 by\n"
    "   scripts/dev/selfhost_fonts.py. Serving them ourselves means a visitor's\n"
    "   IP never reaches Google before they have read the privacy policy. */\n\n"
)
out_css.write_text(header + "\n".join(emitted), encoding="utf-8")
print(f"wrote {out_css} with {len(emitted)} faces")
