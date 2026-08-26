# Audio Translation Capability Flag — Executable Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Every task carries its own proof artifact requirement — a task without executed proof is not complete.

**Goal:** Make the audio-translation subsystem (STT → translate → TTS) an opt-in capability that is **OFF by default**, enabled per-locale by an end-user admin, without removing one line of its code, tests, docs, or examples from the repository. After this change a fresh install runs no GPU worker, registers no translation routes, renders no translation UI, and reports no unreachable backends — while an admin who sets one config variable gets the full pipeline back.

**Architecture:** Mirror the in-tree `AUTH_ENABLED` pattern exactly. `library/backend/api_modular/__init__.py` already conditionally initialises and registers four auth blueprints on a single `flask_app.config` boolean; this plan applies the same shape to the four audio-translation blueprints, which are already co-located in `_register_extension_blueprints()`. The switch is a **locale list**, not a boolean: an empty `AUDIOBOOKS_AUDIO_TRANSLATION_LOCALES` means off, and a non-empty list means on for exactly those locales. A new `/api/system/capabilities` endpoint lets the web UI hide controls rather than render buttons that 404.

**Tech Stack:** Python 3.14 (Flask blueprints, `library/localization/`, `library/translation_monitor/`), SQLite WAL, systemd units, vanilla JS (`library/web-v2/js/`), bash (`install.sh`, `upgrade.sh`, `scripts/install-manifest.sh`, `scripts/smoke_probe.sh`), pytest.

---

## STATUS: PARKED — 2026-08-26

**This plan is a documented "maybe". Do not start it without a trigger.**

**Trigger to unpark:** Qing confirms she expects to use the library's translated
audio. If she does not, the decision moves to Option B or C (see the session
analysis of 2026-08-26) or to leaving the capability permanently off.

**Why parked:** the operationally valuable half of this plan needed no code at
all. On 2026-08-26 the three translation systemd units were disabled on prod and
the decommissioned RunPod endpoint IDs were removed from
`/etc/audiobooks/audiobooks.conf`. `audiobook.target` declares all three units
with `Wants=` (not `Requires=`), so disabling them cannot break the target. That
mitigation closed the prod symptom — pointless daemons and a smoke probe
reporting unreachable backends — without a line of code.

What remains in this plan is therefore value for **other deployments and
future-you**, not for this production instance:

- `install.sh:2009` enables `audiobook-stream-translate.service` on every fresh
  install, so every community user gets a worker that fails forever.
- With the capability off but the routes still registered, the web UI renders
  translation controls that 404 rather than hiding cleanly.
- "Off" is currently a *broken* state rather than a *supported* one.

Those are real, but none of them is urgent, and all seven of the open bd issues
except `od0` are independent of this work.

### Ordering constraint discovered after this plan was written

**`od0` (sampler dead since 2026-06-06) must be diagnosed BEFORE the capability
is permanently off, or it becomes unverifiable.** An `od0` fix cannot be proven
end-to-end without a live STT backend to exercise it against. Either diagnose it
while a backend is available, or accept that it stays open and unverifiable
until the capability is next enabled. Do not close `od0` merely because turning
the capability off has stopped making it visible — that is masking, not fixing.

### Branching guidance if this is unparked

Do **not** put this on a long-lived R&D branch, and do not put it on
`9.0-refactor-rnd` (that is the `bth` complexity-reduction epic — different
scope). Measured churn over the 60 days to 2026-08-26: `upgrade.sh` 6 commits,
`install.sh` 5 commits, 51 distinct test files touched against the 42 this plan
modifies. A long-lived branch would collide with the release path repeatedly.

The flag is the isolation mechanism. Phases 1-5 are no-ops in production while
`AUDIOBOOKS_AUDIO_TRANSLATION_LOCALES` is empty, so they can land on main
incrementally. Give only Phase 6 (`install.sh`/`upgrade.sh`) and Phase 7.4 (the
42-module test audit) their own short-lived branches — those are the two that
can turn main red and block a prod bugfix release.

---

## Verified Preconditions (measured 2026-08-26, do not re-derive)

These were established by direct inspection. They are the load-bearing facts this plan rests on; if any is falsified during implementation, **stop and re-plan**.

| Fact | Evidence |
|---|---|
| Zero layer-3 tables (`chapter_subtitles`, `chapter_translations_audio`, `streaming_segments`, `sampler_jobs`, `translation_queue`) appear in any core browse module | `grep -c` across `audiobooks.py`, `grouped.py`, `collections.py`, `search_cjk.py`, `editions.py`, `duplicates.py`, `supplements.py`, `user_state.py`, `preferences.py`, `utilities.py`, `core.py`, `websocket.py` → all 0 |
| `audiobook_translations` (layer 2, DeepL metadata) IS joined into core queries and MUST stay enabled | `grouped.py:159,249`, `audiobooks.py:381,614` |
| The four audio-translation blueprints are already co-located in one function | `api_modular/__init__.py::_register_extension_blueprints()` — `translations_bp`, `subtitles_bp`, `translated_audio_bp`, `streaming_bp` + `localization.queue.init_queue` |
| The conditional-registration precedent exists and is tested | `api_modular/__init__.py:155-158` (`_register_auth_blueprints`), `:128-140` (`_init_route_modules`) |
| `install.sh` enables the streaming worker unconditionally on every fresh install | `install.sh:2009` — `_enable_unit_smart "audiobook-stream-translate.service"` |
| Scanner coupling is exactly two hook call sites | `scanner/post_insert.py:141-146` (`@register_post_insert("Translation queue")`), `scanner/utils/db_helpers.py:127-129` (→ `sampler_hook.enqueue_sampler_for_new_book`) |
| Subsystem surface | `library/localization/` 4,779 lines / 31 files; `library/translation_monitor/` 1,056 lines; 5 systemd units; 42 of 233 test modules; ~9 data migrations; 2 web-v2 JS modules |

---

## Scope Boundary (READ BEFORE STARTING)

**IN scope:**

1. New config variable `AUDIOBOOKS_AUDIO_TRANSLATION_LOCALES` (default empty) in `lib/audiobook-config.sh` + `library/localization/config.py`.
2. Derived `flask_app.config["AUDIO_TRANSLATION_ENABLED"]` and `["AUDIO_TRANSLATION_LOCALES"]`.
3. Conditional init + registration of the four audio-translation blueprints.
4. Conditional execution of the two scanner hooks.
5. New `GET /api/system/capabilities` endpoint.
6. Web UI gating of subtitle / streaming-translate controls on that endpoint.
7. `install.sh` + `upgrade.sh` stop unconditionally enabling the five translation units; enable them only when the capability is on.
8. `scripts/smoke_probe.sh` reports "audio translation disabled" instead of probing STT backends when off.
9. Loud-failure guard: capability ON with no STT backend configured fails at startup with an actionable message.
10. CI matrix dimension covering the capability-OFF path.
11. New tests: capability-off route absence, capability-on parity, scanner-hook skip, capabilities endpoint contract, loud-failure guard.
12. `etc/audiobooks.conf.example` + `install.sh` config stanza documentation.
13. New `docs/AUDIO-TRANSLATION-ENABLING.md`; updates to `README.md`, `docs/ARCHITECTURE.md`, `docs/MULTI-LANGUAGE-SETUP.md`, `docs/SERVERLESS-OPS.md`, `docs/TROUBLESHOOTING.md`.
14. CHANGELOG entry + VERSION bump.
15. Cross-file upgrade-consistency review per `.claude/rules/upgrade-consistency.md` (all 8 files physically opened and traced).

**OUT of scope (do NOT do these):**

- Deleting, moving, or extracting any localization code, test, doc, or example. This plan is Option A only.
- Touching layer 1 (UI i18n: `library/locales/`, `js/i18n.js`, `i18n_routes.py`) — stays unconditionally ON.
- Touching layer 2 (metadata translation: `audiobook_translations`, DeepL text, pinyin sort) — stays unconditionally ON.
- Dropping, truncating, or migrating any translation table. Existing data for 1,055 books MUST survive untouched.
- Any Vast.ai / RunPod / GPU-backend provisioning work. Backend choice is a separate decision.
- Any blueprint-discovery or plugin mechanism (that is Option B, deliberately deferred).

---

## The Flag Design

One variable is the switch. There is deliberately no separate boolean, so there is nothing to drift out of sync.

```bash
# /etc/audiobooks/audiobooks.conf
AUDIOBOOKS_SUPPORTED_LOCALES=en,zh-Hans        # layer 1, UI i18n — unchanged, free, always on
AUDIOBOOKS_AUDIO_TRANSLATION_LOCALES=""        # layer 3 — EMPTY = OFF (new default)
                                               #   admin sets e.g. "zh-Hans" or "zh-Hans,es"
```

Semantics, to be honoured identically everywhere:

- Unset or empty or whitespace-only → capability **OFF**.
- Non-empty → capability **ON**, for exactly the listed locales.
- Every listed locale MUST also appear in `AUDIOBOOKS_SUPPORTED_LOCALES`; if not, that is a **startup error**, not a silent drop.
- Capability ON with no STT backend configured is a **startup error** (see Phase 5).

---

## Phase 1 — Config Plumbing

**Task 1.1** — Add `AUDIOBOOKS_AUDIO_TRANSLATION_LOCALES` to `lib/audiobook-config.sh` with default `""`, following the existing variable-declaration style. No hardcoded paths.

**Task 1.2** — In `library/localization/config.py`, add:
- `AUDIO_TRANSLATION_LOCALES: list[str]` — parsed, stripped, empty-filtered.
- `AUDIO_TRANSLATION_ENABLED: bool` — `bool(AUDIO_TRANSLATION_LOCALES)`.
Place these above the existing provider-key block. Do not alter `SUPPORTED_LOCALES`.

**Task 1.3** — In `library/backend/api_modular/__init__.py::_configure_app()` (the function already setting `AUTH_ENABLED` at lines 100-106), set `flask_app.config["AUDIO_TRANSLATION_ENABLED"]` and `["AUDIO_TRANSLATION_LOCALES"]` in the same style.

**Proof:** `python3 -c` importing `localization.config` with the env var unset, set to `""`, set to `"  "`, and set to `"zh-Hans, es"` — print the parsed list and boolean for all four cases. Paste actual output.

---

## Phase 2 — Blueprint Gating

**Task 2.1** — In `_init_route_modules()`, move `init_translations_routes` / `init_subtitles_routes` / `init_translated_audio_routes` / `init_streaming_routes` behind `if flask_app.config["AUDIO_TRANSLATION_ENABLED"]:`, mirroring the `AUTH_ENABLED` block at lines 128-140.

**Task 2.2** — In `_register_extension_blueprints()`, split the function: `maintenance_bp`, `roadmap_bp`, `suggestions_bp`, and **`i18n_bp` stay unconditional** (i18n is layer 1). Guard only `translations_bp`, `subtitles_bp`, `translated_audio_bp`, `streaming_bp`, and the `localization.queue.init_queue` call.

**Task 2.3** — Update the `_register_extension_blueprints` docstring, which currently claims it registers "localization blueprints" unconditionally. A stale docstring here is exactly the residual artifact the Prime Directive forbids.

**Task 2.4** — Audit `__all__` at the bottom of `__init__.py` for now-conditional exports; the module-level `from .subtitles import ...` imports may stay (import is cheap and keeps tests working), but anything asserting registration must not.

**Proof:** Start the API with the capability off; `curl -s -o /dev/null -w '%{http_code}'` against one route from each of the four blueprints → expect `404`. Repeat with capability on → expect non-404. Paste all eight status codes.

---

## Phase 3 — Scanner Hook Gating

**Task 3.1** — `scanner/post_insert.py:141` — the `@register_post_insert("Translation queue")` hook must no-op (with a `logger.debug`, not silence) when the capability is off. Prefer an early return inside the hook over conditional registration, so the registry contents stay stable and inspectable.

**Task 3.2** — `scanner/utils/db_helpers.py:127-129` — same treatment for `enqueue_sampler_for_new_book`. Note the existing comment at `:123` ("MUST NOT break book ingestion") — preserve that guarantee.

**Proof:** Ingest a test book on the test VM with capability off; query `SELECT COUNT(*) FROM translation_queue WHERE audiobook_id = <new_id>` and `... FROM sampler_jobs ...` → expect 0 and 0. Repeat with capability on → expect non-zero. Paste the four counts.

---

## Phase 4 — Capabilities Endpoint + UI Gating

**Task 4.1** — Add `GET /api/system/capabilities` to `library/backend/api_modular/utilities_system.py`, returning at minimum:

```json
{"audio_translation": {"enabled": false, "locales": [], "backends_configured": false}}
```

Unauthenticated-readable (it exposes no secrets and the UI needs it pre-login), consistent with the existing `/api/system/version` route.

**Task 4.2** — `library/web-v2/js/subtitles.js` and `js/streaming-translate.js`: fetch capabilities once on shell init, cache on the client, and hide/disable their entry points when disabled. Do NOT let either module throw when its routes are absent — a disabled capability must produce a clean absence, not a console error.

**Task 4.3** — Any player/library markup that renders a translate or subtitle affordance must be gated too. Per `.claude/rules/development-tools.md`, every action item retains its `title` tooltip when shown.

**Proof:** MANDATORY VISUAL VERIFICATION per `~/.claude/rules/verification.md`. Playwright/Brave screenshot of the player page with capability OFF (no translate control) and ON (control present), plus `browser_console_messages` showing zero errors in the OFF case. Structural checks are NOT acceptable here.

---

## Phase 5 — Loud Failure Guard

This phase is what prevents the new default from inheriting the `od0` zombie pathology. A misconfigured capability must fail visibly at startup, never degrade to silence.

**Task 5.1** — At API startup, when `AUDIO_TRANSLATION_ENABLED` is true, assert that at least one STT backend is configured (`RUNPOD_*` endpoint pair, single-endpoint fallback, or `WHISPER_GPU_HOST`). If none: log `ERROR` and refuse to register the blueprints, with a message naming the exact variables to set.

**Task 5.2** — Same assertion for every listed locale being present in `SUPPORTED_LOCALES`.

**Task 5.3** — `scripts/smoke_probe.sh::_probe_stt_providers` — when the capability is off, print `INFO: audio translation disabled` and skip the STT probe entirely. This is what closes `Audiobook-Manager-6ap`: the probe stops reporting unreachable backends because there is no longer a configured capability to be unreachable.

**Proof:** Run the API with capability ON and no backend → paste the actual `ERROR` line and confirm the four routes are absent. Run `smoke_probe.sh` with capability OFF → paste the `INFO` line. Run it with capability ON + backend → confirm it still probes.

---

## Phase 6 — Install / Upgrade / Manifest Consistency

Per `.claude/rules/upgrade-consistency.md`, **all eight files in the consistency table MUST be physically opened and traced**, and files that legitimately need no change MUST be recorded as confirmed-unchanged in the release notes. Mental review does not count.

**Task 6.1** — `install.sh:2009` — stop unconditionally enabling `audiobook-stream-translate.service`. Enable the five translation units only when the capability is on at install time.

**Task 6.2** — `upgrade.sh` — mirror Task 6.1 in `_enable_unit_smart`'s caller. The `install.sh` comment at `:1953` states the two MUST stay in sync; honour it.

**Task 6.3** — Upgrade path for existing installs: a deployment that currently has the units enabled and no `AUDIOBOOKS_AUDIO_TRANSLATION_LOCALES` set must not silently lose the feature. Decide and implement one of: (a) migrate — detect configured RunPod/whisper vars and pre-populate the locale list, or (b) disable with a prominent upgrade notice. **Recommend (a)** — silent capability loss on upgrade is its own zombie. Record the decision in the CHANGELOG.

**Task 6.4** — `scripts/install-manifest.sh` — the localization files stay in the manifest (code ships regardless; only activation changes). Confirm and record; do not remove entries.

**Task 6.5** — `etc/audiobooks.conf.example` + the `install.sh` config stanza — document the new variable with the empty default and a pointer to the enabling doc.

**Task 6.6** — Delete the dead `AUDIOBOOKS_RUNPOD_*` endpoint IDs from prod `/etc/audiobooks/audiobooks.conf` as part of the same change (closes `Audiobook-Manager-6ap`). **The `AUDIOBOOKS_RUNPOD_API_KEY` and the commented `AUDIOBOOKS_VASTAI_SERVERLESS_API_KEY` on lines 94 and 102 were exposed in a session transcript on 2026-08-26 and MUST be rotated or revoked, not merely deleted.**

**Proof:** Fresh `install.sh` on pristine `test-audiobook-cachyos`; `systemctl is-enabled` for all five translation units → expect `disabled`. Then an upgrade run on a deployment with the units previously enabled → confirm Task 6.3's chosen behaviour actually occurred.

---

## Phase 7 — Test Coverage (the anti-rot phase)

Without this phase the default configuration becomes the untested one, which is the exact mechanism that produced `od0`.

**Task 7.1** — New `library/tests/test_audio_translation_capability.py`:
- capability off → all four blueprint route prefixes absent from `app.url_map`
- capability on → all four present
- `i18n_bp` present in **both** cases (layer 1 never gates)
- `audiobook_translations` queries succeed in **both** cases (layer 2 never gates)
- locale-list parsing: unset / `""` / `"  "` / `"zh-Hans"` / `"zh-Hans, es"` / locale not in `SUPPORTED_LOCALES`
- loud-failure guard fires with capability on and no backend
- `/api/system/capabilities` contract shape

**Task 7.2** — Scanner-hook skip tests for both call sites.

**Task 7.3** — Add a CI matrix dimension in `.github/workflows/` running the suite with the capability OFF. Pin it as a required check per the 12-strict gate established 2026-08-26.

**Task 7.4** — Audit the existing 42 translation test modules: any that assume the routes are registered must set the capability explicitly in a fixture rather than relying on an ambient default. This is the largest single work item in the plan — budget for it.

**Proof:** Full `pytest` run, **unfiltered result line from every test binary** (see `~/.claude/rules/verification.md` §1 — a truncated `| head` on a test run is how a red suite was once reported green). Report the OFF-matrix and ON-matrix counts separately.

**Falsification requirement:** temporarily invert the capability check in `_register_extension_blueprints` and confirm `test_audio_translation_capability.py` goes **RED**. A test that cannot fail is not a test. Revert immediately and note the observed RED output in the release notes.

---

## Phase 8 — Documentation

**Task 8.1** — New `docs/AUDIO-TRANSLATION-ENABLING.md`: what the capability does, the three layers and which this one is, how to enable per-locale, backend options (RunPod serverless, Vast.ai, self-hosted `whisper-gpu`, containerised `docker/whisper-server/`), realistic cost expectations, and how to verify it is working.

**Task 8.2** — Update `README.md`, `docs/ARCHITECTURE.md`, `docs/MULTI-LANGUAGE-SETUP.md`, `docs/SERVERLESS-OPS.md`, `docs/TROUBLESHOOTING.md` to state that audio translation is opt-in and off by default. `docs/SERVERLESS-OPS.md` currently opens with "All STT traffic flows through serverless GPU endpoints on RunPod" — that sentence is already false and must be corrected regardless.

**Task 8.3** — Add the new doc to the project-documentation table in `CLAUDE.md`.

**Task 8.4** — CHANGELOG entry per `~/.claude/rules/changelog.md` (bold title, backticked refs, em dashes, no trailing periods) + VERSION bump.

**Proof:** `markdownlint` and `codespell` clean on every touched file; paste both command outputs.

---

## Acceptance Criteria

The work is complete when **all** of the following are demonstrated with cited proof artifacts:

1. Fresh install with no configuration: zero translation routes registered, zero translation units enabled, zero GPU workers running, smoke probe reports the capability disabled rather than backends unreachable.
2. Setting `AUDIOBOOKS_AUDIO_TRANSLATION_LOCALES=zh-Hans` and restarting: full pipeline returns, all four blueprints register, units enable.
3. UI OFF: no translation affordances rendered, zero console errors — proven by screenshot, not by inspecting markup.
4. UI ON: affordances present and functional.
5. Capability ON with no backend: loud startup `ERROR` naming the missing variables. Never silent.
6. **No translation table dropped, truncated, or migrated.** `SELECT COUNT(*)` on `chapter_subtitles` (13,267), `chapter_translations_audio` (5,516), `streaming_segments` (26,202), `sampler_jobs` (1,884), `translation_queue` (1,860) unchanged before and after.
7. Layers 1 and 2 unaffected: UI renders in zh-Hans, pinyin sort works, translated titles display — all with the capability OFF.
8. CI green on both matrix dimensions; falsification RED observed and reverted.
9. All eight `upgrade-consistency.md` files physically traced, with unchanged ones recorded as confirmed.
10. `Audiobook-Manager-6ap` closable: prod config no longer names dead endpoints, exposed keys rotated.

---

## Rollback

Single-variable rollback: set `AUDIOBOOKS_AUDIO_TRANSLATION_LOCALES` to the previous locale list and restart `audiobook-api`. No schema change, no data migration, no code revert — which is the principal reason this option was chosen over extraction. If the code changes themselves prove faulty, `git revert` the range; the BTRFS pre-test snapshot from `/test` Phase 1 is the outer safety net.

---

## Risks

| Risk | Mitigation |
|---|---|
| **Task 7.4 is larger than it looks** — 42 test modules may carry ambient assumptions about route registration | Do Phase 7 immediately after Phase 2, before the UI work, so the true cost surfaces early rather than at the end |
| **Upgrade silently disables a working deployment** | Task 6.3(a) migration path + explicit CHANGELOG note + a proof run against a deployment that had the units enabled |
| **The OFF path rots anyway** | Task 7.3 makes it a required CI check, not an optional one |
| **`od0` gets masked rather than fixed** — turning the capability off makes the dead sampler stop being visible | Do not close `od0`. Re-diagnose it against a live backend if the capability is ever re-enabled. Record this explicitly in the `od0` issue as part of this work |
| **Scope creep into Option B** | The OUT-of-scope list above is binding. Blueprint discovery, plugin mechanisms, and code extraction are separate decisions |
| **Layer boundary proves leakier than measured** | Preconditions table is falsifiable and cited; if any row fails during implementation, stop and re-plan rather than working around it |

---

## Suggested Execution Order

Phases 1 → 2 → **7** → 3 → 5 → 4 → 6 → 8.

Phase 7 is deliberately pulled forward to third position: it is the largest unknown, and discovering its true cost after the UI and install work is done would be the expensive ordering. Phase 4 (visual verification) sits late because it needs a stable capabilities endpoint from Phase 4.1 and the loud-failure semantics from Phase 5.
