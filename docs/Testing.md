# Testing

This page covers how to run the two test suites, exactly what they assert today, which required
tests do not exist yet and which phase adds them, the reasoning behind what this project chooses to
test, and the lint and typecheck gates. It is for anyone adding code to the repository, and for
anyone judging how much of the current behaviour is actually pinned.

**Status:** 74 backend tests across five files and 6 frontend tests in one file are shipped and
passing. Four tests the specification requires for Phase 8 do not exist yet; they are listed below
with what each must assert. No test in this repository asserts anything about model output, because
there is no model. See [Roadmap](Roadmap.md).

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
| `test_api_surface.py` | 34 | The full v1 route surface from the specification exists in the **OpenAPI schema** (not just in `app.routes`), because the schema is what the frontend generates its types from. Every route other than `/health` answers `501` with a body carrying a `phase` that starts with `"Phase "` and a non-empty `endpoint`, so callers can distinguish "not built yet" from "built and broken". No path in the schema contains `block`, `drop` or `quarantine` — the absence of a containment endpoint is asserted, not assumed. A pickled bundle whose `schema_hash` disagrees with its `feature_order` raises `SchemaHashMismatch` at load. |
| `test_config.py` | 8 | The false-positive budget arithmetic: `40 * 8 = 320` alerts/day and a target FPR of `3.2e-4`, so the Phase 2 threshold derives from a stated cost model rather than `0.5`. Budget inputs must be positive. `IDS_ALLOW_AUTO_BLOCK=true` raises a `ValidationError` whose message contains "never drops traffic". Relative paths anchor to the repo root rather than the working directory, absolute paths pass through untouched, relative SQLite URLs are rewritten absolute, a Postgres URL passes through byte for byte, and comma-separated CORS origins parse with whitespace trimmed. |
| `test_features.py` | 16 | The shared feature contract that both training and serving import. Column names normalise to snake_case for the real header quirks in the published CSVs (leading and trailing whitespace, `Flow Bytes/s`, the genuine duplicate `Fwd Header Length.1`). Normalisation preserves order. The schema hash is **order-sensitive**, stable across calls and `sha256:`-prefixed. Identity columns (`flow_id`, `source_ip`, `destination_ip`, `source_port`) are on the leakage denylist while `destination_port` deliberately is not, so Phase 1 has to decide about it explicitly instead of dropping it by default. A preprocessing bundle round-trips through disk with its hash intact. |
| `test_health.py` | 4 | The Phase 0 checkpoint contract. `GET {prefix}/health` returns exactly `{status, model_version, uptime_s}` — no more fields. With no artifacts present the status is `ok` and the version is `unloaded`, because a scaffold without a model is the expected state rather than a failure. Uptime is non-negative and monotonically advances. The prefix is configuration, so an unprefixed `/health` must 404. |
| `test_schema_portability.py` | 12 | The ORM stays swappable between SQLite and Postgres, so moving backends is a change to `IDS_DATABASE_URL` and nothing else. Every column type compiles for both dialects. Primary keys compile to `BIGINT` on Postgres and `INTEGER` on SQLite, because SQLite only auto-assigns rowids for `INTEGER PRIMARY KEY`. No native enum types (vocabularies are `String` plus `CheckConstraint`). No `sqlite_*` table options. Every constraint carries a deterministic name. The check constraints are then exercised against a real in-memory database: an `UNCLASSIFIED_ANOMALY` cannot carry a family, a `KNOWN` alert must carry one, the severity and verdict vocabularies reject unknown values, and `model_version.version` is unique. |
| **Total** | **74** | |

`conftest.py` supplies three fixtures: a session-scoped `client` that enters the `TestClient` context
manager — which is what actually exercises the lifespan, including artifact loading and the
schema-hash check — an `api_prefix` read from settings rather than hardcoded, and a `db_session`
built from ORM metadata on a throwaway in-memory SQLite database, deliberately not the dev file, so
constraint tests never touch real data.

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

The specification's Phase 8 packaging requirements name four tests. One of them exists; three do not.
None can be written honestly before the code they cover exists, and each is listed here with what it
must assert so the requirement does not get lost.

| Required test | Exists | What it must assert | Phase that adds it |
| ------------- | ------ | ------------------- | ------------------ |
| **Feature parity between training and serving** | No | That `features.py` produces byte-identical output on both paths: take a sample of raw flow rows, run them through the training preprocessing path and through the serving path in `inference.py`, and assert the resulting matrices are equal — same values, same dtypes, and above all **same column order**. It must also assert the serving path recomputes the same `schema_hash` the bundle carries. The test is only meaningful once there is a fitted scaler and a real feature matrix to compare, so it needs Phase 1's preprocessing bundle and Phase 5's scoring path to both be real. | Phase 5, against a Phase 1 bundle |
| **Schema-hash mismatch** | **Yes** | Already covered by `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` in `test_api_surface.py`: a pickled bundle declaring `"sha256:deadbeef"` against `feature_order = ["a", "b"]` raises `SchemaHashMismatch` from `load_bundle`. It was written in Phase 0, before there was anything to load, precisely because the failure it prevents produces no exception on its own. What remains for a later phase is the startup-level assertion that the *process refuses to boot* on a mismatched real bundle, and that `model_card.json` disagreeing with `preprocessing.pkl` is equally fatal. | Shipped (unit level); process level in Phase 5 |
| **Dedupe under burst load** | No | That one source host emitting thousands of flows in a window collapses to a single alert row. Concretely: push N flows from one `src_host` with one `alert_class` inside a single `dedupe_window_seconds` bucket and assert exactly one `Alert` row exists, that its `occurrence_count` equals N, that `last_seen` advanced while `first_seen` did not, and that a flow landing in the *next* bucket creates a second alert. `dedupe_key()` is pure and already implemented, so the key arithmetic is testable now; the insert-or-increment behaviour it guards is not, because the persistence path does not exist. | Phase 5 |
| **API contract tests** | Partially | `test_api_surface.py` pins the *shape* of the surface — which operations are documented and that unimplemented ones answer 501. What is missing is contract assertions on real response bodies: that `GET /alerts` paginates by cursor and honours its filters, that `GET /alerts/{id}` returns an explanation and a remediation for every alert kind, that `POST /alerts/{id}/verdict` rejects a verdict outside `{TP, FP, UNSURE}`, that `GET /stream` emits well-formed SSE frames, and that every response validates against the OpenAPI schema the frontend generated its types from. | Phase 5, extended in Phase 6 |

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
