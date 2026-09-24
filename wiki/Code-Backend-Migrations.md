# Code Reference — Database Migrations

This page documents the Alembic setup under `backend/alembic/` and the single migration that currently exists: configuration, environment wiring, the revision template, and every table, column, index and constraint created by `9a6857dcba76_initial_schema`. Read it if you are adding a migration, if you need to know why the schema avoids SQLite-only constructs, or if you are reconciling the SQL in a migration against the ORM in `backend/app/models.py`.

Everything on this page is implemented and runs today. The schema exists in full even though Phase 0 of 9 has no trained model and writes no alerts: the tables are created so that later phases add rows rather than tables.

| File | Lines | Role |
| --- | --- | --- |
| `backend/alembic.ini` | 56 | Alembic configuration, deliberately without a database URL |
| `backend/alembic/env.py` | 67 | Migration environment; pulls the URL from settings and the metadata from the ORM |
| `backend/alembic/script.py.mako` | 27 | Template every generated revision is rendered from |
| `backend/alembic/versions/9a6857dcba76_initial_schema.py` | 204 | The initial schema: `alerts`, `model_versions`, `analyst_verdicts` |

---

## backend/alembic.ini

**Path:** `backend/alembic.ini` — Alembic's own configuration file, holding script location, file naming, formatting hooks and logging, but deliberately not the database URL.

### What it does

The important thing about this file is what is missing from it. Its header says so directly: `sqlalchemy.url` is deliberately absent, because `alembic/env.py` reads the URL from pydantic-settings instead, so migrations and the application can never target different databases. Overriding the database is done with the `IDS_DATABASE_URL` environment variable, not by editing this file. A `sqlalchemy.url` here would be a second source of truth, and the failure it produces — a migration applied to one database while the app talks to another — is quiet until something reads a table that was never migrated.

The rest is mechanical. `script_location = alembic` points at the revision package, `prepend_sys_path = .` puts `backend/` on the path so `env.py` can `from app import models`, and `file_template = %%(rev)s_%%(slug)s` is what produced the name `9a6857dcba76_initial_schema.py`.

The `[post_write_hooks]` section formats every generated revision with ruff, in two passes: `ruff check --fix` then `ruff format`. Both are declared as `type = exec` rather than the more common `console_scripts`, and the file explains why: the `console_scripts` hook type cannot resolve `ruff` under `uv run`, so the hook invokes the venv executable directly.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `[alembic]` | Config section | `script_location = alembic` | Location of the revision package, relative to `backend/` |
| `prepend_sys_path` | Config key | `prepend_sys_path = .` | Adds `backend/` to `sys.path` so `env.py` can import `app.models` and `app.config` |
| `version_path_separator` | Config key | `version_path_separator = os` | Uses the OS path separator when resolving version locations |
| `file_template` | Config key | `file_template = %%(rev)s_%%(slug)s` | Revision filenames are the revision hash, an underscore, then a slug of the message |
| `[post_write_hooks]` | Config section | `hooks = ruff_fix, ruff_format` | Two formatting passes applied to every newly generated revision |
| `ruff_fix` | Hook | `ruff_fix.type = exec` / `ruff_fix.executable = ruff` / `ruff_fix.options = check --fix REVISION_SCRIPT_FILENAME` | Lint-fix pass; `exec` because `console_scripts` cannot resolve ruff under `uv run` |
| `ruff_format` | Hook | `ruff_format.type = exec` / `ruff_format.executable = ruff` / `ruff_format.options = format REVISION_SCRIPT_FILENAME` | Formatting pass over the same file |
| `[logger_root]` | Config section | `level = WARNING`, `handlers = console` | Quiet by default |
| `[logger_sqlalchemy]` | Config section | `level = WARNING`, `qualname = sqlalchemy.engine` | Suppresses per-statement engine chatter during migrations |
| `[logger_alembic]` | Config section | `level = INFO`, `qualname = alembic` | Alembic's own progress lines are shown |
| `[handler_console]` | Config section | `class = StreamHandler`, `args = (sys.stderr,)` | Log output goes to stderr |
| `[formatter_generic]` | Config section | `format = %(levelname)-5.5s [%(name)s] %(message)s`, `datefmt = %H:%M:%S` | Compact log line format |

### Notes

- `backend/pyproject.toml` carries `[tool.ruff.lint.per-file-ignores]` with `"alembic/versions/*" = ["E501"]`, so the long check-constraint expressions in generated migrations are not reflowed into unreadability by the line-length rule.
- Because the URL is absent, running `alembic` with no environment still works: it picks up whatever `Settings` resolves, which defaults to the development SQLite file.
- Status: implemented.

---

## backend/alembic/env.py

**Path:** `backend/alembic/env.py` — the migration environment, binding Alembic to the application's own settings and ORM metadata.

### What it does

`env.py` is where the single-source-of-truth rule from `alembic.ini` is enforced. It imports `settings` from `app.config` and assigns `config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)` before anything else, so both offline and online migration runs use exactly the URL the application would use. `settings.sqlalchemy_url` is also the property that rewrites a relative SQLite path to an absolute one anchored at the repository root, which means `alembic upgrade head` migrates the same file regardless of the directory it was invoked from.

It then imports `app.models` purely for its side effect. The comment states the consequence plainly: importing the models registers every table on `Base.metadata`, and without that import, autogenerate produces an empty migration. The import carries `# noqa: F401` because the name is never referenced.

Both `run_migrations_offline` and `run_migrations_online` are configured identically in the three ways that matter: `compare_type=True` so a column type change is detected, `compare_server_default=True` so a changed server default is detected, and `render_as_batch=True`. Batch mode is the SQLite accommodation — SQLite cannot `ALTER` most things in place, so Alembic rewrites the table instead. The comment notes it is harmless on Postgres, which is what makes it safe to leave on unconditionally rather than branching on dialect.

The online path builds its engine with `poolclass=pool.NullPool`. A migration runs once and exits; pooling connections for it would only hold a connection open past the point it is needed.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `config` | Module-level value | `config = context.config` | Alembic's config object, immediately given the URL from settings |
| `target_metadata` | Module-level value | `target_metadata = Base.metadata` | The ORM metadata autogenerate diffs the database against |
| `run_migrations_offline` | Function | `run_migrations_offline() -> None` | Renders migrations to SQL without a connection, using `literal_binds=True` and `dialect_opts={"paramstyle": "named"}` |
| `run_migrations_online` | Function | `run_migrations_online() -> None` | Opens a real connection via `engine_from_config(..., prefix="sqlalchemy.", poolclass=pool.NullPool)` and runs migrations inside a transaction |
| module dispatch | Module entry | `if context.is_offline_mode(): run_migrations_offline() else: run_migrations_online()` | Alembic executes this file as a script; the trailing dispatch selects the mode |

### Notes

- The `from app import models  # noqa: F401` line is load-bearing. Removing it does not break imports or raise — it silently makes `alembic revision --autogenerate` emit an empty `upgrade()`.
- `fileConfig(config.config_file_name)` is called only when `config.config_file_name is not None`, so the environment still works when Alembic is driven programmatically without an ini file.
- `render_as_batch=True` is why the initial migration wraps its index creation in `with op.batch_alter_table(...)` blocks.
- Neither `configure` call passes an `include_object` or `include_name` filter, so autogenerate compares `Base.metadata` against everything in the target database. A table created outside the ORM — a scratch table, a table left by another application sharing the database — is seen as a table the models no longer declare, and autogenerate will propose dropping it. That is one of the specific things step 3 of the workflow below is asking you to read for. Alembic's own `alembic_version` table is exempt automatically.
- Status: implemented.

---

## backend/alembic/script.py.mako

**Path:** `backend/alembic/script.py.mako` — the Mako template every generated revision file is rendered from.

### What it does

The template exists so new revisions arrive already matching the codebase's conventions rather than needing to be reformatted by hand. Two things are customised against Alembic's default. First, `from __future__ import annotations` is added at the top, which is the repository-wide convention. Second, the template stays deliberately plain and lets the post-write hooks finish the job: `ruff check --fix` applies the `I` import-sorting rules and the `UP` modernisation rules, which is why the template's `from typing import Sequence, Union` / `from alembic import op` / `import sqlalchemy as sa` block arrives in the committed revision as `from collections.abc import Sequence` / `import sqlalchemy as sa` / `from alembic import op`. The template is the input to that pass, not a pre-formatted output of it.

The revision identifiers are rendered from Alembic's own variables — `up_revision`, `down_revision`, `branch_labels` and `depends_on` — and the `upgrade()` and `downgrade()` bodies fall back to `pass` when autogenerate finds no changes, so an empty revision is still syntactically valid.

```python
def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| docstring block | Template | `"""${message}\n\nRevision ID: ${up_revision}\nRevises: ${down_revision \| comma,n}\nCreate Date: ${create_date}\n"""` | Header of every generated revision; `\| comma,n` renders a multi-parent `down_revision` as a comma-separated list |
| `from __future__ import annotations` | Fixed header | `from __future__ import annotations` | Emitted before any import on every generated revision — the repository-wide convention, and the one line the template adds that Alembic's default does not |
| `from typing import Sequence, Union` | Fixed header | `from typing import Sequence, Union` | Rewritten by the `UP` rules on the post-write pass: the committed revision carries `from collections.abc import Sequence` and annotates with `str \| None` rather than `Union[str, None]` |
| `from alembic import op` / `import sqlalchemy as sa` | Fixed header | `from alembic import op` then `import sqlalchemy as sa` | Reordered by the `I` rules on the post-write pass; the committed revision puts `import sqlalchemy as sa` first and `from alembic import op` after it |
| `${imports if imports else ""}` | Template slot | `${imports if imports else ""}` | Where autogenerate injects dialect-specific imports; empty in the one committed revision |
| `revision` | Template variable | `revision: str = ${repr(up_revision)}` | This revision's identifier |
| `down_revision` | Template variable | `down_revision: Union[str, None] = ${repr(down_revision)}` | The parent revision, or `None` for the initial one |
| `branch_labels` | Template variable | `branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}` | Branch labels, unused in this project |
| `depends_on` | Template variable | `depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}` | Cross-branch dependencies, unused in this project |
| `upgrade` | Function template | `def upgrade() -> None:` | Rendered with the autogenerated upgrade operations, or `pass` |
| `downgrade` | Function template | `def downgrade() -> None:` | Rendered with the autogenerated downgrade operations, or `pass` |

### Notes

- The template writes `Union[str, None]` while the committed initial migration carries `str | None`. That is the `ruff_fix` post-write hook doing its job: the `UP` rule set in `[tool.ruff.lint]` rewrites the typing-module form into the modern union syntax after generation.
- `${imports if imports else ""}` is where autogenerate injects dialect-specific imports, for example `postgresql` types. The initial migration needed none, which is itself evidence of the portability rule below.
- Status: implemented.

---

## backend/alembic/versions/9a6857dcba76_initial_schema.py

**Path:** `backend/alembic/versions/9a6857dcba76_initial_schema.py` — the initial migration, creating all three tables with their indexes and check constraints.

### What it does

This is the only revision in the repository. `revision = "9a6857dcba76"` with `down_revision = None` makes it the base. Its comments state that it was autogenerated from `app/models.py` and then reviewed, which is the intended workflow: the ORM is the source of truth, autogenerate proposes the SQL, and a human reads it before it is committed.

The schema it creates is the alert lifecycle. `alerts` holds one row per deduplicated detection; `model_versions` tracks which trained model produced what, so every alert can be traced to the model that scored it; `analyst_verdicts` records analyst true-positive/false-positive judgements, which Phase 7's retraining pipeline consumes. All three exist now even though no code writes to them yet. Phase 5 fills `alerts` and accepts verdicts through `POST /api/v1/alerts/{alert_id}/verdict` — a Phase 5 stub today, called by the Phase 6 dashboard once that screen exists. `model_versions` has no writer named in the code: the route that reads it, `GET /api/v1/models`, answers 501 naming Phase 7 (drift and active learning), and `app/models.py` ties the table to Phase 7's champion/challenger promotion.

The whole migration is written to run identically on SQLite and Postgres. That is not incidental; it is the constraint the schema was designed under, and it is described in its own section below.

### Tables created

#### `alerts`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `BigInteger().with_variant(Integer(), "sqlite")` | no | Primary key, `autoincrement=True` |
| `kind` | `String(32)` | no | `KNOWN` or `UNCLASSIFIED_ANOMALY` |
| `family` | `String(32)` | yes | Attack family; must be `NULL` for an anomaly |
| `detection_stage` | `String(32)` | no | `stage1_supervised` or `stage2_anomaly` |
| `severity` | `String(16)` | no | `low`, `medium`, `high`, `critical` |
| `risk_score` | `Float` | no | Ranking score for the triage queue |
| `confidence` | `Float` | yes | Stage 1 classifier confidence |
| `anomaly_score` | `Float` | yes | Stage 2 reconstruction error |
| `detected_at` | `DateTime(timezone=True)` | no | Indexed |
| `src_ip` | `String(45)` | no | 45 characters holds a full IPv6 address |
| `src_port` | `Integer` | yes | |
| `dst_ip` | `String(45)` | no | |
| `dst_port` | `Integer` | yes | |
| `protocol` | `String(16)` | yes | |
| `asset_criticality` | `String(16)` | yes | Feeds risk ranking |
| `host_prior_alert_count` | `Integer` | no | Prior alert volume for the source host |
| `mitre_technique` | `String(64)` | yes | ATT&CK technique identifier |
| `explanation` | `JSON` | yes | Per-feature attribution payload |
| `narrative` | `Text` | yes | Human-readable summary |
| `recommended_actions` | `JSON` | yes | Static playbook actions, not free-form text |
| `raw_flow` | `JSON` | yes | The scored flow record |
| `dedupe_key` | `String(255)` | no | Grouping key for burst suppression |
| `occurrence_count` | `Integer` | no | Times this dedupe key has fired |
| `first_seen` | `DateTime(timezone=True)` | no | |
| `last_seen` | `DateTime(timezone=True)` | no | |
| `status` | `String(16)` | no | `open`, `in_review`, `closed`, `dismissed` |
| `model_version` | `String(64)` | no | Audit trail: which model scored this row |
| `source` | `String(16)` | no | `replay`, `live` or `api` |
| `ground_truth_label` | `String(64)` | yes | Dataset label, available in replay mode |
| `created_at` | `DateTime(timezone=True)` | no | `server_default=sa.text("(CURRENT_TIMESTAMP)")` |
| `updated_at` | `DateTime(timezone=True)` | no | `server_default=sa.text("(CURRENT_TIMESTAMP)")`; refreshed by SQLAlchemy's `onupdate=func.now()` on the ORM model, not by the database — a raw SQL `UPDATE` leaves it stale, and there is no trigger in this migration |

Constraints on `alerts`:

| Name | Kind | Definition |
| --- | --- | --- |
| `pk_alerts` | Primary key | `PRIMARY KEY (id)` |
| `ck_alerts_kind_valid` | Check | `kind IN ('KNOWN', 'UNCLASSIFIED_ANOMALY')` |
| `ck_alerts_family_matches_kind` | Check | `(kind = 'UNCLASSIFIED_ANOMALY' AND family IS NULL) OR (kind = 'KNOWN' AND family IS NOT NULL)` |
| `ck_alerts_detection_stage_valid` | Check | `detection_stage IN ('stage1_supervised', 'stage2_anomaly')` |
| `ck_alerts_severity_valid` | Check | `severity IN ('low', 'medium', 'high', 'critical')` |
| `ck_alerts_status_valid` | Check | `status IN ('open', 'in_review', 'closed', 'dismissed')` |
| `ck_alerts_source_valid` | Check | `source IN ('replay', 'live', 'api')` |
| `ck_alerts_occurrence_count_positive` | Check | `occurrence_count >= 1` |

Indexes on `alerts`, all created inside a `batch_alter_table` block and all non-unique:

| Index | Columns | Serves |
| --- | --- | --- |
| `ix_alerts_dedupe_key_last_seen` | `dedupe_key`, `last_seen` | The dedupe lookup: has this key fired inside the suppression window |
| `ix_alerts_detected_at` | `detected_at` | Time-ordered queue and time-range filters |
| `ix_alerts_kind_detected_at` | `kind`, `detected_at` | Filtering the queue to `UNCLASSIFIED_ANOMALY` in time order |
| `ix_alerts_src_ip_detected_at` | `src_ip`, `detected_at` | Per-host history and the related-alerts view |
| `ix_alerts_status_risk_score` | `status`, `risk_score` | The default triage view: open alerts ranked by risk |

`ck_alerts_family_matches_kind` is the one worth reading twice. It makes the project's central distinction structural rather than conventional: a Stage 2 anomaly has no family because nothing named it, and the database refuses to store one that claims otherwise. `backend/tests/test_schema_portability.py` asserts both directions of it.

Six NOT NULL columns have defaults that live in the ORM only: `occurrence_count` (1), `status` (`open`), `source` (`replay`) and `host_prior_alert_count` (0) on `alerts`, and `stage` (`challenger`) and `is_active` (false) on `model_versions`. SQLAlchemy supplies them on flush, so an ORM insert that omits them succeeds; a hand-written `INSERT` that omits them fails the NOT NULL, because this migration emits no `server_default` for any of the six. Only `created_at` and `updated_at` carry a real `server_default`. Anything that writes to these tables outside the ORM has to supply all six.

#### `model_versions`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `BigInteger().with_variant(Integer(), "sqlite")` | no | Primary key, `autoincrement=True` |
| `version` | `String(64)` | no | Unique |
| `stage` | `String(16)` | no | `champion`, `challenger` or `archived` |
| `supervised_algorithm` | `String(64)` | yes | For example the RandomForest or LightGBM identifier |
| `anomaly_algorithm` | `String(64)` | yes | |
| `trained_at` | `DateTime(timezone=True)` | yes | |
| `trained_on` | `String(255)` | yes | Dataset and split description |
| `schema_hash` | `String(80)` | yes | The feature-order hash this model was trained against |
| `tau_sup` | `Float` | yes | Stage 1 threshold from the false-positive budget |
| `tau_anom` | `Float` | yes | Stage 2 threshold, the 99.5th benign percentile |
| `metrics` | `JSON` | yes | Evaluation output |
| `is_active` | `Boolean` | no | |
| `notes` | `Text` | yes | |
| `created_at` | `DateTime(timezone=True)` | no | `server_default=sa.text("(CURRENT_TIMESTAMP)")` |
| `updated_at` | `DateTime(timezone=True)` | no | `server_default=sa.text("(CURRENT_TIMESTAMP)")`; refreshed by SQLAlchemy's `onupdate=func.now()` on the ORM model, not by the database — a raw SQL `UPDATE` leaves it stale, and there is no trigger in this migration |

Constraints on `model_versions`:

| Name | Kind | Definition |
| --- | --- | --- |
| `pk_model_versions` | Primary key | `PRIMARY KEY (id)` |
| `uq_model_versions_version` | Unique | `UNIQUE (version)` |
| `ck_model_versions_stage_valid` | Check | `stage IN ('champion', 'challenger', 'archived')` |

`schema_hash` is `String(80)`, which comfortably holds `sha256:` plus a 64-character hex digest. Storing it per model version means the champion/challenger comparison in Phase 7 can tell whether two models were even trained against the same feature matrix.

#### `analyst_verdicts`

| Column | Type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `BigInteger().with_variant(Integer(), "sqlite")` | no | Primary key, `autoincrement=True` |
| `alert_id` | `BigInteger().with_variant(Integer(), "sqlite")` | no | Foreign key to `alerts.id`, `ON DELETE CASCADE` |
| `verdict` | `String(16)` | no | `TP`, `FP` or `UNSURE` |
| `note` | `Text` | yes | Analyst's free-text note |
| `analyst` | `String(128)` | yes | |
| `model_version` | `String(64)` | yes | Which model produced the alert being judged |
| `consumed_at` | `DateTime(timezone=True)` | yes | Set when a retraining run has ingested this verdict |
| `created_at` | `DateTime(timezone=True)` | no | `server_default=sa.text("(CURRENT_TIMESTAMP)")` |

Constraints on `analyst_verdicts`:

| Name | Kind | Definition |
| --- | --- | --- |
| `pk_analyst_verdicts` | Primary key | `PRIMARY KEY (id)` |
| `fk_analyst_verdicts_alert_id_alerts` | Foreign key | `FOREIGN KEY (alert_id) REFERENCES alerts (id) ON DELETE CASCADE` |
| `ck_analyst_verdicts_verdict_valid` | Check | `verdict IN ('TP', 'FP', 'UNSURE')` |

Indexes on `analyst_verdicts`, both non-unique:

| Index | Columns | Serves |
| --- | --- | --- |
| `ix_analyst_verdicts_alert_id` | `alert_id` | Joining verdicts back to their alert |
| `ix_analyst_verdicts_created_at_verdict` | `created_at`, `verdict` | Phase 7's "confirmed false positives since X" query for the benign refit pool |

`ON DELETE CASCADE` reaches SQLite only because `_build_engine` in `backend/app/db.py` attaches a `connect` listener that runs `PRAGMA foreign_keys=ON` — SQLite ignores foreign keys otherwise — alongside `PRAGMA journal_mode=WAL`, which keeps the replay writer from blocking dashboard readers. Postgres enforces the foreign key unconditionally, so the pragma is what makes the two backends behave the same. Note that the listener is attached to the application engine only: the `db_session` fixture in `backend/tests/conftest.py` builds its own in-memory engine without it, so the cascade is not exercised by the suite — the ORM-side `cascade="all, delete-orphan"` on `Alert.verdicts` is what the tests see.

### The Postgres-compatibility rule

Every column type in this migration compiles on both SQLite and Postgres, and no SQLite-only construct appears anywhere. Four specific rules produce that:

1. **No native enum types.** Every closed vocabulary — `kind`, `severity`, `status`, `source`, `detection_stage`, `stage`, `verdict` — is a `String` column plus a named `CheckConstraint`. Native enums need a dedicated `CREATE TYPE` / `ALTER TYPE` dance on Postgres and do not exist on SQLite at all; `String` plus a check behaves identically on both.
2. **Primary keys use an explicit dialect variant.** `sa.BigInteger().with_variant(sa.Integer(), "sqlite")` compiles to `BIGINT` on Postgres and `INTEGER` on SQLite. That is not cosmetic: SQLite only auto-assigns rowids for an `INTEGER PRIMARY KEY`, so a plain `BigInteger` key fails to autoincrement there.
3. **Every constraint and index is explicitly named.** The names come from the `NAMING_CONVENTION` on `Base.metadata` in `backend/app/db.py`, which is why they read as `ck_alerts_severity_valid` and `fk_analyst_verdicts_alert_id_alerts`. Unnamed constraints get different names on each backend and cannot later be `ALTER`ed on SQLite at all.
4. **Structured payloads use the generic `JSON` type, not `JSONB`.** `explanation`, `recommended_actions`, `raw_flow` and `metrics` are `sa.JSON()`, which maps to `json` on Postgres and `TEXT` on SQLite — the rule is stated in the `app/models.py` module docstring. `JSONB` would be faster to index on Postgres and would not compile at all on SQLite, which `test_every_column_type_compiles_for_sqlite` would catch immediately. Switching those columns to `JSONB` is a legitimate Postgres-only optimisation later, but it is the moment the single-migration-for-both-backends property ends.

What this buys is stated in `backend/app/db.py` and in `backend/tests/test_schema_portability.py`: moving from the development SQLite file to Postgres is a change to `IDS_DATABASE_URL` and nothing else. No migration is rewritten, no model is edited, no type is swapped. The alternative failure — discovering a SQLite-only type on the day of the swap — would not surface until exactly the moment it is most expensive, so the property is asserted in the test suite instead of trusted. `test_every_column_type_compiles_for_postgres` compiles every column against the Postgres dialect and `test_every_column_type_compiles_for_sqlite` does the same for SQLite, `test_no_native_enum_types_are_used` walks the metadata rejecting any `Enum`, `test_primary_keys_are_bigint_on_postgres_and_integer_on_sqlite` pins the variant, `test_no_sqlite_specific_table_options` rejects any `sqlite_`-prefixed table option, and `test_constraint_names_are_deterministic` fails on any constraint the naming convention did not name.

`server_default=sa.text("(CURRENT_TIMESTAMP)")` is likewise chosen because both backends understand it. The parentheses are what SQLite requires for an expression default; Postgres accepts them without complaint.

`DateTime(timezone=True)` is a request, not a guarantee. Postgres stores the offset; SQLite stores the value naively and drops it, so keeping timestamps tz-aware is an application-layer obligation that no constraint on this page enforces — the `app/models.py` docstring states it as exactly that. It has a concrete consequence one module over: `app/dedupe.py` calls `timestamp.timestamp()`, which interprets a naive datetime as local time, so a naive value written on one host produces a different dedupe bucket than the same instant written on another.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `revision` | Constant | `revision: str = "9a6857dcba76"` | This revision's identifier |
| `down_revision` | Constant | `down_revision: str \| None = None` | `None` makes this the base revision |
| `branch_labels` | Constant | `branch_labels: str \| Sequence[str] \| None = None` | Unused |
| `depends_on` | Constant | `depends_on: str \| Sequence[str] \| None = None` | Unused |
| `upgrade` | Function | `upgrade() -> None` | Creates `alerts` with 8 constraints and 5 indexes, then `model_versions`, then `analyst_verdicts` with its foreign key and 2 indexes |
| `downgrade` | Function | `downgrade() -> None` | Drops the indexes and tables in reverse dependency order: `analyst_verdicts`, `model_versions`, then `alerts` |

### Notes

- Creation order is dependency order: `alerts` first, because `analyst_verdicts.alert_id` references it. `downgrade()` reverses that, dropping `analyst_verdicts` before `alerts`.
- Index creation is wrapped in `with op.batch_alter_table(...)` blocks because `env.py` sets `render_as_batch=True`. On Postgres these compile to ordinary `CREATE INDEX`.
- Some index names are wrapped in `batch_op.f(...)` and others are passed as plain strings. `op.f()` marks a name as already-final so the naming convention is not applied to it a second time; the single-column indexes derived from `index=True` on the ORM column go through `op.f()`, while the composite indexes declared explicitly in `__table_args__` are passed verbatim.
- The `# Autogenerated from app/models.py, then reviewed.` comment appears in both `upgrade()` and `downgrade()`. Treat it as a standing instruction rather than a note about this file: hand-writing a migration that the ORM does not describe puts the two permanently out of sync.
- Status: implemented. Applying it is `make migrate`, and `backend/tests/conftest.py` builds its in-memory test database from `Base.metadata` directly rather than by running migrations. Nothing in the suite applies a migration, so ORM/migration drift is not caught by `make test-backend`: a drifted migration still applies cleanly and simply produces a different schema. The check is manual — run `make revision m="drift check"` against an up-to-date database and confirm autogenerate emits an empty `upgrade()`, then delete the throwaway revision.

---

## How to add a migration

The workflow is ORM first, autogenerate second, review third. Both a `Makefile` and a PowerShell equivalent (`make.ps1`) are provided at the repository root, because GNU make is not installed on Windows by default.

1. **Change the ORM.** Edit `backend/app/models.py`. Give every constraint an explicit `name=`, use `String` plus a `CheckConstraint` rather than a native enum, and use `sa.BigInteger().with_variant(sa.Integer(), "sqlite")` for any new surrogate key.

2. **Generate the revision.**

   ```sh
   make revision m="add drift table"
   ```

   On Windows:

   ```powershell
   ./make.ps1 revision -m "add drift table"
   ```

   Both resolve to `cd backend && uv run alembic revision --autogenerate -m "<message>"`. The `[post_write_hooks]` in `alembic.ini` run `ruff check --fix` and `ruff format` over the new file automatically.

3. **Read what was generated.** Autogenerate is a proposal. Check that no native enum snuck in, that every new constraint carries a name from the convention, that `downgrade()` actually reverses `upgrade()`, and that no `postgresql`-only or `sqlite`-only import appeared in the `${imports}` slot.

4. **Apply it.**

   ```sh
   make migrate
   ```

   On Windows:

   ```powershell
   ./make.ps1 migrate
   ```

   Both resolve to `cd backend && uv run alembic upgrade head`. The `env` prerequisite on the `migrate` target creates `.env` from `.env.example` first if it is absent.

5. **Run the suite.**

   ```sh
   make test-backend
   ```

   `backend/tests/test_schema_portability.py` will fail on a native enum (`test_no_native_enum_types_are_used`), an unnamed constraint (`test_constraint_names_are_deterministic`), a SQLite-only table option (`test_no_sqlite_specific_table_options`) or a type that does not compile for Postgres (`test_every_column_type_compiles_for_postgres`) — before the change is committed rather than on the day someone points `IDS_DATABASE_URL` at a real database. What it will not catch is drift between the ORM and this migration: the suite builds its schema from `Base.metadata` and never runs Alembic, so step 3 is the only review that compares the two.

To target a different database, set `IDS_DATABASE_URL` in `.env` rather than editing `alembic.ini`. `env.py` reads the URL from settings, so the migration and the application follow it together. See [Configuration](Configuration).
