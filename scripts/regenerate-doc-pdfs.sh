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
cd "$(dirname "$0")/.." || { echo "FAIL: cannot cd to project root"; exit 1; }
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
    pandoc_rc=${PIPESTATUS[0]}

    # The glyph grep is only half the check. `set -uo pipefail` without -e means
    # the assignment above always succeeds, so ANY failure that does not emit a
    # "Missing character" warning — pandoc or xelatex missing, a LaTeX error, a
    # missing header file, OOM — leaves $missing empty and used to print "ok"
    # with no PDF on disk. A drift detector that fails silently on every failure
    # except the one it greps for is worse than none (Audiobook-Manager-p1k).
    if (( pandoc_rc != 0 )); then
        echo "FAIL ${md}: pandoc exited ${pandoc_rc}"
        rc=1
        continue
    fi
    if [[ ! -s "${md%.md}.pdf" ]]; then
        echo "FAIL ${md}: no PDF was produced"
        rc=1
        continue
    fi
    if [[ -n "$missing" ]]; then
        echo "FAIL ${md}: glyphs would be silently dropped:"
        echo "$missing" | sed 's/^/    /'
        rc=1
    else
        echo "ok   ${md%.md}.pdf"
    fi
done
exit $rc
