#!/bin/bash
# Regenerate the tracked documentation PDFs from their Markdown sources.
#
# Run this whenever a doc with a committed .pdf changes — otherwise the PDF
# silently drifts from the .md (they were 4 months stale before v8.4.3.1).
#
# FONT CHOICES ARE NOT ARBITRARY:
#   mainfont/monofont = Noto Sans (Mono) CJK SC — "SC" is Simplified Chinese,
#     which is what zh-Hans is. The "HK" variant has no Mono family at all, and
#     naming a font that does not exist does NOT fail: xelatex silently
#     substitutes and DROPS every CJK glyph in code blocks.
#   docs/pandoc-header.tex maps U+25BA/U+25C4 to DejaVu Sans Mono, because the
#     CJK monospace font carries no geometric shapes and would drop those arrows
#     just as silently.
#
# The build is checked: any "Missing character" warning is a FAILURE, not noise.
set -uo pipefail
cd "$(dirname "$0")/.."
HEADER="docs/pandoc-header.tex"
rc=0
for md in docs/STREAMING-TRANSLATION.md docs/STREAMING-TRANSLATION.zh-Hans.md; do
    [[ -f "$md" ]] || continue
    missing=$(pandoc "$md" -o "${md%.md}.pdf" \
        --pdf-engine=xelatex --highlight-style=tango -H "$HEADER" \
        -V geometry:"margin=1in,paper=letterpaper" \
        -V colorlinks=true -V linkcolor=blue -V urlcolor=blue \
        -V mainfont="Noto Sans CJK SC" -V monofont="Noto Sans Mono CJK SC" 2>&1 \
        | grep 'Missing character' | sed 's/.*There is no //' | sort -u)
    if [[ -n "$missing" ]]; then
        echo "FAIL ${md}: glyphs would be silently dropped:"
        echo "$missing" | sed 's/^/    /'
        rc=1
    else
        echo "ok   ${md%.md}.pdf"
    fi
done
exit $rc
