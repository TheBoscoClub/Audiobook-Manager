# Content Classification Drift

How the `audiobooks.content_type` column gets stale, why it matters, and how
to repair it. Read this before debugging "why does X show/hide on the
library view but not elsewhere."

## What `content_type` Is

Every row in `audiobooks` carries a `content_type` string. The library-view
API (`library/backend/api_modular/audiobooks.py`) filters on it:

```python
AUDIOBOOK_FILTER = "(content_type IN ('Product', 'Performance', 'Speech') "
                   "OR content_type IS NULL)"
```

Anything classified as `Podcast`, `Show`, `Episode`, `Lecture`,
`Radio/TV Program`, `Meditation`, `Newspaper/Magazine` is **excluded** from
the main library view and surfaces only in the Reading Room / Podcasts &
Shows collections.

## The Bug Class: Stale Classification at Import Time

`content_type` is set **once, at row-insertion time**, by the scanner +
enrichment pipeline. It is not recomputed on later code changes. That means
every fix to the classification logic only affects **new imports** — existing
rows stay frozen at whatever the logic produced on the day they were
scanned.

When the logic gets smarter (e.g., a new podcast-publisher heuristic), the
new code can identify a book as a Podcast on subsequent scans, but rows
already in the DB keep their old `Product` label unless something explicitly
rewrites them.

### The April 2026 dev VM incident

On 2026-04-07 19:20, a dev VM was populated from
a fresh scan+import. All 1,844 rows landed in a one-second window.

On 2026-04-08, commits `ccb863e` and `c10b335` added **Phase 0 podcast
detection** — publisher/author heuristics that catch podcasts the Audible
API reports as `Product`, and a backfill pass that sweeps the whole DB
reclassifying stale rows.

The dev scan predated the fix by about 21 hours. The Localization-RND branch
later forked off main with Phase 0 *in the code* — but code doesn't
retroactively rewrite rows, so the dev DB stayed stuck at pre-Phase-0
classifications. Prod and QA were scanned after the fix (or had the backfill
run on them), so they were clean.

Observable symptoms:

- 70 rows labeled `Podcast` on dev that prod had reclassified to `Show`
- 21 rows labeled `Podcast` on dev that prod had reclassified to `Episode`
- 10 rows labeled `Podcast` on dev that prod had reclassified back to
  `Product` (false positives the refined heuristic corrected)
- 1 stray `Meditation` row (Brian Cox Scottish Superstitions) with no prod
  counterpart — pre-existing noise, not drift
- 10 Wondery ad-free `Product` rows caught by the Phase 0 publisher
  heuristic (America's Coup in Iran, Encore: Enron, The Osage Murders)
- 6 residual `Product` rows that slipped past both passes above
  (3 Michelle Obama "The Light Podcast" episodes, 3 Stephen Fry "Ep."
  episodes) — their ASINs didn't match prod's ASINs for the same titles,
  and their authors weren't in `_PODCAST_PUBLISHERS`

Total: **118 rows** needed reclassification on dev to match prod
(101 ASIN-JOIN + 1 Meditation singleton + 10 Phase 0 backfill + 6 author
cross-classification).

## The ASIN-Mismatch Blind Spot

ASIN-based drift detection (TSV export → JOIN) catches rows where prod and
dev agree on the ASIN and differ on `content_type`. It does **not** catch
rows where prod and dev have **different ASINs for the same logical title**.
This happens when:

- Prod rescanned the title under a newer Audible SKU and dev still has the
  older one
- Prod had NULL ASIN for a row (never resolved) and dev had a populated one
- An item was rescanned on prod after Audible swapped ASINs for an edition

In the April incident, three Michelle Obama "The Light Podcast" episodes and
three Stephen Fry "Ep." episodes fell into this gap. Prod had classified them
correctly but under different ASIN strings, so the JOIN never linked them.

**Secondary detection pattern — cross-classification author analysis.** Find
authors who appear in BOTH the library-view classifications
(`Product`/`Performance`/`Speech`) and the excluded classifications
(`Podcast`/`Show`/`Episode`). Any title in the first group by an author who
already has podcast entries in the second group is a strong drift candidate:

```sql
WITH podcast_authors AS (
  SELECT DISTINCT author FROM audiobooks
    WHERE content_type IN ('Podcast','Show','Episode')
      AND author IS NOT NULL AND author != ''
)
SELECT a.id, a.asin, substr(a.title,1,60) t, substr(a.author,1,30) au,
       a.content_type
  FROM audiobooks a
  JOIN podcast_authors pa ON pa.author = a.author
  WHERE a.content_type IN ('Product','Performance','Speech')
  ORDER BY au, t;
```

Review each hit manually — some authors legitimately have both regular
audiobooks and podcasts (e.g., an author with a novel AND a podcast show).
The goal is to surface candidates for human judgment, not auto-reclassify.

**Verification must exercise the user-facing pathway.** The April incident
was initially declared "resolved" based on DB row counts and backend API
`total_count` dropping to the expected value. The user's browser still
showed the leaked rows because the first fix pass missed the 6 ASIN-mismatch
cases. The backend API query and the actual user-visible grid agreed
*after* the mismatch was caught — but the backend signal alone was never
proof. Always verify by rendering the library view (or querying the API
exactly as the UI does) and looking for the specific titles you expect to
be excluded.

## Detection: Cross-DB ASIN Comparison

The cleanest way to detect drift is to compare classifications on ASIN
between a "known-good" DB (prod) and a "suspect" DB (dev/QA/staging).

```bash
# On the known-good host (prod): export ASIN → content_type
sudo sqlite3 /var/lib/audiobooks/db/audiobooks.db \
  "SELECT asin, content_type FROM audiobooks WHERE asin IS NOT NULL;" \
  > /tmp/prod-content-types.tsv

# Transfer the TSV to the suspect host (via guest-exec file write, scp, etc.)

# On the suspect host: compare
sudo -u audiobooks sqlite3 /var/lib/audiobooks/audiobooks.db <<'SQL'
CREATE TEMP TABLE prod_ct(asin TEXT PRIMARY KEY, content_type TEXT);
.mode tabs
.import /tmp/prod-content-types.tsv prod_ct
SELECT a.content_type AS dev_ct, p.content_type AS prod_ct, COUNT(*)
  FROM audiobooks a
  JOIN prod_ct p ON p.asin = a.asin
  WHERE a.content_type IS NOT p.content_type
  GROUP BY 1, 2
  ORDER BY 3 DESC;
SQL
```

Duplicate ASINs in the source DB (common — multiple editions share an ASIN)
will cause benign `UNIQUE constraint failed` warnings during `.import`. The
first occurrence wins; the comparison result stays valid and surgical.

## Repair: ASIN-Based Surgical Rewrite

Once you have the TSV, apply the reclassification in a single transaction:

```bash
sudo cp -a /var/lib/audiobooks/audiobooks.db{,.bak-classification-$(date +%F)}

sudo -u audiobooks sqlite3 /var/lib/audiobooks/audiobooks.db <<'SQL'
BEGIN;
CREATE TEMP TABLE prod_ct(asin TEXT PRIMARY KEY, content_type TEXT);
.mode tabs
.import /tmp/prod-content-types.tsv prod_ct
UPDATE audiobooks
  SET content_type = (SELECT content_type FROM prod_ct
                      WHERE prod_ct.asin = audiobooks.asin)
  WHERE asin IN (SELECT asin FROM prod_ct)
    AND content_type IS NOT (SELECT content_type FROM prod_ct
                             WHERE prod_ct.asin = audiobooks.asin);
COMMIT;
SQL
```

Rows without ASINs or with no prod counterpart are left alone — those need
manual review (title/author match or user decision).

## Prevention: Run Backfill After Any DB Import

`library/scripts/backfill_enrichment.py` contains the Phase 0 podcast
detection pass (added in commit `c10b335`). It scans every `Product` row
and reclassifies based on publisher/author heuristics against known podcast
networks.

**Run this on any host immediately after**:

1. A fresh install that imported existing data from a scan
2. Restoring a DB from a pre-fix backup
3. Upgrading from a pre-Phase-0 version to a Phase-0+ version

```bash
sudo -u audiobooks /opt/audiobooks/library/venv/bin/python \
  /opt/audiobooks/library/scripts/backfill_enrichment.py --podcast-detection
```

The backfill is idempotent — running it on an already-clean DB is a no-op.

## Why the Classification Isn't Retroactive by Default

Reclassifying on every enrichment run would make enrichment O(N) on the
whole library instead of O(Δ) on changed rows, and would fight user
overrides (manual reclassifications a user makes in the admin UI). Keeping
the fix in a separate opt-in backfill script is the right trade: fast
normal operation, explicit repair when you know the logic changed.

## Rules for Future Classification Changes

1. **If you change classification logic, update the backfill script in the
   same commit.** Every fix that can produce a different label than the
   old logic must have a corresponding backfill pass.
2. **Document the change in CHANGELOG.md under `### Fixed`** with the
   specific heuristic added, so deployers know when to run the backfill.
3. **Never silently rewrite user overrides.** The backfill should respect
   an `override_content_type` flag (or equivalent) if the admin UI adds one.
4. **Run the backfill on every DB this repo touches during `/test`** — if
   a suite imports a DB fixture, it should also run the backfill against
   that fixture so tests don't drift into using stale labels.
