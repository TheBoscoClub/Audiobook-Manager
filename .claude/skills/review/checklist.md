# Pre-Landing Review Checklist

## Instructions

Review the `git diff origin/main` output for the issues listed below. Be specific — cite `file:line` and suggest fixes. Skip anything that's fine. Only flag real problems.

**Two-pass review:**
- **Pass 1 (CRITICAL):** Run SQL & Data Safety, Race Conditions, LLM Output Trust Boundary, Shell Injection, and Enum Completeness first. Highest severity.
- **Pass 2 (INFORMATIONAL):** Run remaining categories below. Lower severity but still actioned.
- **Specialist categories (handled by parallel subagents, NOT this checklist):** Test Gaps, Dead Code, Magic Numbers, Conditional Side Effects, Performance & Bundle Impact, Crypto & Entropy. See `review/specialists/` for these.

All findings get action via Fix-First Review: obvious mechanical fixes are applied automatically,
genuinely ambiguous issues are batched into a single user question.

**Output format:**

```
Pre-Landing Review: N issues (X critical, Y informational)

**AUTO-FIXED:**
- [file:line] Problem → fix applied

**NEEDS INPUT:**
- [file:line] Problem description
  Recommended fix: suggested fix
```

If no issues found: `Pre-Landing Review: No issues found.`

Be terse. For each issue: one line describing the problem, one line with the fix. No preamble, no summaries, no "looks good overall."

---

## Review Categories

### Pass 1 — CRITICAL

#### SQL & Data Safety
- String interpolation in SQL (even if values are `.to_i`/`.to_f` — use parameterized queries (Rails: sanitize_sql_array/Arel; Node: prepared statements; Python: parameterized queries))
- TOCTOU races: check-then-set patterns that should be atomic `WHERE` + `update_all`
- Bypassing model validations for direct DB writes (Rails: update_column; Django: QuerySet.update(); Prisma: raw queries)
- N+1 queries: Missing eager loading (Rails: .includes(); SQLAlchemy: joinedload(); Prisma: include) for associations used in loops/views

#### Race Conditions & Concurrency
- Read-check-write without uniqueness constraint or catch duplicate key error and retry (e.g., `where(hash:).first` then `save!` without handling concurrent insert)
- find-or-create without unique DB index — concurrent calls can create duplicates
- Status transitions that don't use atomic `WHERE old_status = ? UPDATE SET new_status` — concurrent updates can skip or double-apply transitions
- Unsafe HTML rendering (Rails: .html_safe/raw(); React: dangerouslySetInnerHTML; Vue: v-html; Django: |safe/mark_safe) on user-controlled data (XSS)

#### LLM Output Trust Boundary
- LLM-generated values (emails, URLs, names) written to DB or passed to mailers without format validation. Add lightweight guards (`EMAIL_REGEXP`, `URI.parse`, `.strip`) before persisting.
- Structured tool output (arrays, hashes) accepted without type/shape checks before database writes.
- LLM-generated URLs fetched without allowlist — SSRF risk if URL points to internal network (Python: `urllib.parse.urlparse` → check hostname against blocklist before `requests.get`/`httpx.get`)
- LLM output stored in knowledge bases or vector DBs without sanitization — stored prompt injection risk

#### Shell Injection (Python-specific)
- `subprocess.run()` / `subprocess.call()` / `subprocess.Popen()` with `shell=True` AND f-string/`.format()` interpolation in the command string — use argument arrays instead
- `os.system()` with variable interpolation — replace with `subprocess.run()` using argument arrays
- `eval()` / `exec()` on LLM-generated code without sandboxing

#### Enum & Value Completeness
When the diff introduces a new enum value, status string, tier name, or type constant:
- **Trace it through every consumer.** Read (don't just grep — READ) each file that switches on, filters by, or displays that value. If any consumer doesn't handle the new value, flag it. Common miss: adding a value to the frontend dropdown but the backend model/compute method doesn't persist it.
- **Check allowlists/filter arrays.** Search for arrays or `%w[]` lists containing sibling values (e.g., if adding "revise" to tiers, find every `%w[quick lfg mega]` and verify "revise" is included where needed).
- **Check `case`/`if-elsif` chains.** If existing code branches on the enum, does the new value fall through to a wrong default?
To do this: use Grep to find all references to the sibling values (e.g., grep for "lfg" or "mega" to find all tier consumers). Read each match. This step requires reading code OUTSIDE the diff.

### Pass 2 — INFORMATIONAL

#### Async/Sync Mixing (Python-specific)
- Synchronous `subprocess.run()`, `open()`, `requests.get()` inside `async def` endpoints — blocks the event loop. Use `asyncio.to_thread()`, `aiofiles`, or `httpx.AsyncClient` instead.
- `time.sleep()` inside async functions — use `asyncio.sleep()`
- Sync DB calls in async context without `run_in_executor()` wrapping

#### Column/Field Name Safety
- Verify column names in ORM queries (`.select()`, `.eq()`, `.gte()`, `.order()`) against actual DB schema — wrong column names silently return empty results or throw swallowed errors
- Check `.get()` calls on query results use the column name that was actually selected
- Cross-reference with schema documentation when available

#### Dead Code & Consistency (version/changelog only — other items handled by maintainability specialist)
- Version mismatch between PR title and VERSION/CHANGELOG files
- CHANGELOG entries that describe changes inaccurately (e.g., "changed from X to Y" when X never existed)

#### LLM Prompt Issues
- 0-indexed lists in prompts (LLMs reliably return 1-indexed)
- Prompt text listing available tools/capabilities that don't match what's actually wired up in the `tool_classes`/`tools` array
- Word/token limits stated in multiple places that could drift

#### Completeness Gaps
- Shortcut implementations where the complete version would cost <30 minutes CC time (e.g., partial enum handling, incomplete error paths, missing edge cases that are straightforward to add)
- Options presented with only human-team effort estimates — should show both human and CC+gstack time
- Test coverage gaps where adding the missing tests is a "lake" not an "ocean" (e.g., missing negative-path tests, missing edge case tests that mirror happy-path structure)
- Features implemented at 80-90% when 100% is achievable with modest additional code

#### Time Window Safety
- Date-key lookups that assume "today" covers 24h — report at 8am PT only sees midnight→8am under today's key
- Mismatched time windows between related features — one uses hourly buckets, another uses daily keys for the same data

#### Type Coercion at Boundaries
- Values crossing Ruby→JSON→JS boundaries where type could change (numeric vs string) — hash/digest inputs must normalize types
- Hash/digest inputs that don't call `.to_s` or equivalent before serialization — `{ cores: 8 }` vs `{ cores: "8" }` produce different hashes

#### View/Frontend
- Inline `<style>` blocks in partials (re-parsed every render)
- O(n*m) lookups in views (`Array#find` in a loop instead of `index_by` hash)
- Ruby-side `.select{}` filtering on DB results that could be a `WHERE` clause (unless intentionally avoiding leading-wildcard `LIKE`)

#### Distribution & CI/CD Pipeline
- CI/CD workflow changes (`.github/workflows/`): verify build tool versions match project requirements, artifact names/paths are correct, secrets use `${{ secrets.X }}` not hardcoded values
- New artifact types (CLI binary, library, package): verify a publish/release workflow exists and targets correct platforms
- Cross-platform builds: verify CI matrix covers all target OS/arch combinations, or documents which are untested
- Version tag format consistency: `v1.2.3` vs `1.2.3` — must match across VERSION file, git tags, and publish scripts
- Publish step idempotency: re-running the publish workflow should not fail (e.g., `gh release delete` before `gh release create`)

**DO NOT flag:**
- Web services with existing auto-deploy pipelines (Docker build + K8s deploy)
- Internal tools not distributed outside the team
- Test-only CI changes (adding test steps, not publish steps)

---

## Severity Classification

```
CRITICAL (highest severity):      INFORMATIONAL (main agent):      SPECIALIST (parallel subagents):
├─ SQL & Data Safety              ├─ Async/Sync Mixing             ├─ Testing specialist
├─ Race Conditions & Concurrency  ├─ Column/Field Name Safety      ├─ Maintainability specialist
├─ LLM Output Trust Boundary      ├─ Dead Code (version only)      ├─ Security specialist
├─ Shell Injection                ├─ LLM Prompt Issues             ├─ Performance specialist
└─ Enum & Value Completeness      ├─ Completeness Gaps             ├─ Data Migration specialist
                                   ├─ Time Window Safety            ├─ API Contract specialist
                                   ├─ Type Coercion at Boundaries   └─ Red Team (conditional)
                                   ├─ View/Frontend
                                   └─ Distribution & CI/CD Pipeline

All findings are actioned via Fix-First Review. Severity determines
presentation order and classification of AUTO-FIX vs ASK — critical
findings lean toward ASK (they're riskier), informational findings
lean toward AUTO-FIX (they're more mechanical).
```

---

## Fix-First Heuristic

This heuristic is referenced by both `/review` and `/ship`. It determines whether
the agent auto-fixes a finding or asks the user.

```
AUTO-FIX (agent fixes without asking):     ASK (needs human judgment):
├─ Dead code / unused variables            ├─ Security (auth, XSS, injection)
├─ N+1 queries (missing eager loading)      ├─ Race conditions
├─ Stale comments contradicting code       ├─ Design decisions
├─ Magic numbers → named constants         ├─ Large fixes (>20 lines)
├─ Missing LLM output validation           ├─ Enum completeness
├─ Version/path mismatches                 ├─ Removing functionality
├─ Variables assigned but never read       └─ Anything changing user-visible
└─ Inline styles, O(n*m) view lookups        behavior
```

**Rule of thumb:** If the fix is mechanical and a senior engineer would apply it
without discussion, it's AUTO-FIX. If reasonable engineers could disagree about
the fix, it's ASK.

**Critical findings default toward ASK** (they're inherently riskier).
**Informational findings default toward AUTO-FIX** (they're more mechanical).

---

## Suppressions — DO NOT flag these

- "X is redundant with Y" when the redundancy is harmless and aids readability (e.g., `present?` redundant with `length > 20`)
- "Add a comment explaining why this threshold/constant was chosen" — thresholds change during tuning, comments rot
- "This assertion could be tighter" when the assertion already covers the behavior
- Suggesting consistency-only changes (wrapping a value in a conditional to match how another constant is guarded)
- "Regex doesn't handle edge case X" when the input is constrained and X never occurs in practice
- "Test exercises multiple guards simultaneously" — that's fine, tests don't need to isolate every guard
- Eval threshold changes (max_actionable, min scores) — these are tuned empirically and change constantly
- Harmless no-ops (e.g., `.reject` on an element that's never in the array)
- ANYTHING already addressed in the diff you're reviewing — read the FULL diff before commenting
<!--
  This file is APPENDED to gstack's stock checklist by bin/gstack-seed-checklist.
  It extends — does not replace — the upstream categories. Stock handles Rails/Node
  well; these extensions cover Rust, Bash, extended Python, and bosco-stack-specific
  conventions.

  Maintenance: if gstack's stock checklist grows new categories that cover any of
  the items below, trim the duplicate from this file. Stock is the source of truth
  for general patterns; this file is only for stack-specific additions.
-->

---

# Language-Specific Extensions

## Rust

### Pass 1 — CRITICAL

#### Unsafe code without invariant documentation
- `unsafe { ... }` block without a preceding `// SAFETY:` comment naming the invariants the caller must uphold. Clippy's `undocumented_unsafe_blocks` catches this — require it on in CI.
- `unsafe fn` without `# Safety` section in the doc comment documenting preconditions
- `std::mem::transmute` between types whose size/alignment could differ across targets
- `slice::from_raw_parts` or `*const T` dereference without validating length and lifetime
- `str::from_utf8_unchecked` on data not known to be UTF-8 (prefer `from_utf8` + error path)

#### Production-path panics
- `.unwrap()` / `.expect(...)` in code under `src/bin/`, `src/main.rs`, or any library function callable from downstream crates — replace with `?` or explicit `match`
- `panic!(...)` / `unreachable!()` / `todo!()` in library code that will ship — library panics should propagate as `Err` so callers can recover
- Integer arithmetic without `checked_*` / `saturating_*` / `wrapping_*` on values from untrusted input (use the Rust-appropriate overflow-safe variant)

#### Concurrency foot-guns
- `.await` while holding a `std::sync::Mutex` or `RwLock` guard — use `tokio::sync::Mutex` when guard crosses await points
- `std::mem::forget` on a type with `Drop` when the Drop side-effect matters for correctness (rare but catastrophic when it hits)
- `Arc<Mutex<T>>` where `RwLock` is more appropriate — or vice versa — identify from access pattern in the diff

### Pass 2 — INFORMATIONAL

- Unbounded dependency versions in `Cargo.toml` (`"*"` or `">=1"`) — pin with `^`/`~` at minimum
- Missing `cargo audit` / `cargo deny` step in CI workflow
- Public API functions returning `String` where `&str` or `Cow<'_, str>` would avoid allocation
- `derive(Debug)` on types containing secrets — use manual `Debug` impl that redacts
- Missing `#[must_use]` on functions returning `Result`/`Option` that callers frequently drop

---

## Bash / Shell Scripts

### Pass 1 — CRITICAL

#### Script hygiene
- Shell script without `set -euo pipefail` as the first non-comment line (silent failure risk)
- Missing `IFS=$'\n\t'` where word-splitting matters
- Shebang is `#!/bin/sh` when the script uses bash features (arrays, `[[ ]]`, `<()`) — must be `#!/bin/bash` per project convention

#### Unquoted expansions
- Unquoted `$var` in command arguments — word-splitting + glob expansion risk. Must be `"$var"`
- `$@` used instead of `"$@"` — same problem, breaks on args with spaces
- `$*` used where `"$@"` is meant — concatenates with IFS instead of preserving word boundaries
- Command substitution output used unquoted: `COMMIT=$(git rev-parse HEAD); git tag $COMMIT` → must be `"$COMMIT"`

#### Destructive operations on variable-expanded paths
- `rm -rf "$path"` without prior guard: `[ -n "$path" ] && [ "$path" != "/" ]`
- `rm -rf "$DIR"/*` without verifying `$DIR` is non-empty (expands to `/*` if unset — catastrophic)
- `find ... -delete` with variable-expanded `-path` / `-name` — verify pattern scope first
- `chmod 777` or `chmod -R 777` anywhere in the diff — always a red flag
- `sudo rm -rf` chained after any command that could have set `$var` incorrectly

#### Injection / eval
- `eval "$var"` on any value derived from user input, file content, or network response
- `bash -c "$cmd"` with variable interpolation — use `bash -c 'cmd' _ "$arg"` form instead
- Command substitution containing user input: `` `$USER_INPUT` `` — use array-passed args

### Pass 2 — INFORMATIONAL

- Backticks for command substitution (``` `cmd` ```) — use `$(cmd)` for readability and nesting
- `cd $dir` without `|| exit` — subsequent commands may run in wrong directory
- `$?` checks written as `if [ $? -eq 0 ]` — prefer `if cmd; then` form
- Parsing `ls` output in a loop (`for f in $(ls)`) — use glob or `find -print0 | xargs -0`
- Using `/tmp/$$` or `/tmp/foo` as a filename — use `mktemp` (TOCTOU + predictable-name risk)
- Missing `trap 'cleanup' EXIT` on scripts that create temp files or mutate state
- `printf` called without a format string: `printf "$var"` — use `printf '%s\n' "$var"` (injection via `$var` containing `%`)
- ShellCheck (`shellcheck`) not run in CI on changed `.sh` / `.bash` files

---

## Python (extends stock Shell Injection + Async/Sync Mixing)

### Pass 1 — CRITICAL

#### Deserialization of untrusted data
- `yaml.load(...)` without `Loader=yaml.SafeLoader` — defaults to `FullLoader` which can construct arbitrary Python objects
- `yaml.unsafe_load(...)` ever, except on files the process itself wrote
- Python's binary-object serialization modules (`p1ckle`, `marshal`, `shelve`) — when invoked on data not locally produced, they are code-execution vectors by design. Always use JSON or an explicit schema for external input. [token obfuscation in this bullet is intentional: the exact module name is p-i-c-k-l-e]
- `ast.literal_eval` is safe; `eval` / `exec` / `compile` on untrusted strings is not

#### Weak cryptography
- `hashlib.md5(...)` / `hashlib.sha1(...)` used for integrity or authentication (fine for cache keys / non-security fingerprints, flag anyway and ASK)
- `random.random()` / `random.randint()` for tokens, session IDs, password resets — use `secrets`
- `Crypto.*` (`pycryptodome`) import when `cryptography` covers the use case — unless there's a specific reason
- ECB mode block ciphers, custom rolled crypto primitives

#### Network / SSRF / TLS
- `requests.get/post/put/delete(...)` without a `timeout=` parameter — will hang indefinitely on unresponsive server
- `requests.*(..., verify=False)` — TLS verification disabled
- `urllib3.disable_warnings(...)` calls (often paired with `verify=False`)
- `urllib.request.urlopen(...)` on user-supplied URL without allowlist validation (SSRF)

#### Error swallowing
- `except:` (bare) — catches `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`
- `except Exception: pass` — silent failure mode, almost always wrong
- `except Exception as e: logger.debug(e)` on control-flow-affecting operations — dropped signal

### Pass 2 — INFORMATIONAL

- Python 2 idioms in new code (`print` without parens, `unicode`, `xrange`) — your Python-3.14 default rule
- `dict.update(**kw)` where non-string keys are possible
- `os.path.join(user_input, ...)` without `os.path.realpath` + prefix check (path traversal)
- `open(path)` without explicit `encoding='utf-8'` — locale-dependent on some systems
- `type(x) == Y` instead of `isinstance(x, Y)` — breaks on subclasses

---

# Bosco-Stack Specific Extensions

## API keys + secrets (per `rules/security.md`)

### Pass 1 — CRITICAL

- Any literal API key / token / service credential in source (matches: `sk-*`, `ghp_*`, `gho_*`, `xoxb-*`, `AKIA*`, `AIza*`, `re_*`, `eyJ*` JWT, DeepL API token). MUST be sourced from `~/.config/api-keys.env` — not hardcoded, not even in config templates
- `.env` file or `credentials.*` file committed to git (check `.gitignore` coverage)
- Hardcoded `ANTHROPIC_API_KEY` — violates the Max-plan rule in CLAUDE.md (only OAuth should be used for Claude Code)
- Token interpolation into log strings (`logger.info(f"key={api_key}")`) — leaks into logs, journals, error reports

## Rust-first rule (per `rules/security.md`)

### Pass 2 — INFORMATIONAL

- New compiled binary in C / C++ / Go without a PR-description comment explaining why Rust is not appropriate (allowed exceptions: Qt/KDE programs, kernel modules, ecosystems with no Rust support)
- Existing C/C++ code modified without corresponding memory-safety hardening (`-fstack-protector-strong`, `-D_FORTIFY_SOURCE=2`, smart pointers, `explicit_bzero` on secrets)

## BTRFS + snapshots (per `rules/projects.md` + `rules/infrastructure.md`)

### Pass 1 — CRITICAL

- `btrfs subvolume delete` on a path matching `*pristine*` — pristine snapshots are protected, never delete
- `virsh snapshot-delete` targeting `pristine` — same rule
- BTRFS snapshot created OUTSIDE `$PROJECT/.snapshots/` — pollutes the top-level `ccp` listing
- Snapshot created at `<projects-root>/MyProject-snap-*` path (not inside project) — violates canonical location rule

### Pass 2 — INFORMATIONAL

- fstab entry added without `nofail,degraded` options on the projects storage array — can break boot on a degraded array
- Subvolume created without corresponding fstab entry — won't survive a remount
- `snapper` config referenced without being created first (ordering bug — delete-config requires subvolume)

## Docker (per `rules/development-tools.md`)

### Pass 1 — CRITICAL

- `Dockerfile` without a `USER` directive before the final `CMD` / `ENTRYPOINT` — runs as root by default
- Secrets passed via `ARG` — visible forever in image history. Use `--secret` mount or runtime env
- `COPY . /app` when `.dockerignore` is missing — bakes `.git/`, `.env`, `node_modules` into the image
- `pip install --trusted-host` or `npm install --unsafe-perm` — bypasses integrity checks
- `ADD http://...` for external downloads without checksum verification

### Pass 2 — INFORMATIONAL

- Multi-stage build not used when final image could be substantially smaller
- `apt-get install` without `--no-install-recommends` and without `rm -rf /var/lib/apt/lists/*` in the same RUN
- Missing `HEALTHCHECK`
- Docker image not built via Buildx (per `rules/development-tools.md`)

## Database encryption at rest (per `rules/security.md`)

### Pass 1 — CRITICAL

- MariaDB/MySQL init script without `innodb_encrypt_tables = FORCE`, `innodb_encrypt_log = ON`, `encrypt_binlog = ON`
- PostgreSQL init without either `pgcrypto` usage for sensitive columns or filesystem-level encryption documented
- SQLite database storing authentication / PII / tokens without SQLCipher — plain SQLite leaves plaintext in journal files
- Docker volume mount for a sensitive DB without volume-level encryption mentioned in `docker-compose.yml`

## Skill supply chain (per gstack's `/cso --skills` but codified here)

### Pass 1 — CRITICAL

- New file in `.claude/skills/**/*.md` or `~/.claude/skills/**/*.md` that uses `curl ... | bash`, `curl ... | sh`, or similar pipe-to-shell pattern
- Skill files that reference `~/.config/api-keys.env`, `~/.ssh/`, or other credential paths without a justification comment
- Skill file that writes outside its own dir without explicit user intent
- New MCP server added to `.claude/settings.json` pointing at a non-public / non-official endpoint

## Git hygiene (per `rules/github.md`)

### Pass 1 — CRITICAL

- Script that passes `-c commit.gpgsign=false` or `--no-gpg-sign` to git — branch protection rule forbids this
- Script that uses `--no-verify` to skip pre-commit / pre-push hooks — forbidden unless user explicitly requested
- `git push --force` or `git reset --hard` on the default branch in any automation
- `gh repo create` without `--private` flag (new repos must be private by default)
- `gh repo create` in a personal namespace instead of the designated org — violates repo-owner rule

### Pass 2 — INFORMATIONAL

- CHANGELOG.md entry for the diff missing bold-title format or lacks comparison-link update (per `rules/changelog.md`)
- Version string duplicated in two files instead of derived from one canonical source (per `rules/development-tools.md` — single canonical source rule)
- New file created at a second path when an existing canonical path exists (should be symlink)

## Search hygiene

### Pass 2 — INFORMATIONAL

- `grep -r` / `rg` without excluding `.snapshots/` and `.btrbk-snapshots/` — per `rules/development-tools.md`, snapshots should never pollute search results
- `find` without `-not -path '*/.snapshots/*'` when searching under the projects root — per `rules/development-tools.md`, snapshots should never pollute search results

---

# Severity Classification — additions to stock table

```
CRITICAL (added above):                INFORMATIONAL (added above):
├─ Rust: Unsafe, production panics,    ├─ Rust: unbounded deps, audit gaps,
│  concurrency foot-guns                │  must-use, debug on secrets
├─ Bash: hygiene, unquoted expand,     ├─ Bash: backticks, cd failures,
│  destructive ops, eval injection      │  $?, mktemp, shellcheck
├─ Python: deserialization, weak       ├─ Python: py2 idioms, path join,
│  crypto, SSRF/TLS, error swallowing   │  encoding, isinstance
├─ API keys: literals in source        ├─ Rust-first: new C/C++/Go PR
├─ BTRFS: pristine delete, snapshot    ├─ Docker: multi-stage, apt cleanup,
│  location, btrbk paths                │  healthcheck, buildx
├─ Docker: USER, ARG secrets,          ├─ BTRFS: fstab nofail, subvol+fstab
│  .dockerignore, ADD URL                │  ordering, snapper ordering
├─ DB encryption: TDE / SQLCipher      ├─ Git: changelog, canonical source,
├─ Skill supply chain: curl-pipe-bash  │  symlink vs copy
├─ Git: bypass signing, force-push,    └─ Search: snapshot exclusions
│  wrong namespace
```

The Fix-First Heuristic and Suppressions from stock apply unchanged to these extensions.
