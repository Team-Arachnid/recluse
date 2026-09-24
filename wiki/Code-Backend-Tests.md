# Code Reference — Backend Tests

This page documents every module under `backend/tests/`, the fixtures they share, and the exact invariant each test function pins down. Read it if you are adding a test, if a test failed and you need to know what property it was defending, or if you want to know which of the specification's required tests do not exist yet. For what is being tested see [Code Reference — Backend Core](Code-Backend-Core.md), [Code Reference — Training Package](Code-Backend-Training.md) and [Code Reference — Database Migrations](Code-Backend-Migrations.md); for the testing approach as a whole see [Testing](Testing.md).

The suite is small on purpose. Phase 0 of 9 is complete and there is no trained model, so there is very little behaviour to assert. What is here instead is a set of guards against failures that are *silent* — train/serve skew, a schema that only works on SQLite, an endpoint that fabricates data rather than admitting it is unimplemented. Each of those produces no exception on its own, so a test is the only thing that makes them audible.

Run the suite with `make test-backend`, or `./make.ps1 test-backend` on Windows; both resolve to `cd backend && uv run pytest`. `[tool.pytest.ini_options]` in `backend/pyproject.toml` sets `testpaths = ["tests"]`, `pythonpath = ["."]` and `addopts = "-q --strict-markers"`, and registers one marker: `integration`, for tests that need a live backend process.

| File | Lines | Role |
| --- | --- | --- |
| `backend/tests/conftest.py` | 48 | Shared fixtures: the API client, the configured prefix, a throwaway database session |
| `backend/tests/test_health.py` | 35 | The Phase 0 checkpoint contract on `GET /health` |
| `backend/tests/test_config.py` | 70 | Settings behaviour later phases depend on |
| `backend/tests/test_features.py` | 95 | The shared feature contract in `training/features.py` |
| `backend/tests/test_api_surface.py` | 118 | The v1 route surface exists, is documented, and is honest about being unimplemented |
| `backend/tests/test_schema_portability.py` | 146 | The ORM stays swappable between SQLite and Postgres, and the check constraints bite |

---

## backend/tests/conftest.py

**Path:** `backend/tests/conftest.py` — the three fixtures every other test module draws on.

### What it does

The `client` fixture is the one with a subtlety worth stating. It builds `TestClient(create_app())` inside a `with` block, and the docstring explains why: entering the context manager is what exercises startup. FastAPI's lifespan does not run when a `TestClient` is merely constructed, so without the `with` the artifact loading and the schema-hash check would never execute and the tests that depend on them would pass for the wrong reason. It is session-scoped, so the app starts once for the whole run.

The `db_session` fixture builds a fresh in-memory SQLite database from `Base.metadata` for each test that asks for it, then disposes of the engine afterwards. It is function-scoped rather than session-scoped because several tests deliberately provoke an `IntegrityError`, which leaves the session in a failed transaction; a shared session would carry that failure into the next test. The docstring also states the more important reason it is not the development database file: these tests assert constraint behaviour by writing rows that violate constraints, and must not touch real data.

Note that `db_session` builds the schema with `Base.metadata.create_all(engine)` rather than by running Alembic. That is a deliberate trade: it keeps the fixture fast and independent of migration state, and it means a divergence between the ORM and the migrations surfaces as a migration that fails to apply rather than as a suite that passes against a schema nobody migrated.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `client` | Fixture (session scope) | `client() -> Iterator[TestClient]` | Yields a `TestClient` wrapping `create_app()` inside a `with` block, so the lifespan — artifact loading and the schema-hash check — actually runs |
| `api_prefix` | Fixture | `api_prefix() -> str` | Returns `settings.api_v1_prefix`, so tests build URLs from configuration rather than hardcoding `/api/v1` |
| `db_session` | Fixture | `db_session() -> Iterator[Session]` | Yields a session against a throwaway `sqlite+pysqlite:///:memory:` engine built from `Base.metadata`, with `expire_on_commit=False`; closes the session and disposes the engine on teardown |

### Notes

- `sessionmaker(bind=engine, expire_on_commit=False)` matters for `test_verdict_vocabulary_is_enforced`, which reads `alert.id` after a commit. With the default `expire_on_commit=True` that attribute access would trigger a refresh.
- The engine is created per test and disposed in the `finally`, so an in-memory database never outlives the test that created it.
- `api_prefix` being a fixture rather than a literal is what lets `test_health_is_mounted_under_the_configured_prefix` assert both that the prefix is `/api/v1` *and* that an unprefixed path 404s, without either fact being duplicated.
- Status: implemented.

---

## backend/tests/test_health.py

**Path:** `backend/tests/test_health.py` — pins the Phase 0 checkpoint contract: `GET /health` is the one endpoint that must work before anything else exists.

### What it does

`/health` is the only non-501 endpoint in Phase 0, so it carries the entire "the stack runs end to end" claim. These four tests fix its response shape, its behaviour with no model on disk, its uptime semantics, and its mount point.

The second test is the one that encodes a project-wide stance. No trained model is the expected Phase 0 state, not a failure, so `status` is `"ok"` and `model_version` is `"unloaded"` — the string constant `UNLOADED_VERSION` from `app/inference.py`. Reporting `degraded` here would mean the dashboard shows a red light for nine phases; reporting a fake version would mean the health endpoint lies. `degraded` is reserved for a bundle that was found but could not be made usable.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `test_health_returns_the_agreed_three_fields` | Test | `(client: TestClient, api_prefix: str) -> None` | `GET {prefix}/health` returns 200 and a body whose key set is exactly `{"status", "model_version", "uptime_s"}` — no more, no fewer |
| `test_health_status_is_ok_without_artifacts` | Test | `(client: TestClient, api_prefix: str) -> None` | With no artifacts on disk, `status == "ok"` and `model_version == "unloaded"`. Absent models are the expected state, not a failure |
| `test_uptime_is_non_negative_and_advances` | Test | `(client: TestClient, api_prefix: str) -> None` | `uptime_s` is never negative and never decreases between two successive calls — it is monotonic, not a wall-clock difference that could go backwards |
| `test_health_is_mounted_under_the_configured_prefix` | Test | `(client: TestClient, api_prefix: str) -> None` | The prefix is `/api/v1`, and an unprefixed `GET /health` returns 404. The prefix is configuration, so nothing may be reachable at both paths |

### Notes

- Asserting the key set with `set(body) == {...}` rather than checking individual keys means an accidentally added field fails the test. The response shape is a contract with the frontend, which generates its types from the OpenAPI schema.
- `second >= first` uses a non-strict comparison: two calls in quick succession may legitimately land in the same tick.
- Status: implemented. These are the only assertions in the suite that exercise real end-to-end behaviour rather than structure.

---

## backend/tests/test_config.py

**Path:** `backend/tests/test_config.py` — pins the configuration behaviour that later phases build on, including the false-positive budget and the alert-never-block guarantee.

### What it does

`app/config.py` is Phase 0 code that Phase 2, Phase 5 and Phase 9 all depend on, and most of what it does is compute or rewrite values rather than merely hold them. These eight tests pin the computations.

Three of them defend properties that are not really about configuration at all. The budget arithmetic is the Phase 2 threshold in miniature — `tau_sup` is derived from these numbers, so if the arithmetic drifts, the threshold drifts with it. The auto-block test encodes a hard requirement from the brief as a validation error rather than a convention. And the path-resolution test prevents a class of bug where the answer depends on the directory `uvicorn` was launched from.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `test_false_positive_budget_arithmetic` | Test | `() -> None` | With `expected_daily_flow_volume=1_000_000`, `analyst_capacity_per_hour=40`, `analyst_shift_hours=8`: `max_alerts_per_day == 320` and `target_fpr == pytest.approx(3.2e-4)`. The Phase 2 threshold is derived from this, not from 0.5 |
| `test_budget_inputs_must_be_positive` | Test | `() -> None` | `Settings(expected_daily_flow_volume=0)` raises `ValidationError`. A zero volume would make `target_fpr` a division by zero |
| `test_auto_block_cannot_be_enabled` | Test | `() -> None` | `Settings(allow_auto_block=True)` raises `ValidationError` whose message contains `"never drops traffic"`. No configuration turns this system into an inline blocker |
| `test_relative_paths_anchor_to_the_repo_root_not_the_cwd` | Test | `() -> None` | `settings.data_path == (REPO_ROOT / "data").resolve()` and `settings.artifacts_path == (REPO_ROOT / "backend" / "artifacts").resolve()`. Otherwise the database silently differs depending on where `uvicorn` ran |
| `test_absolute_paths_are_respected` | Test | `(tmp_path) -> None` | `Settings(data_dir=tmp_path).data_path == tmp_path`. The repo-root anchoring applies to relative paths only and must not rewrite an absolute one |
| `test_sqlite_url_is_rewritten_to_an_absolute_path` | Test | `() -> None` | `Settings(database_url="sqlite+pysqlite:///data/ids.db").sqlalchemy_url` yields a URL whose `database` component is an absolute path. This is what makes `alembic upgrade head` and the app hit the same file |
| `test_postgres_url_passes_through_untouched` | Test | `() -> None` | `"postgresql+psycopg://ids:secret@db:5432/ids"` survives as `sqlalchemy_url` byte for byte. Swapping backends is meant to be this line and nothing else |
| `test_cors_origins_parse_from_a_comma_separated_string` | Test | `() -> None` | `Settings(cors_origins="http://a.test, http://b.test ,").cors_origin_list == ["http://a.test", "http://b.test"]` — whitespace trimmed, trailing empty entry dropped |

### Notes

- Every test constructs its own local `Settings(...)` rather than mutating the module-level `settings` singleton, except `test_relative_paths_anchor_to_the_repo_root_not_the_cwd`, which deliberately asserts against the real singleton because that is the object the application actually uses.
- `test_auto_block_cannot_be_enabled` asserts on the *message*, not just the exception type. That makes the refusal self-documenting: whoever sets the flag reads why it is refused.
- The SQLite-rewriting test imports `Path` inside the function body. Functionally irrelevant; it keeps the assertion and its import adjacent.
- Status: implemented.

---

## backend/tests/test_features.py

**Path:** `backend/tests/test_features.py` — pins the Phase 0 half of the shared feature contract, the module imported by both training and serving.

### What it does

The module docstring states the stake: `features.py` is imported by both training and serving, and everything downstream — the schema-hash startup check especially — depends on these parts being stable. The tests split into three groups: header normalisation, the schema hash, and the leakage deny-list, plus one round-trip test for the bundle.

The normalisation cases are drawn from the real dataset rather than invented. The comments name what each one represents: the published CSVs really do ship headers with leading whitespace, and CICIDS2017 has a genuine duplicate header disambiguated only by a `.1` suffix.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `test_column_names_normalise_to_snake_case` | Test (parametrised, 10 cases) | `(raw: str, expected: str) -> None` | `normalise_column_name` maps each real CICIDS2017 header to its snake_case form: `" Flow Duration"`/`"Flow Duration "` → `flow_duration`, `"Flow Bytes/s"` → `flow_bytes_s`, `"Flow Packets/s"` → `flow_packets_s`, `"Fwd IAT Min"` → `fwd_iat_min`, `"Destination Port"` → `destination_port`, `"Fwd Header Length.1"` → `fwd_header_length_1`, `"Bwd PSH Flags"` → `bwd_psh_flags`, `"CWE Flag Count"` → `cwe_flag_count`, `"  Label  "` → `label` |
| `test_normalise_columns_preserves_order` | Test | `() -> None` | `normalise_columns([" B", "A "]) == ["b", "a"]`. Order is preserved rather than sorted — the whole schema-hash mechanism depends on order being meaningful |
| `test_schema_hash_is_order_sensitive` | Test | `() -> None` | `compute_schema_hash(["a", "b"]) != compute_schema_hash(["b", "a"])`. This is the entire reason the hash exists: feeding the right columns in the wrong order produces garbage scores and raises nothing |
| `test_schema_hash_is_stable_and_prefixed` | Test | `() -> None` | The same feature order hashes identically across calls, and the result starts with `"sha256:"`. Stability makes the persisted value comparable; the prefix makes it self-describing |
| `test_identity_columns_are_on_the_leakage_denylist` | Test | `() -> None` | `flow_id`, `source_ip`, `destination_ip` and `source_port` are all members of `LEAKAGE_COLUMNS` — the addressing scheme of the lab is not a feature |
| `test_destination_port_is_not_silently_dropped` | Test | `() -> None` | `PORT_COLUMN == "destination_port"` and it is **not** in `LEAKAGE_COLUMNS`. Dropping it by default would hide the ablation the brief asks for, so Phase 1 has to decide explicitly |
| `test_bundle_round_trips_with_a_matching_hash` | Test | `(tmp_path) -> None` | `build_preprocessing_bundle` derives `schema_hash` from the feature order it was given; after `save_preprocessing_bundle` and `load_preprocessing_bundle` through a real file, both `feature_order` and `schema_hash` survive unchanged |

### Notes

- `test_bundle_round_trips_with_a_matching_hash` passes `scaler=None`. The bundle's I/O path does not care what the scaler is, and using `None` keeps the test free of a scikit-learn dependency.
- The round-trip goes through an actual file on `tmp_path` rather than an in-memory buffer, so it also exercises `save_preprocessing_bundle`'s `path.parent.mkdir(parents=True, exist_ok=True)`.
- These tests cover the Phase 0 surface of `features.py` only. `build_feature_matrix` is a stub and has no test, which is the largest gap in the suite — see below.
- Status: implemented.

---

## backend/tests/test_api_surface.py

**Path:** `backend/tests/test_api_surface.py` — asserts that the whole v1 route surface exists, is documented in OpenAPI, and answers honestly when unimplemented.

### What it does

Registering every route in Phase 0 means the OpenAPI schema — and therefore the generated frontend types — is complete from the start, so the dashboard can be built against the real contract rather than a mock. The cost of that decision is that most routes have no implementation, and the risk is that an unimplemented route quietly returns plausible-looking empty data. These tests close that: unimplemented routes must answer `501` with the phase that fills them in, and none of them may fabricate data.

`_documented_operations` reads from `app.openapi()` rather than walking `app.routes`, and the docstring gives the reason: the schema is the artefact the frontend generates its types from, so a route missing there is a route the client cannot see, regardless of whether the server would have answered it.

Two tests defend things that are not about any individual route. `test_no_route_mentions_blocking` walks every path in the schema asserting that none contains `block`, `drop` or `quarantine` — the brief forbids auto-block, and the absence of a containment endpoint is asserted rather than assumed. `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` writes a deliberately inconsistent pickle and requires `load_bundle` to raise; its docstring notes it was written in Phase 0 so the guard exists *before* there is anything to load, because the failure it prevents produces no exception on its own.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `EXPECTED_ROUTES` | Constant | `EXPECTED_ROUTES = [("GET", "/health"), ("POST", "/score"), ("GET", "/alerts"), ("GET", "/alerts/{alert_id}"), ("POST", "/alerts/{alert_id}/verdict"), ("GET", "/alerts/{alert_id}/related"), ("GET", "/stream"), ("GET", "/metrics/model"), ("GET", "/metrics/threshold"), ("GET", "/metrics/drift"), ("GET", "/analytics/summary"), ("GET", "/analytics/mitre-coverage"), ("POST", "/replay/start"), ("POST", "/replay/stop"), ("POST", "/ingest/start"), ("GET", "/models")]` | The sixteen method/path pairs exactly as `BUILD_PROMPT.md` Part 8 specifies them |
| `_documented_operations` | Helper | `_documented_operations(app) -> set[tuple[str, str]]` | Returns every `(METHOD, path)` pair from `app.openapi()["paths"]`, uppercasing the method |
| `test_route_is_documented` | Test (parametrised, 16 cases) | `(method: str, path: str, api_prefix: str) -> None` | Each expected route appears in the OpenAPI schema at `{prefix}{path}`. The schema, not the router, is the thing checked |
| `test_unimplemented_routes_answer_501_with_a_phase` | Test (parametrised, 15 cases) | `(client: TestClient, api_prefix: str, method: str, path: str) -> None` | Every expected route except `/health` returns exactly `501`, with a JSON body whose `phase` starts with `"Phase "` and whose `endpoint` is non-empty. `{alert_id}` is substituted with `1`; `/metrics/threshold` is given `params={"t": 0.87}` to satisfy its required query parameter |
| `test_openapi_schema_is_served` | Test | `(client: TestClient) -> None` | `GET /openapi.json` returns a schema whose `openapi` version starts with `"3."` and whose `info.title` is exactly `"Recluse API"` |
| `test_no_route_mentions_blocking` | Test | `(client: TestClient) -> None` | No path in the schema contains `block`, `drop` or `quarantine`, case-insensitively. There is no containment endpoint and there is not going to be one |
| `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` | Test | `(tmp_path) -> None` | A `preprocessing.pkl` with `feature_order=["a", "b"]` and `schema_hash="sha256:deadbeef"` makes `load_bundle(tmp_path)` raise `SchemaHashMismatch`. Train/serve skew is silent, so a bad bundle must stop the process |

### Notes

- The 501 test asserts on `body["phase"]` and `body["endpoint"]` rather than on an exact message. That leaves the wording free to improve while keeping the contract — "not built yet, and here is which phase builds it" — fixed.
- `path.replace("{alert_id}", "1")` means the parameterised routes are exercised with a real URL, so a route that 422s on path-parameter validation before reaching the 501 helper would fail here.
- `test_route_is_documented` calls `create_app()` directly instead of using the `client` fixture, because it only needs the schema and not a running lifespan.
- `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` imports `pickle` and `load_bundle` inside the function body, keeping the imports next to the thing being constructed.
- Status: implemented. This module is the reason "not built yet" is distinguishable from "built and broken" across the whole API.

---

## backend/tests/test_schema_portability.py

**Path:** `backend/tests/test_schema_portability.py` — asserts the ORM stays swappable between SQLite and Postgres, and that the check constraints actually reject bad rows.

### What it does

The module docstring states the requirement and the reason: Phase 0 requires Postgres-compatible types only, so moving off SQLite is a change to `IDS_DATABASE_URL` and nothing else — and a type that only SQLite understands would not surface until the day of the swap, so it is asserted here instead. That is the general shape of this whole file: it converts a property that would otherwise fail at the worst possible moment into a property that fails on the next test run.

The first six tests walk `Base.metadata.sorted_tables` and check structural properties. The remaining six write real rows into the `db_session` fixture and require the database to reject the malformed ones, because a check constraint that is declared but not enforced is worse than no constraint at all — it invites code that assumes the invariant holds.

`_minimal_alert` builds a well-formed `Alert` with every non-nullable field populated and accepts `**overrides`, so each constraint test differs from a valid row in exactly one field. That is what makes a failure diagnostic: if `test_severity_vocabulary_is_enforced` fails, the only thing that was wrong with the row was the severity.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `test_every_column_type_compiles_for_postgres` | Test | `() -> None` | Every column of every table compiles against `postgresql.dialect()`. A SQLite-only type raises `UnsupportedCompilationError` here rather than on migration day |
| `test_every_column_type_compiles_for_sqlite` | Test | `() -> None` | The same, against `sqlite.dialect()` — the development default must keep working too |
| `test_primary_keys_are_bigint_on_postgres_and_integer_on_sqlite` | Test | `() -> None` | `Alert.__table__.c.id.type` compiles to `"BIGINT"` on Postgres and `"INTEGER"` on SQLite. SQLite only auto-assigns rowids for `INTEGER PRIMARY KEY`, so a plain `BigInteger` key would not autoincrement — hence the explicit dialect variant |
| `test_no_native_enum_types_are_used` | Test | `() -> None` | No column's type class is named `Enum`. Native enums need a dedicated migration dance on Postgres; vocabularies are `String` plus `CheckConstraint` instead, which both backends treat identically |
| `test_no_sqlite_specific_table_options` | Test | `() -> None` | No table carries a kwarg starting with `sqlite_` |
| `test_constraint_names_are_deterministic` | Test | `() -> None` | Every constraint on every table has a name, and no name starts with `_unnamed_`. Unnamed constraints get different names per backend and cannot be `ALTER`ed on SQLite, so the naming convention must actually apply |
| `_minimal_alert` | Helper | `_minimal_alert(**overrides: object) -> Alert` | Builds a valid `Alert` — `kind="KNOWN"`, `family="port_scan"`, `detection_stage="stage1_supervised"`, `severity="high"`, `risk_score=0.91`, timezone-aware `detected_at`/`first_seen`/`last_seen`, `src_ip="10.0.0.5"`, `dst_ip="10.0.0.9"`, `dedupe_key="10.0.0.5\|port_scan\|0"`, `model_version="unloaded"` — then applies `overrides` |
| `test_a_well_formed_alert_persists` | Test | `(db_session) -> None` | The baseline row commits and is counted. Without this, every rejection test below could be passing for the wrong reason |
| `test_unclassified_anomaly_cannot_carry_a_family` | Test | `(db_session) -> None` | `kind="UNCLASSIFIED_ANOMALY"` with `family="botnet"` raises `IntegrityError`. Stage 2 alerts have no family label — that is the entire point |
| `test_a_known_alert_must_carry_a_family` | Test | `(db_session) -> None` | `kind="KNOWN"` with `family=None` raises `IntegrityError`. The other half of `ck_alerts_family_matches_kind` |
| `test_severity_vocabulary_is_enforced` | Test | `(db_session) -> None` | `severity="catastrophic"` raises `IntegrityError` — `ck_alerts_severity_valid` is a real constraint, not documentation |
| `test_verdict_vocabulary_is_enforced` | Test | `(db_session) -> None` | An `AnalystVerdict` with `verdict="MAYBE"` against a committed alert raises `IntegrityError` |
| `test_model_version_is_unique` | Test | `(db_session) -> None` | Inserting `ModelVersion(version="rf-1")` twice raises `IntegrityError` — `uq_model_versions_version` holds, which is what makes the audit trail from an alert back to a model unambiguous |

### Notes

- The two `family` tests are the pair that make the project's central distinction structural. `UNCLASSIFIED_ANOMALY` means "nothing named this", so a row claiming both is not an inconsistent record — it is a contradiction, and the database refuses it.
- These tests exercise SQLite, so `ON DELETE CASCADE` and check constraints are only enforced because `app/db.py` issues `PRAGMA foreign_keys=ON` on connect. Postgres enforces both unconditionally; the pragma is what makes the two behave alike.
- `test_no_native_enum_types_are_used` compares `column.type.__class__.__name__` to the string `"Enum"` rather than using `isinstance`, so it also catches dialect-specific enum subclasses that do not inherit from the generic type.
- Status: implemented.

---

## What is not covered yet

`BUILD_PROMPT.md` Part 11 requires four tests as part of Phase 8 packaging. Two do not exist at all, and two exist only in the partial form that Phase 0 allows.

| Required test | State today | What is missing |
| --- | --- | --- |
| **Feature-parity: `features.py` produces identical output in the training and serving paths** | Does not exist | `build_feature_matrix` is a stub that raises `NotImplementedError`, so there is no output to compare. This is the most important missing test in the repository: it is the direct check on train/serve skew, where the schema hash is only the indirect one. It lands with Phase 1, and should feed the same frame through the training path and the serving path and assert the resulting matrices are identical element for element, not merely the same shape |
| **Schema-hash mismatch** | Partial — `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` covers one case | Only the self-inconsistent-bundle case is tested: a pickle whose stored hash disagrees with its own `feature_order`. Not covered: a bundle with no `schema_hash` at all (the `"carries no schema_hash; refusing to serve"` branch of `_verify_schema_hash`), a `model_card.json` whose hash disagrees with `preprocessing.pkl`, and the end-to-end case where a mismatched bundle makes the application fail to start rather than just making `load_bundle` raise |
| **Dedupe under burst** | Does not exist | `app/dedupe.py` is a stub. The test the brief asks for is the burst case: many alerts from one host inside the suppression window must collapse into one row with a rising `occurrence_count` and an advancing `last_seen`, not five thousand rows that make the queue unusable. The schema is ready for it — `dedupe_key`, `occurrence_count`, `first_seen`, `last_seen` and the `ix_alerts_dedupe_key_last_seen` index all exist — but nothing writes to them yet. Lands with the alert pipeline |
| **API contract** | Partial — `test_api_surface.py` covers the shape, not the behaviour | What exists asserts that every route is registered, documented and honest about being unimplemented. What does not exist is the actual contract: request and response body validation against the Pydantic schemas, pagination and filter behaviour on `GET /alerts`, the SSE framing on `GET /stream`, error bodies for bad input, and the round trip of posting a verdict and reading it back. Each of those arrives with the endpoint it describes, in Phase 5 |

Two further gaps are worth naming even though Part 11 does not list them separately. There is no test that the Alembic migrations apply cleanly to an empty database and produce a schema matching `Base.metadata` — `conftest.py` builds its schema with `create_all` instead, so a migration could drift from the ORM without the suite noticing. And the `integration` marker is registered in `backend/pyproject.toml` for tests that need a live backend process, but no test currently uses it.

See [Testing](Testing.md) for the strategy these gaps sit inside, and [Roadmap](Roadmap.md) for when each is scheduled to close.

---

## Related pages

- [Testing](Testing.md) — testing strategy, what is worth asserting and what is not
- [Code Reference — Backend Core](Code-Backend-Core.md) — `config.py`, `db.py`, `models.py`, `inference.py`
- [Code Reference — Training Package](Code-Backend-Training.md) — `features.py` and the contract `test_features.py` pins
- [Code Reference — Backend Routes](Code-Backend-Routes.md) — the routes `test_api_surface.py` enumerates
- [Code Reference — Database Migrations](Code-Backend-Migrations.md) — the schema `test_schema_portability.py` defends
- [API Reference](API-Reference.md) — the v1 surface and its 501 contract
