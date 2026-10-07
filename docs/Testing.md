# Testing

This page covers how to run the two test suites, exactly what they assert, what the specification
required and where each requirement is met, what is still not covered, the reasoning behind what this
project chooses to test, and the lint and typecheck gates. It is for anyone adding code to the
repository, and for anyone judging how much of the current behaviour is actually pinned.

**Status:** 710 backend tests across 37 files and 55 frontend tests across 3 files, all passing
but one deliberate skip: `test_unimplemented_routes_answer_501_with_a_phase` is parametrised over
`DEFERRED_ROUTES`, which emptied when Phase 9 built the last stubbed route, and pytest reports an
empty parameter set as a skip. The four tests the specification names for Phase 8 — feature parity,
the schema-hash guard, dedup under burst, the API contract — all exist. No CI workflow runs either
suite (the repository's one workflow publishes these docs); the gate is `make test` before a push.
See [Roadmap](Roadmap.md).

---

## Running the suites

| Target | make | PowerShell | Underlying command |
| ------ | ---- | ---------- | ------------------ |
| Both suites | `make test` | `./make.ps1 test` | backend then frontend, in that order |
| Backend only | `make test-backend` | `./make.ps1 test-backend` | `cd backend && uv run pytest` |
| Frontend only | `make test-frontend` | `./make.ps1 test-frontend` | `cd frontend && npm run test` |
| Frontend in watch mode | — | — | `cd frontend && npm run test:watch` |

Neither suite needs a running server or a dataset. The backend builds its own app via `create_app()`
inside a `TestClient`, against a sandbox `tests/conftest.py` sets up before `app` is imported:
`IDS_DATABASE_URL` and `IDS_ARTIFACTS_DIR` point at a throwaway directory, the session fixture runs
`alembic upgrade head` on that database, and installs the committed model release beside it — so the
suite never touches `data/ids.db` or `backend/artifacts/`, and passes on a clean clone. The
frontend's live-integration block skips itself when no backend answers.

pytest is configured in `backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q --strict-markers"
markers = [
    "integration: needs a live backend process (deselect with -m 'not integration')",
]
```

`pythonpath = ["."]` is what lets tests import both `app` and `training` without an editable install.
`--strict-markers` makes a typo'd marker an error rather than a silently ignored decorator. Useful
invocations:

```
uv run pytest tests/test_config.py            # one file
uv run pytest -k "schema_hash"                # by name
uv run pytest -m "not integration"            # skip live-process tests
uv run pytest -q --collect-only               # what would run
```

vitest is configured inside `frontend/vite.config.ts` rather than a separate file, so the test run
shares the `@/` alias and the repo-root env directory with the app:

```ts
test: {
  environment: 'jsdom',
  globals: true,
  setupFiles: [`${here}src/test/setup.ts`],
  include: ['src/**/*.{test,spec}.{ts,tsx}'],
}
```

`src/test/setup.ts` does two things and nothing else: it registers the `@testing-library/jest-dom`
matchers for vitest, and it runs `cleanup` after every test so one test's DOM cannot leak into the
next.

---

## What is covered today

### Backend — `backend/tests/`

Grouped by the phase that brought each file; a file later phases extended keeps its first phase.

| Test file | Tests | Invariant it protects |
| --------- | ----: | --------------------- |
| `test_api_surface.py` | 57 | The whole v1 surface is in the **OpenAPI schema** (not just in `app.routes`), because the schema is what the frontend generates its types from. `IMPLEMENTED_ROUTES` lists all twenty-six operations and each must answer something other than 501; a partition test compares the listed pairs with the schema's operations exactly, so a route nobody listed fails the suite. `DEFERRED_ROUTES`, which held the routes still answering 501 with their exact phase string, emptied with Phase 9 and stays for any future stub. No path contains `block`, `drop` or `quarantine` — the absence of a containment endpoint is asserted, not assumed. A pickled bundle whose `schema_hash` disagrees with its `feature_order` raises `SchemaHashMismatch` at load. |
| `test_config.py` | 8 | The false-positive budget arithmetic: `40 * 8 = 320` alerts/day and a target FPR of `3.2e-4`, so the Phase 2 threshold derives from a stated cost model rather than `0.5`. Budget inputs must be positive. `IDS_ALLOW_AUTO_BLOCK=true` raises a `ValidationError` whose message contains "never drops traffic". Relative paths anchor to the repo root rather than the working directory, absolute paths pass through untouched, relative SQLite URLs are rewritten absolute, a Postgres URL passes through byte for byte, and comma-separated CORS origins parse with whitespace trimmed. |
| `test_features.py` | 16 | The shared feature contract that both training and serving import. Column names normalise to snake_case for the real header quirks in the published CSVs (leading and trailing whitespace, `Flow Bytes/s`, the genuine duplicate `Fwd Header Length.1`). Normalisation preserves order. The schema hash is **order-sensitive**, stable across calls and `sha256:`-prefixed. Identity columns are on the leakage denylist while `destination_port` deliberately is not. A preprocessing bundle round-trips through disk with its hash intact. |
| `test_health.py` | 5 | `GET {prefix}/health` returns exactly `{status, model_version, uptime_s}`. A bundle loaded from an empty directory reports `ok` and `unloaded`, and the endpoint reports whatever version is actually on disk. Uptime is non-negative and advances. The prefix is configuration, so an unprefixed `/health` must 404. |
| `test_schema_portability.py` | 12 | The ORM stays swappable between SQLite and Postgres. Every column type compiles for both dialects; primary keys compile to `BIGINT` on Postgres and `INTEGER` on SQLite; no native enum types and no `sqlite_*` table options; every constraint carries a deterministic name. The check constraints are exercised against a real in-memory database: an `UNCLASSIFIED_ANOMALY` cannot carry a family, a `KNOWN` alert must carry one, the vocabularies reject unknown values, and `model_versions.version` is unique. |
| `test_clean.py` | 35 | Phase 1 — every documented CICIDS2017 defect is handled explicitly rather than papered over: header whitespace, `Inf` and `NaN` in the rate columns from zero-duration flows, exact-duplicate rows, zero-variance columns, negative durations and IATs, and the label spellings that would otherwise split one attack family across several classes. |
| `test_split.py` | 18 | Phase 1 — the split is by capture day and never shuffled; the benign-only Stage 2 training set is asserted attack-free in code; rows dropped from one split never reappear in another. |
| `test_feature_matrix.py` | 19 | Phase 1 — the serving path reproduces the training matrix element for element, and feeding the right columns in the wrong order does not change the result. |
| `test_pipeline.py` | 13 | Phase 1's acceptance criteria as properties of the written files: no NaN or Inf survives, no rows are shared across splits, the bundle carries all five keys, and the scaler was fitted on train alone. Plus the warning Phase 1 emits when refitting the bundle would orphan a trained champion. |
| `test_console.py` | 2 | A report degrades a character rather than crashing the run on a cp1252 console after the real work is finished. |
| `test_labels.py` | 36 | Phase 2 — every published label maps, nothing maps by substring (`Web Attack Brute Force` is not `brute_force`), an unmapped label raises rather than becoming benign, and the support floor holds a class of eleven rows out of the vocabulary while reporting that it did. |
| `test_metrics.py` | 10 | Phase 2 — `tau_sup` is the smallest threshold inside the budget and never `argmax` or `0.5`; a budget no observed score satisfies is reported rather than hidden; alert volume is projected from the false-positive rate rather than from a lab day's attack density. |
| `test_supervised.py` | 29 | Phase 2 end to end — the model and its preprocessing are written as a matching pair, the serving loader accepts what training wrote and refuses a mismatched schema, a weaker challenger does not displace the champion, the report carries every section the checkpoint asks for, and a LightGBM champion **unpickles in a subprocess** (the check that catches a class pickled from `__main__`). Phase 4 added a frozen preprocessing bundle and a fixed-hyperparameter fit that sizes its output layer from the fold. |
| `test_autoencoder.py` | 39 | Phase 3 end to end — one attack row in the benign training set is **fatal**; the input transform is applied exactly once on both paths; `tau_anom` is a benign percentile with the budget-equivalent threshold reported beside it; the persisted histogram keeps both clipped tails; a flow scores the same alone and in a batch; the serving loader reaches `stage2_ready` on what training wrote and refuses a network whose input width disagrees with `feature_order`. |
| `test_fusion.py` | 28 | Phase 4 — the cascade and the serving path that runs it. Both thresholds are **inclusive**. Stage 2 is never consulted for a row Stage 1 named, and its score reads `NaN` there — *not asked*, rather than a zero. The family comes from the best *attack* class, never `argmax` over all columns. An `UNCLASSIFIED_ANOMALY` carries no family. Either stage may be absent and the cascade degrades; with neither it raises instead of returning nulls that read as *no attacks found*. |
| `test_loao.py` | 36 | Phase 4 — a held-out family is genuinely removed and the model genuinely refitted; the stage columns and the **Missed** column account for every row; a family with no rows is *unmeasurable*, never 0%; a held-out family can be caught but never **named**; small supports carry a Wilson interval; the record contains no `NaN`. |
| `test_alerts.py` | 46 | Phases 5–6 — the queue, the drawer, verdicts and host context against a real in-memory database with the real constraints. Sorted by risk, never time; keyset paging covers every row exactly once even when a higher-risk alert lands mid-page, and ties on the rounded risk lose no rows; a garbage cursor is 422, not a silent reset. An anomaly's detail names no technique and no family. A verdict records the alert's own model version and leaves its status alone. Related alerts are measured from the anchor, both ways. The stats strip counts open alerts, floors its rate window instead of dividing by zero, and carries no accuracy field. Bulk dismiss reports stale ids rather than failing, and writes no verdict. The verdict filter's `none` means unjudged, not unsure. |
| `test_analytics.py` | 20 | Phases 5–7 — aggregates computed from the database: known and unclassified split, empty buckets as zeros, `UNSURE` outside the true-positive rate (null with nothing decided), the heatmap's axis from the table rather than the data, unclassified anomalies counted beside it. The feedback loop counts pending labels as those no retrain consumed, groups labels by the model version they judged, and reports retraining as available. |
| `test_events.py` | 10 | Phase 5 — the SSE broker: every subscriber receives each event, a full queue drops and counts rather than blocking the scoring loop, and the active source is reported. |
| `test_explain.py` | 17 | Phase 5 — both explainers against real artifacts: SHAP contributors ordered by magnitude with shares of the full attribution, anomaly contributors are the worst-reconstructed features, empty batches return nothing. `narrate` names the family for a known attack, never a family or a technique for an anomaly, mentions the observed port only when a port feature contributed, and has a phrase for every real feature. |
| `test_metrics_api.py` | 19 | Phases 5–6 — `/metrics/*` serves the measured numbers rather than recomputing them; accuracy is present but not the headline; the threshold projection reads both figures off one axis and divides by the shift, not 24; a threshold past the histogram says so; the anomaly histogram serves one count per bin on the same edges. |
| `test_pipeline_alerts.py` | 14 | Phase 5 — the dedupe upsert and `ingest_batch` against real artifacts and a real database: one row per key, the higher risk kept and the first evidence preserved, a new window a new row; known and anomaly alerts fully formed and satisfying `family_matches_kind`; a burst collapses to one row with every hit published; prior-alert counts come from earlier batches; a naive timestamp is rejected; each explainer runs only for its own stage. |
| `test_playbook.py` | 21 | Phase 5 — the reviewed lookups: every attack family has a technique and a playbook, an unclassified anomaly gets no technique and an honest, populated playbook, no action is an automatic containment instruction, the two tables agree on the families, and an unknown family is refused rather than guessed. |
| `test_replay.py` | 23 | Phases 5–8 — the replay engine and the stream: training rows are not replayable; speed scales the delay and never the batch; a bad dataset or speed leaves nothing armed; starting twice or stopping nothing is a conflict, and stop awaits the task so a following start cannot race it. SSE frames survive a payload that would break the protocol, the stream is 503 with no source, a disconnecting client is unsubscribed, and a cross-thread publish reaches a waiting subscriber. |
| `test_risk.py` | 30 | Phase 5 — the queue's ordering key: each stage's base is zero exactly at its threshold, Stage 2's spreads alerts a raw percentile would crush, scores never fall as confidence or error rise, the lifts saturate and cap, a missing input raises, and severity bands use the real vocabulary. |
| `test_score.py` | 6 | Phase 5 — `POST /score`: one result per row in order, a string-valued feature refused with a 422 naming it, an empty batch is 200, and no loaded stage is 503 rather than 500. |
| `test_topology.py` | 36 | Phase 5 — the lab host inventory: derived addresses are deterministic and spread across each family's documented hosts rather than collapsed onto one pair, the attacker side never enters the asset inventory, criticality tiers match the brief's assignment, and provenance marks exactly the derived fields. |
| `test_drift.py` | 31 | Phase 7 — PSI arithmetic: an unmoved distribution scores zero, an emptied bin scores finite, counts and mismatched bins are refused, quantile edges are open-ended and collapse for near-constant features, and retrain is recommended on one feature rather than the average. Sampling is systematic across batches and keeps rows that raised no alert; pruning keeps the newest. The benign refit pool admits only explicit false positives, caps any one host, and reports a pool below its floor as unusable. |
| `test_drift_api.py` | 20 | Phase 7 — no snapshot is reported as none, not as zero; the latest is served worst feature first, the series oldest first. The registry counts the alerts each version scored, re-registering does not grow it, and a new champion archives the old one. A retrain request is 202 and fits nothing, a second is 409 and one with no labels 422; the history keeps the declined runs; no route trains a model. |
| `test_drift_job.py` | 8 | Phase 7 — the nightly job against real artifacts: a snapshot per feature, attributed to the model that scored the window; too few samples records the run and scores nothing; no reference, or one from another feature contract, is a refusal; a dry run stores nothing; `--source` keeps live and replay windows apart; the reference is cut at `IDS_DRIFT_BINS`. |
| `test_retrain.py` | 10 | Phase 7 — the retrain pipeline and the guarded Stage 2 refit: the served version names both stages, publishing a challenger keeps Stage 2's section of the model card, a refused pool leaves the autoencoder untouched, a refit that does not improve is declined and writes nothing, a promoted one archives the champion, and a retrain freezes the feature contract. |
| `test_api_contract.py` | 2 | Phase 8 — the served OpenAPI schema equals the committed snapshot `tests/snapshots/openapi.json`, and every operation declares a response schema. With the frontend's contract test, the wire format cannot change without `make openapi`. |
| `test_parity.py` | 3 | Phase 8 — train/serve parity on the shipped models and real flows: the serving matrix is the training matrix element for element, a reordered payload builds the same matrix, and `POST /score` returns the offline evaluation's scores. |
| `test_release.py` | 8 | Phase 8 — the committed release matches its manifest, installs into an empty directory and serves both stages; a tampered release or a missing manifest installs nothing; a model somebody trained is never replaced (`--force` keeps what it displaces); an untouched earlier release is upgraded; a library-version mismatch is reported. |
| `test_seed.py` | 4 | Phase 8 — `make seed`: the demo sample is held-out traffic and its card says so; the seed writes exactly what the pipeline produces from it and nothing else, verdicts included; it refuses to mix into existing data; `--reset` keeps the audit trail. |
| `test_flowmeter.py` | 9 | Phase 9 — the meter reproduces CICFlowMeter-V3 on packets small enough to work out by hand: the columns of a TCP exchange, the permuted first-packet flags, the teardown after the first FIN as its own flow, UDP's borrowed TCP header length, the flow timeout restarting in the same orientation, zero-duration flows counted and not scored, decoding across link layers and IP versions, a pcap round trip, and exactly the 70 training columns. |
| `test_live_capture.py` | 10 | Phase 9 — the live path through the real routes, meter, models and pipeline, driven by hand-built pcaps: an interface outside `IDS_LIVE_INTERFACES` is 403 and a pcap outside the capture directory 404; alert mode is 409 before a burn-in; shadow mode scores every flow and alerts no one; the calibration cuts the percentile, keeps the burn-in's histogram and refuses a thin burn-in; alert mode writes observed endpoints at the local threshold and ranks them against it; replay and capture never write at once. |
| **Total** | **710** | |

`conftest.py` supplies the shared fixtures. The session-scoped `sandbox` migrates the throwaway
database and installs the release (above); `client` enters the `TestClient` context manager, which
is what actually exercises the lifespan — artifact loading, the schema-hash check, the registry
write; `api_prefix` is read from settings rather than hardcoded; and `db_session` is a throwaway
in-memory SQLite database built from the ORM metadata, for the tests that assert constraint
behaviour. Phase 1 added synthetic CICIDS2017 frames carrying all five documented defects, Phase 2
small train/validation/test splits that reproduce the real ones' structure plus a frozen budget stub,
Phase 3 `phase3_benign`, and Phase 4 `two_stage_artifacts` — a promoted Stage 1 and a fitted Stage 2
built by running the real Phase 2 and Phase 3 code on those frames, which is what makes a test of
the cascade a test of what the API actually loads. Phase 9 added `tests/packets.py`, which builds
Ethernet frames and classic pcaps by hand, so the flow meter and the live path are tested on packets
whose every field is known.

### Frontend

| Test file | Tests | Invariant it protects |
| --------- | ----: | --------------------- |
| `src/pages/SystemHealth.test.tsx` | 6 | Against a stubbed `fetch`: the three health fields render; a skeleton with an accessible `role="status"` shows before the first response; the request goes to the configured path rather than a literal; **no accuracy figure appears**; a 5xx surfaces "backend unreachable" after the query client's retries. One more block probes `${VITE_DEV_PROXY_TARGET}/api/v1/health` and skips itself when nothing answers; when a backend is running, it asserts the version on screen is the one the live process just reported. |
| `src/pages/screens.test.tsx` | 48 | The seven screens against a stubbed API surface — `src/test/harness.tsx` answers every endpoint from `src/test/fixtures.ts`, so a screen that reads five endpoints is tested with all five. The queue is the landing page, ordered by risk, with dedupe counts; anomalies are distinct and one click away; the drawer asks why, what and how-to-fix in that order, refuses to invent a technique or a playbook for an anomaly, holds a live alert to the threshold it was decided at, and offers no containment action; a verdict refetches the queue and bulk dismiss sends one request and no verdict; the threshold is a real keyboard slider that asks the server for each projection; PR and ROC carry their caption; the LOAO table has real misses and a named-correctly column; the drift banner rises on one feature past 0.25; the registry is the audit trail; retraining is queued, never performed, with the declined runs shown; analytics keeps never-fired techniques as visible zeros; and no screen mentions accuracy. |
| `src/types/contract.test.ts` | 1 | Phase 8 — `src/types/api.d.ts` is exactly what the committed OpenAPI snapshot generates, so a hand edit or a stale regeneration fails. |
| **Total** | **55** | |

The anti-accuracy tests deserve their own note. A hero tile reading "99.8% ACCURATE" is the exact
failure mode this project is built to avoid: on traffic that is roughly 99% benign, a model that
always predicts "benign" scores 99%. The dashboard avoiding that number is not enough — the tests
make reintroducing it, on any screen, a build failure. See [Anti-Patterns](Anti-Patterns.md).

---

## Phase 8's required tests, and what is still not covered

The specification's Phase 8 packaging requirements name four tests. All four exist.

| Required test | Where | What it asserts |
| ------------- | ----- | --------------- |
| **Feature parity between training and serving** | `test_parity.py`, `test_fusion.py` | On the shipped models and real flows, the matrix the serving path builds from JSON equals the training matrix element for element, a reordered payload builds the same matrix, and `POST /score` returns the offline evaluation's scores. The behavioural half — the same rows scored as loose dicts through `score_batch` and as a frame the way training does — has been in `test_fusion.py` since Phase 4. |
| **Schema-hash mismatch** | `test_api_surface.py`, `test_supervised.py`, `test_autoencoder.py` | A bundle whose `schema_hash` disagrees with its `feature_order` raises `SchemaHashMismatch` from `load_bundle`; the serving loader refuses a Stage 1 model from a different schema and a Stage 2 network whose input width disagrees. `load_bundle` runs in the lifespan, uncaught, so the process does not come up; that last step is by construction rather than separately tested. |
| **Dedup under burst load** | `test_pipeline_alerts.py` | One source host's burst inside a window collapses to one row with each hit published and the higher risk kept, and the next window opens a new row. Measured too: a full 100x replay of the test day folded 96,370 alerting flows into 6 queue rows (`docs/Roadmap.md`). |
| **API contract** | `test_api_contract.py`, `contract.test.ts`, the route tests | The whole served schema is pinned to the committed snapshot, the generated TypeScript types to the snapshot, and every route's behaviour by its own file (`test_alerts.py`, `test_replay.py`, `test_score.py`, `test_drift_api.py`, `test_live_capture.py`, …). |

What is still not covered, stated so it does not get lost:

- **No CI runs the suites.** `.github/workflows/` holds only the docs-publishing workflow, so a push
  is gated by whoever runs `make test` first.
- **No test compares the migrated schema with the ORM directly.** The suite applies the whole
  Alembic chain and the API tests run on it, but a drift they never exercise is caught only by the
  manual autogenerate check on [Code: Backend Migrations](Code-Backend-Migrations.md).
- **Live capture's socket has no automated test.** Opening an `AF_PACKET` socket needs root or
  `CAP_NET_RAW` and a live interface. The tests drive the identical metering and scoring path from
  pcaps, and the burn-in recorded in `reports/phase9_live.md` is the run that exercised the socket.
- **Phase 9's self-run attack exercise is not a test and has not been run.** It belongs in an
  isolated lab the operator owns; see the README's "Real traffic" section.

---

## Testing philosophy for this project

**A test that pins an honest 501 was worth writing.** It would have been easy to leave unbuilt routes
unregistered. Registering all sixteen in Phase 0 meant the OpenAPI schema — and therefore the
generated TypeScript types — was complete from the first day, and the frontend could be built against
the real contract instead of a guess. The 501 body was machine-readable (`detail`, `phase`,
`endpoint`), so `ApiError.isNotImplemented` let the UI say "not built yet" rather than showing a
generic failure, and `test_unimplemented_routes_answer_501_with_a_phase` kept that property true
until Phase 9 built the last stub: the moment someone filled a route with fabricated sample data to
"make the screen look right", the test failed. `test_implemented_routes_do_not_answer_501` keeps the
other half true now. The honest surface is the thing being protected.

**Train/serve parity is the single most valuable test this repository contains.** Every other
failure mode in this system announces itself. A missing table raises. A bad threshold shows up in the
metrics. But feeding a model the right columns in the wrong order produces plausible-looking garbage
scores and raises nothing at all — no exception, no warning, no log line. The dashboard fills with
alerts, every number is wrong, and nothing indicates it. That is why `features.py` is imported by
both paths rather than reimplemented, why the feature order is persisted in the bundle, why the
schema hash is order-sensitive (`compute_schema_hash(["a","b"]) != compute_schema_hash(["b","a"])`
is an explicit test), and why a hash mismatch is fatal at startup rather than logged.
`test_parity.py` is the one that closes the loop: it compares the two paths' matrices directly, on
the shipped models and real flows, instead of trusting that they share a module. Live capture is the
same rule one layer earlier — `test_flowmeter.py` pins the meter to the quantity CICFlowMeter-V3
produced, quirks included, because a "correct" meter would be skew too.

**No test may assert against mock model output.** A test that needs a model uses a real one:
`two_stage_artifacts` trains both stages with the real Phase 2 and Phase 3 code on small synthetic
frames, and the sandbox installs the committed release for the tests that need the shipped models.
Writing `assert recall > 0.7` against a stubbed scorer would produce a green suite that means
nothing, and worse, it would make the number look measured. The rule the project follows: every number that
appears anywhere — in a test, in the dashboard, in the README — traces to a real model run on real
data, or it is marked as not measured yet. Fixtures may stub *transport* (the frontend stubs `fetch`
to test rendering) but never *inference*.

**Assert the constraint, do not merely honour it.** Several tests exist only to make an absence
loud. `test_no_route_mentions_blocking` fails if anyone adds a containment endpoint.
`test_auto_block_cannot_be_enabled` fails if the validator is removed.
"renders no accuracy figure anywhere" and "never mentions accuracy" fail if a hero accuracy tile returns on any screen.
`test_destination_port_is_not_silently_dropped` fails if someone quietly adds the column to the
leakage denylist and skips the ablation the specification asks for. These cost almost nothing and
convert a written rule into an enforced one.

**Test the portable surface before the swap, not on the day of it.** `test_schema_portability.py`
compiles every column type against both dialects in CI. A SQLite-only type would otherwise surface
the first time someone points `IDS_DATABASE_URL` at Postgres, in whatever environment that happens to
be.

---

## Linting and type checking

| Target | make | PowerShell | Underlying command |
| ------ | ---- | ---------- | ------------------ |
| Lint backend + typecheck frontend | `make lint` | `./make.ps1 lint` | `uv run ruff check .` then `npm run typecheck` |
| Format and autofix the backend | `make format` | `./make.ps1 format` | `uv run ruff format .` then `uv run ruff check --fix .` |
| Typecheck the frontend only | `make typecheck` | `./make.ps1 typecheck` | `npm run typecheck` |

### Backend — ruff

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "W"]
ignore = ["B008"]

[tool.ruff.lint.per-file-ignores]
"alembic/versions/*" = ["E501"]
```

| Rule set | Enforces |
| -------- | -------- |
| `E`, `W` | pycodestyle errors and warnings — spacing, indentation, a 100-character line limit. |
| `F` | pyflakes — unused imports and variables, undefined names, redefinitions. |
| `I` | isort — one import order, so diffs are about code rather than import shuffling. |
| `UP` | pyupgrade — modern syntax for the target version, which is why the codebase uses `str | None` and `list[str]` rather than `Optional` and `List`. |
| `B` | flake8-bugbear — likely bugs such as mutable default arguments and loop-variable capture. |

`B008` (function call in a default argument) is ignored globally because `Depends()` in a parameter
default is FastAPI's documented idiom. `E501` is ignored inside `alembic/versions/` because
autogenerated migrations produce long literal lines that are not worth rewrapping by hand. Every
in-file suppression carries its reason: `noqa: E402` on the deferred route imports in
`app/routes/__init__.py`, which must come after the `not_implemented` helper, and on the test
imports that must follow the sandbox's environment; `noqa: S301` on the four first-party artifact
unpickles; `noqa: BLE001` where a broad `except` is deliberate and reported rather than swallowed
(the registry write at startup, a live capture's failure, a retrain run's failure, an unreadable
champion artifact); and `noqa: F401` on the models import Alembic's autogenerate needs.

`make format` runs `ruff format` before `ruff check --fix`, so formatting and autofixable lint land in
one pass.

### Frontend — tsc

`npm run typecheck` runs the compiler twice, once per project, because the app and the build tooling
target different environments:

```
tsc -p tsconfig.json      --noEmit   # src/**, DOM + vite/client + vitest globals, ES2022
tsc -p tsconfig.node.json --noEmit   # vite.config.ts + scripts/*.mjs, node types, ES2023
```

| Option | Effect |
| ------ | ------ |
| `strict: true` | The full strict family, including `strictNullChecks` — an optional API field must be narrowed before use. |
| `noUnusedLocals`, `noUnusedParameters` | Dead bindings are errors, not warnings. |
| `noFallthroughCasesInSwitch` | A `case` without a terminator is an error. |
| `noUncheckedSideEffectImports` | A bare `import './x'` must resolve. |
| `verbatimModuleSyntax` | Type-only imports must say `import type`, so the emitted bundle contains exactly the imports written. |
| `isolatedModules` | Every file must transpile independently, which is what the esbuild-based pipeline requires. |
| `paths: { "@/*": ["src/*"] }` | The `@/` alias, mirrored in `vite.config.ts` so the compiler and the bundler resolve identically. |

`npm run build` runs `typecheck` before `vite build`, so a type error fails the production build
rather than shipping. The generated `src/types/api.d.ts` is typechecked like any other file — if the
backend contract changes and the file is stale, the mismatch surfaces here — and
`src/types/contract.test.ts` fails if it differs from what the committed snapshot generates.
Regenerate with `make openapi`, which rewrites the snapshot from the app and the types from the
snapshot without a running server (or `make gen-types` against a running backend); never hand-edit
it.

There is no ESLint configuration in the repository. The strict compiler settings plus the test suite
are the current gate.
