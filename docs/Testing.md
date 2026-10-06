# Testing

This page covers how to run the two test suites, exactly what they assert today, which required
tests do not exist yet and which phase adds them, the reasoning behind what this project chooses to
test, and the lint and typecheck gates. It is for anyone adding code to the repository, and for
anyone judging how much of the current behaviour is actually pinned.

**Status:** 328 backend tests across sixteen files and 6 frontend tests in one file are shipped and
passing. Phase 1 brought the data-pipeline tests, Phase 2 the 77 that cover the class vocabulary, the
threshold arithmetic and the Stage 1 training path, Phase 3 the 39 that cover the benign-only fit
and the Stage 2 artifact, and Phase 4 the 52 that cover the cascade, the serving path and the
hold-out loop. Some of what the specification requires for Phase 8 still does not exist;
it is listed below with what each missing test must assert. See [Roadmap](Roadmap.md).

---

## Running the suites

| Target | make | PowerShell | Underlying command |
| ------ | ---- | ---------- | ------------------ |
| Both suites | `make test` | `./make.ps1 test` | backend then frontend, in that order |
| Backend only | `make test-backend` | `./make.ps1 test-backend` | `cd backend && uv run pytest` |
| Frontend only | `make test-frontend` | `./make.ps1 test-frontend` | `cd frontend && npm run test` |
| Frontend in watch mode | — | — | `cd frontend && npm run test:watch` |

Neither suite needs a running server. The backend builds its own app via `create_app()` inside a
`TestClient`; the frontend's live-integration block skips itself when no backend answers.

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

| Test file | Tests | Invariant it protects |
| --------- | ----: | --------------------- |
| `test_api_surface.py` | 36 | The full v1 route surface from the specification exists in the **OpenAPI schema** (not just in `app.routes`), because the schema is what the frontend generates its types from. The 501 assertion is split in two since Phase 5: the three still-deferred routes must answer `501` with the **exact** phase string each reports, and the thirteen implemented routes must **not** answer 501 at all. A third test asserts the two lists are disjoint and together cover all sixteen pairs — without it a route could leave one list without joining the other and stop being checked by either. No path in the schema contains `block`, `drop` or `quarantine` — the absence of a containment endpoint is asserted, not assumed. A pickled bundle whose `schema_hash` disagrees with its `feature_order` raises `SchemaHashMismatch` at load. |
| `test_config.py` | 8 | The false-positive budget arithmetic: `40 * 8 = 320` alerts/day and a target FPR of `3.2e-4`, so the Phase 2 threshold derives from a stated cost model rather than `0.5`. Budget inputs must be positive. `IDS_ALLOW_AUTO_BLOCK=true` raises a `ValidationError` whose message contains "never drops traffic". Relative paths anchor to the repo root rather than the working directory, absolute paths pass through untouched, relative SQLite URLs are rewritten absolute, a Postgres URL passes through byte for byte, and comma-separated CORS origins parse with whitespace trimmed. |
| `test_features.py` | 16 | The shared feature contract that both training and serving import. Column names normalise to snake_case for the real header quirks in the published CSVs (leading and trailing whitespace, `Flow Bytes/s`, the genuine duplicate `Fwd Header Length.1`). Normalisation preserves order. The schema hash is **order-sensitive**, stable across calls and `sha256:`-prefixed. Identity columns (`flow_id`, `source_ip`, `destination_ip`, `source_port`) are on the leakage denylist while `destination_port` deliberately is not, so Phase 1 has to decide about it explicitly instead of dropping it by default. A preprocessing bundle round-trips through disk with its hash intact. |
| `test_health.py` | 5 | The Phase 0 checkpoint contract. `GET {prefix}/health` returns exactly `{status, model_version, uptime_s}` — no more fields. A bundle loaded from an empty directory reports `ok` and `unloaded`, because a clean clone without a model is the expected state rather than a failure; the endpoint itself is asserted to report whatever version is actually on disk, so the test holds both before and after a local training run. Uptime is non-negative and monotonically advances. The prefix is configuration, so an unprefixed `/health` must 404. |
| `test_schema_portability.py` | 12 | The ORM stays swappable between SQLite and Postgres, so moving backends is a change to `IDS_DATABASE_URL` and nothing else. Every column type compiles for both dialects. Primary keys compile to `BIGINT` on Postgres and `INTEGER` on SQLite, because SQLite only auto-assigns rowids for `INTEGER PRIMARY KEY`. No native enum types (vocabularies are `String` plus `CheckConstraint`). No `sqlite_*` table options. Every constraint carries a deterministic name. The check constraints are then exercised against a real in-memory database: an `UNCLASSIFIED_ANOMALY` cannot carry a family, a `KNOWN` alert must carry one, the severity and verdict vocabularies reject unknown values, and `model_version.version` is unique. |
| `test_clean.py` | 35 | Every documented CICIDS2017 defect is handled explicitly rather than papered over: header whitespace, `Inf` and `NaN` in the rate columns from zero-duration flows, exact-duplicate rows, zero-variance columns, negative durations and IATs, and the label spellings that would otherwise split one attack family across several classes. |
| `test_split.py` | 18 | The split is by capture day and never shuffled; the benign-only Stage 2 training set is asserted attack-free in code; rows dropped from one split never reappear in another. |
| `test_feature_matrix.py` | 19 | The serving path reproduces the training matrix element for element, and feeding the right columns in the wrong order does not change the result. This is the direct check on train/serve skew, where the schema hash is only the indirect one. |
| `test_pipeline.py` | 13 | Phase 1's acceptance criteria as properties of the written files: no NaN or Inf survives, no rows are shared across splits, the bundle carries all five keys, and the scaler was fitted on train alone. Plus the warning Phase 1 emits when refitting the bundle would orphan a trained champion. |
| `test_console.py` | 2 | A report degrades a character rather than crashing the run on a cp1252 console after the real work is finished. |
| `test_labels.py` | 36 | Phase 2 — every published label maps, nothing maps by substring (`Web Attack Brute Force` is not `brute_force`), an unmapped label raises rather than becoming benign, and the support floor holds a class of eleven rows out of the vocabulary while reporting that it did. |
| `test_metrics.py` | 10 | Phase 2 — `tau_sup` is the smallest threshold inside the budget and never `argmax` or `0.5`; a budget no observed score satisfies is reported rather than hidden; alert volume is projected from the false-positive rate rather than from a lab day's attack density. |
| `test_supervised.py` | 29 | Phase 2 end to end — the model and its preprocessing are written as a matching pair, the serving loader accepts what training wrote and refuses a mismatched schema, a weaker challenger does not displace the champion, the report carries every section the checkpoint asks for, and a LightGBM champion **unpickles in a subprocess** (the check that catches a class pickled from `__main__`, which trains without complaint and cannot be served). Phase 4 added the two things its hold-out loop needs from this module: a frozen preprocessing bundle, asserted to leave the surviving rows of a reduced matrix bit-identical to the same rows of the full one, and a fixed-hyperparameter fit with no validation set — including that it sizes the output layer from the fold rather than from the record, because a booster told to emit three columns for a two-class problem does not fail, it emits a column of noise. |
| `test_autoencoder.py` | 39 | Phase 3 end to end — one attack row in the benign training set is **fatal**, not a warning; the input transform tames a column the scaler left unscaled and keeps the ordering it compresses, and is applied exactly once on both the fit and the scoring path; `tau_anom` is a benign percentile with the budget-equivalent threshold reported beside it; the persisted histogram keeps every row including both clipped tails; a state dict carries its own geometry, so the architecture in the model card cannot disagree with the weights; one flow scores the same alone, in a batch of seven and in a batch of sixty-four; the serving loader reaches `stage2_ready` on what training wrote and refuses a network whose input width disagrees with `feature_order`; and the report says so when a baseline beats the autoencoder, when the distributions fail to separate, and when Stage 1 misses a family Stage 2 misses too. |
| `test_fusion.py` | 24 | Phase 4 — the cascade and the serving path that runs it. Both thresholds are **inclusive**, matching the arithmetic `tau_sup` and `tau_anom` were cut with, so the operating point that ships is the one that was measured. Stage 2 is never consulted for a row Stage 1 named, and its score reads `NaN` there — *not asked*, rather than a zero somebody could re-rank the queue by. The family comes from the best *attack* class and never from `argmax` over all columns, which would emit a `KNOWN` alert naming benign as the attack. An `UNCLASSIFIED_ANOMALY` carries no family, and a vocabulary with no attack column cannot raise a `KNOWN` alert at all — both mirror the `family_matches_kind` constraint. Either stage may be absent and the cascade degrades; with neither it raises instead of returning nulls that read as *no attacks found*. Plus the train/serve parity check below. |
| `test_loao.py` | 28 | Phase 4 end to end — a family in the training split is genuinely removed and the model genuinely refitted, and one the temporal split already held out is reported as such rather than dressed up as a retrain that did nothing. The stage columns and the **Missed** column account for every row of the family. A family with no rows anywhere is *unmeasurable*, never 0%, because a zero cell reads as a detector that failed. A held-out family can still be caught but never **named**, so the naming rate is asserted at zero under hold-out and above zero for the control. Stage 2's arena scores are recomputed from the artifact the API loads, which is how *the autoencoder is unchanged* becomes a check rather than a claim. The generated prose is tested too: the verdict must credit the stage that actually caught the family, small supports must carry a Wilson interval, and the record must contain no `NaN` — `json.dumps` emits a bare one by default and every strict parser downstream then rejects the file. |
| **Total** | **328** | |

`conftest.py` supplies the shared fixtures: a session-scoped `client` that enters the `TestClient` context
manager — which is what actually exercises the lifespan, including artifact loading and the
schema-hash check — an `api_prefix` read from settings rather than hardcoded, and a `db_session`
built from ORM metadata on a throwaway in-memory SQLite database, deliberately not the dev file, so
constraint tests never touch real data. Phase 1 added synthetic CICIDS2017 frames carrying all five documented defects, and Phase 2 added small train/validation/test splits that reproduce the two structural properties of the real ones — a family too rare to train on, and a test day whose families are absent from the training vocabulary — plus a frozen budget stub so a test's threshold arithmetic does not move when someone edits `.env`. Phase 3 added `phase3_benign`, a benign-only day carrying a block of duplicated rows, because the real benign-only set carries duplicates too: Phase 1 removes them within each capture file and across the supervised splits, but that set is assembled from three days after the pass. Phase 4 added `two_stage_artifacts`, a directory holding a promoted Stage 1 and a fitted Stage 2 built by running the real Phase 2 and Phase 3 code on those frames — which is what makes a test of the cascade a test of what the API actually loads.

The Phase 3 tests run against a **real promoted champion**, built by the Phase 2 code into a temporary directory, rather than against a hand-assembled bundle. Stage 2 is fitted against Stage 1's feature contract, and a fixture that fabricated that contract would not catch the pair coming apart.

### Frontend — `frontend/src/pages/SystemHealth.test.tsx`

| Block | Tests | Invariant it protects |
| ----- | ----: | --------------------- |
| `SystemHealth (stubbed backend)` | 5 | Against a stubbed `fetch`: the three health fields render; a skeleton with an accessible `role="status"` named "loading health" shows before the first response lands; the request goes to the configured path `/api/v1/health` rather than a literal written into a component; **no accuracy figure appears anywhere** — asserted with `expect(container.textContent).not.toMatch(/accura/i)` and a check against any `NN.N%` string; and a 5xx surfaces "backend unreachable" instead of a blank panel, after the two retries with backoff that the query client performs. |
| `SystemHealth (live FastAPI)` | 1 | Probes `${VITE_DEV_PROXY_TARGET}/api/v1/health` at module load and uses `describe.skipIf` to skip itself when nothing answers. When a backend *is* running, it fetches health over real HTTP and asserts the `model_version` on screen is the one the live process just reported. This is what proves "live health data fetched from FastAPI" rather than asserting it. |
| **Total** | **6** | |

The anti-accuracy test deserves its own note. A hero tile reading "99.8% ACCURATE" is the exact
failure mode this project is built to avoid: on traffic that is roughly 99% benign, a model that
always predicts "benign" scores 99%. The dashboard avoiding that number is not enough — the test
makes reintroducing it a build failure. See [Anti-Patterns](Anti-Patterns.md).

---

## What is not covered yet

The specification's Phase 8 packaging requirements name four tests. Two exist, one is partly
shipped as of Phase 4, and one does not exist at all.
None can be written honestly before the code they cover exists, and each is listed here with what it
must assert so the requirement does not get lost.

| Required test | Exists | What it must assert | Phase that adds it |
| ------------- | ------ | ------------------- | ------------------ |
| **Feature parity between training and serving** | **Partially** | Phase 4 discharged the behavioural half, earlier than planned, because `score_batch` made it possible: `test_the_serving_path_and_the_training_path_produce_the_same_scores` scores the same two hundred rows twice — once as loose dicts through `score_batch`, once as a frame through `build_feature_matrix` the way training does — against real artifacts, and requires the confidences to agree. `test_a_flow_whose_keys_arrive_shuffled_scores_identically` covers column order, which is the part that fails silently, and `load_bundle` already recomputes the `schema_hash` from `feature_order` at startup. What remains is the stricter form the requirement asks for: comparing the two *matrices* element for element including dtypes, rather than comparing the scores they produce. Equal scores are strong evidence of equal matrices and not a proof of it. | Shipped (behavioural); matrix level still open |
| **Schema-hash mismatch** | **Yes** | Already covered by `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` in `test_api_surface.py`: a pickled bundle declaring `"sha256:deadbeef"` against `feature_order = ["a", "b"]` raises `SchemaHashMismatch` from `load_bundle`. It was written in Phase 0, before there was anything to load, precisely because the failure it prevents produces no exception on its own. What remains for a later phase is the startup-level assertion that the *process refuses to boot* on a mismatched real bundle, and that `model_card.json` disagreeing with `preprocessing.pkl` is equally fatal. | Shipped (unit level); process level still open |
| **Dedupe under burst load** | **Yes** (Phase 5) | That one source host emitting thousands of flows in a window collapses to a single alert row. Concretely: push N flows from one `src_host` with one `alert_class` inside a single `dedupe_window_seconds` bucket and assert exactly one `Alert` row exists, that its `occurrence_count` equals N, that `last_seen` advanced while `first_seen` did not, and that a flow landing in the *next* bucket creates a second alert. `tests/test_pipeline_alerts.py` covers it at unit level — one row, `occurrence_count == 2`, two published events, the higher risk surviving — and `reports/phase5_api.md` records it measured against a live 10x replay: 93 alert events over 4 distinct rows, one burst of 85 flows collapsing into one. | Shipped |
| **API contract tests** | **Yes** (Phase 5) | `test_api_surface.py` pins the *shape* of the surface — which operations are documented and that unimplemented ones answer 501. Phase 5 added those: `tests/test_alerts.py` covers cursor pagination (including the skip-and-repeat failure an offset page would have), every filter, the detail panels for both alert kinds, and verdict rejection; `tests/test_replay.py` covers the SSE framing, including a payload carrying a newline that would otherwise break the protocol; `tests/test_score.py` covers the 422 and 503 paths. Responses validate against the Pydantic models by construction, since each route declares a `response_model`. | Shipped; extended in Phase 6 |

Two further gaps worth naming, outside the Phase 8 list:

- **No test asserts the temporal split has no leakage.** Phase 1's checkpoint requires zero duplicate
  rows across splits and no surviving `NaN`/`Inf`; that check should become an assertion, not a
  printed table. Phase 1.
- **The frontend has one test file.** Six of the seven screens do not exist yet, so neither do their
  tests. Phase 6. See [Frontend Screens](Frontend-Screens.md).

---

## Testing philosophy for this project

**A test that pins an honest 501 is worth writing.** It would have been easy to leave unbuilt routes
unregistered. Registering all sixteen in Phase 0 means the OpenAPI schema — and therefore the
generated TypeScript types — is complete from the first day, and the frontend can be built against
the real contract instead of a guess. The 501 body is machine-readable (`detail`, `phase`,
`endpoint`), so `ApiError.isNotImplemented` lets the UI say "not built yet" rather than showing a
generic failure. `test_unimplemented_routes_answer_501_with_a_phase` is what keeps that property
true: the moment someone stubs a route with fabricated sample data to "make the screen look right",
the test fails. The honest surface is the thing being protected.

**Train/serve parity is the single most valuable test this repository will contain.** Every other
failure mode in this system announces itself. A missing table raises. A bad threshold shows up in the
metrics. But feeding a model the right columns in the wrong order produces plausible-looking garbage
scores and raises nothing at all — no exception, no warning, no log line. The dashboard fills with
alerts, every number is wrong, and nothing indicates it. That is why `features.py` is imported by
both paths rather than reimplemented, why the feature order is persisted in the bundle, why the
schema hash is order-sensitive (`compute_schema_hash(["a","b"]) != compute_schema_hash(["b","a"])`
is an explicit test), and why a hash mismatch is fatal at startup rather than logged. The parity test
is the one that closes the loop: it compares the two paths directly instead of trusting that they
share a module. Until it exists, the schema hash is the guard and it is doing real work.

**No test may assert against mock model output.** There is no trained model, so nothing in this
repository asserts a detection number, a recall figure, or a threshold value. Writing
`assert recall > 0.7` against a stubbed scorer would produce a green suite that means nothing, and
worse, it would make the number look measured. The rule the project follows: every number that
appears anywhere — in a test, in the dashboard, in the README — traces to a real model run on real
data, or it is marked as not measured yet. Fixtures may stub *transport* (the frontend stubs `fetch`
to test rendering) but never *inference*.

**Assert the constraint, do not merely honour it.** Several tests exist only to make an absence
loud. `test_no_route_mentions_blocking` fails if anyone adds a containment endpoint.
`test_auto_block_cannot_be_enabled` fails if the validator is removed.
`test_renders_no_accuracy_figure` fails if a hero accuracy tile returns.
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
autogenerated migrations produce long literal lines that are not worth rewrapping by hand. Two
in-file suppressions exist and both carry a reason: `noqa: E402` on the deferred route imports in
`app/routes/__init__.py`, which must come after the `not_implemented` helper to avoid a cycle, and
`noqa: S301` on the first-party artifact unpickle in `inference.py`.

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
backend contract changes and the file is stale, the mismatch surfaces here. Regenerate with
`make gen-types` while the backend is running; never hand-edit it.

There is no ESLint configuration in the repository. The strict compiler settings plus the test suite
are the current gate.
