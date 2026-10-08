# OpenStudyBuilder — Public Mono-Repo

OpenStudyBuilder is a clinical trial metadata management solution. This
repository holds the open-source components: the API, the web frontend, the
Neo4j database setup, the data importers and exporter, the schema migrations,
and the test suites.

## Repository layout

| Directory | Purpose | Stack |
|---|---|---|
| `api/` | REST API. Hosts three FastAPI apps off one codebase: `clinical_mdr_api` (port 8000), `consumer_api` (8008), `extensions` (8009) | Python, FastAPI, Neo4j |
| `frontend/` | OpenStudyBuilder web application | Vue 3, Vite, npm |
| `db/` | Neo4j database setup, initialisation, backup and restore | Docker, Python |
| `db_schema_migration/` | Numbered schema migrations and data corrections | Python |
| `import_standards/` | CDISC controlled terminology and data model import | Python |
| `import_sponsor_data/` | Sponsor codelists, dictionaries and mock study data import | Python |
| `export/` | Study data export | Python |
| `documentation_portal/` | Documentation site | VitePress, npm |
| `system_tests/` | End-to-end tests: `ui-tests/` (web application) and `neodash-test/` (NeoDash reports) | Cypress, npm |
| `verifications/` | Database and API verification suite | Python, pytest-bdd |
| `load_tests/` | Load tests | Python, Locust |
| `_tools/` | Shared tooling. `build_sbom.py` backs every component's `build-sbom` script | Python |

Each directory has its own README covering what is specific to it. Setup that
several components share is documented here and linked from there.

## AccuraTrial fork

This checkout is the AccuraTrial fork of OpenStudyBuilder. It tracks upstream
releases (currently **2.10.0**) and adds the platform integration described
below. Upstream's component READMEs apply unchanged unless this section says
otherwise.

### Command Center deployment

In the AccuraTrial platform, OpenStudyBuilder is a private, authenticated child
application behind Command Center:

- Command Center owns interactive login and issues five-minute RS256 tokens with
  audience `accuratrial-openstudybuilder`.
- OSB API/RBAC stays enabled; the Vue OAuth UI stays disabled to avoid a second
  login. Browser API traffic must enter through the managed OSB origin so Command
  Center can inject the audience token.
- Command Center proxies the whole OSB origin to the `frontend` container, which
  must therefore proxy `/api`, `/consumer-api`, `/extensions-api`, `/doc` and
  `/neodash` itself. The fork's `frontend/Dockerfile` defaults to
  `NGINX_CONF=standalone` for that reason; upstream's default `gateway` variant
  serves only the static SPA.
- Native OSB, Neo4j, and NeoDash ports must not be publicly exposed.
- The production override disables NeoDash standalone login and removes the
  Neo4j username/password from its browser-readable configuration. Treat
  NeoDash as an optional private administrative tool until a server-side,
  Command Center-authorized database bridge is implemented.
- Production configuration starts from `.env.production.example`.
- Production starts from immutable images only:
  `docker compose --env-file .env.production -f compose.yaml -f
  compose.production.yaml up -d --no-build`.
- `MAPPING_AUTHORITY_MODE=shadow` or `enforced` deliberately blocks the legacy
  StudyBundleV1 EDC sender. Command Center's EDC-send action must remain disabled
  until the governed native release package is implemented and validated.

Never copy the EDC JWT signing secret into this repository. OSB trusts only
Command Center's HTTPS discovery/JWKS endpoint; EDC imports use a separate,
narrowly scoped `x-api-key`.

### Running the full stack in Docker

The root `compose.yaml` runs the whole solution (database, the three API apps,
frontend, documentation portal, NeoDash). Its topology is the fork's: the named
volumes `studybuilder_database` and `studybuilder_database_logs`, the published
loopback ports and the authentication defaults are unchanged from 2.9, and only
the build contexts follow the 2.10 directory names. Upstream's single-port
`gateway` service is opt-in (`docker compose --profile gateway up -d`, then
`http://localhost:8080`); it carries no authentication and is never part of the
platform deployment.

The seeded database image is built from `db/Dockerfile`:

- It pins the seed tarball, the runtime image and the APOC plugin to one Neo4j
  release through `NEO4J_VERSION_PINNED` (default `2026.06.0`). Do not replace
  the runtime image with a floating `neo4j:enterprise` tag: Neo4j cannot open a
  store created by a newer kernel.
- The build needs a Neo4j password for its own seeding run, passed as a BuildKit
  secret: `NEO4J_MDR_AUTH_PASSWORD=<any build password> docker compose build database`.
  The seeded backup holds only the `mdrdb` user database, so the runtime
  password (`NEO4J_PASSWORD`) is independent of it.
- The default seed includes the two curated CDISC example studies and not the
  large `DummyStudy` fixture set. Opt in with `INCLUDE_DUMMY_STUDIES=true`.
- The seed build defaults to a bounded 1/3 GiB heap and 1 GiB page cache so it
  completes in an 8 GiB Docker VM; raise `NEO4J_BUILD_HEAP_INITIAL`,
  `NEO4J_BUILD_HEAP_MAX` and `NEO4J_BUILD_PAGECACHE` on a larger builder.

`docker compose down --remove-orphans` leaves no anonymous volume behind;
`node --test scripts/compose-volume-coverage.test.mjs` checks that every VOLUME
an image declares is covered in every compose file of this repository.

### Fork schema migrations

The fork's own migrations run in a separate series,
`db_schema_migration/migrations/migration_accuratrial_001` … `_010`, so they
never collide with upstream's numbering (`_001` … `_009` were `migration_024` …
`_032` before upstream 2.10 shipped its own `migration_024`). See
[`migration_overview_accuratrial_001.md`](db_schema_migration/migrations/migration_overview_accuratrial_001.md).

`_010` must run before upstream `migration_024`: it adds the sponsor ODM datatype
`text` to CODMDT so `text` items keep their datatype
([`migration_overview_accuratrial_010.md`](db_schema_migration/migrations/migration_overview_accuratrial_010.md)).

### Retired Proposal V2 delivery worker

The former `import_osb_proposal_v2` command now refuses execution before opening
an API or database client. IL retired direct Proposal V2 delivery and removed its
outbox in migration 061. SourceFactPackageV1 ingestion and the governed Package V2
workflow are the supported authority path. The old worker and hash validators
remain for forensic tests with explicitly injected adapters; no environment flag
re-enables delivery and no replacement delivery API is introduced.

The separate `import_360i` legacy helper remains limited to disposable migration
work with explicit unsafe-legacy opt-in. It now reads and records through the
[IL owner ledger API](import_sponsor_data/README.md#legacy-il-ledger-owner-api),
with the same payload hashes and native upsert behavior. Importer1.18 also
includes the coordinated native measured-unit preflight correction.
### Line endings on Windows

The repository `.gitattributes` forces Unix line endings for shell and AWK
scripts used inside Linux containers, and pins the normative platform schema
bytes the Command Center registry hashes. A fresh checkout needs no global Git
configuration.

## Prerequisites

Install these once, before setting up any component.

| Tool | Version | Needed by |
|---|---|---|
| [Docker](https://docs.docker.com/engine/install/) | any recent | `db`, full-stack runs |
| Python | 3.14 | `api`, `db`, `db_schema_migration`, `import_standards`, `import_sponsor_data`, `export`, `verifications`, `load_tests` |
| [Pipenv](https://pipenv.pypa.io/en/latest/) | 2023.3.20 or later | all Python components |
| Node.js | 20 | `frontend`, `documentation_portal`, `system_tests` |

Notes:

- On Windows, run the shell scripts under WSL/WSL2, or run a Neo4j Desktop
  database and point the environment variables at it.
- Verify your user is in the `docker` group before starting.
- Every Python component is a separate Pipenv project with its own `Pipfile`.
  Install its dependencies from inside that directory, not from the repo root.

## Local development setup

Each Python component follows the same three steps. Run them from inside the
component directory.

```sh
cd <component>
pipenv sync --dev              # install dependencies
cp .env.example .env           # then edit the values
pipenv run <script>            # see the component README for its scripts
```

Not every component ships a `.env.example`:

| Component | Environment file |
|---|---|
| `api` | `.env.example` |
| `frontend` | `.env.example` |
| `db_schema_migration` | `.env.example` |
| `verifications` | `.env.example` |
| `import_sponsor_data` | `.env.import` |
| `db` | none — see [`db/README.md`](db/README.md) for the contents to create |
| `import_standards` | none — see [`import_standards/README.md`](import_standards/README.md) |
| `export`, `load_tests` | none |

Node components use npm, not yarn:

```sh
cd <component>
npm ci
```

`system_tests/` is the exception: it has no top-level `package.json`. Its two
suites, `ui-tests/` and `neodash-test/`, are separate npm projects — run
`npm ci` from inside a suite directory.

## Start the Neo4j database

Every backend component talks to the same Neo4j instance. Start it once.

```sh
cd db
# create .env — see db/README.md for the full variable list
./create_neo4j_local.sh
pipenv sync --dev
pipenv run init_neo4j
```

The script has no port defaults of its own — it reads them from the `.env` you
create. The values `db/README.md` documents for a local Docker setup:

| Port | Purpose |
|---|---|
| 5074 | Neo4j Browser (HTTP) |
| 5078 | Bolt |

If you run Neo4j Desktop instead, the defaults are 7474 (HTTP) and 7687
(Bolt); set `NEO4J_MDR_HTTP_PORT` and `NEO4J_MDR_BOLT_PORT` accordingly.

Full detail, including how the init script handles clearing and backing up an
existing database: [`db/README.md`](db/README.md).

## Populate the database

The steps below are order-dependent. Each one assumes the previous has
completed.

1. **Initialise the schema** — [`db/`](db/README.md). Creates constraints and
   indexes on an empty database.
2. **Import CDISC standards** — [`import_standards/`](import_standards/README.md).
   Writes directly to Neo4j; the API does not need to be running.
3. **Start the API** — [`api/`](api/README.md). `pipenv run dev` serves on
   port 8000. `NEO4J_DSN` in `api/.env` must carry the Bolt port from step 1:
   the shipped `.env.example` has `7687`, the Docker setup above uses `5078`.
4. **Import sponsor data** — [`import_sponsor_data/`](import_sponsor_data/README.md).
   Imports through the API, so step 3 must be running.

Once the database is populated, start the frontend:

```sh
cd frontend
npm ci
npm run dev        # http://localhost:5173
```

## Running the full stack in Docker

This fork keeps a root `compose.yaml` for the whole solution; see
[Running the full stack in Docker](#running-the-full-stack-in-docker-1) under
*AccuraTrial fork*. Individual components also ship their own `compose.yaml`,
used by their build pipelines — `api/`, `frontend/`, `documentation_portal/`,
`db_schema_migration/`, `export/`, `import_standards/` and `import_sponsor_data/`.

## Component documentation

| Component | README |
|---|---|
| API | [`api/README.md`](api/README.md) |
| API extensions | [`api/extensions/README.md`](api/extensions/README.md) |
| Frontend | [`frontend/README.md`](frontend/README.md) |
| Database | [`db/README.md`](db/README.md) |
| Schema migrations | [`db_schema_migration/README.md`](db_schema_migration/README.md) |
| CDISC standards import | [`import_standards/README.md`](import_standards/README.md) |
| Sponsor data import | [`import_sponsor_data/README.md`](import_sponsor_data/README.md) |
| Export | [`export/README.md`](export/README.md) |
| Documentation portal | [`documentation_portal/README.md`](documentation_portal/README.md) |
| System tests | [`system_tests/README.md`](system_tests/README.md) |
| Verifications | [`verifications/README.md`](verifications/README.md) |
| Load tests | [`load_tests/README.md`](load_tests/README.md) |

## Tests and quality gates

Each component defines its own scripts. Check the component's `Pipfile`
`[scripts]` block or `package.json` `scripts` object rather than guessing.

The most-used ones:

```sh
cd api
pipenv run testunit          # unit tests
pipenv run testint           # integration tests, needs Neo4j
pipenv run lint              # pylint
pipenv run mypy
pipenv run format            # isort then black
pipenv run openapi           # regenerate openapi.json after route changes

cd frontend
npm run lint
npm run test:smoke
```

Continuous integration is defined in [`.github/workflows/`](.github/workflows).
`pr-required-checks.yml` detects which paths a pull request touches and
dispatches the matching per-component workflow.

## Security

See [`SECURITY.md`](SECURITY.md).

Licence and contribution terms are per component: where a component directory
contains a `LICENSE.md` or `CONTRIBUTING.md`, that file governs the component.

## AI-agent skills

This repository includes AI-agent skills for common development and review
workflows. Invoke a skill in a supported AI-agent chat by entering its name,
for example `/summarize-pr`.

| Skill | Use it for |
|---|---|
| `/develop-main-api` | Implement or change endpoints in the main Clinical MDR API. |
| `/develop-consumer-api` | Implement or change Consumer API endpoints. |
| `/develop-extensions-api` | Implement or change Extensions API endpoints. |
| `/develop-frontend` | Implement or change the OpenStudyBuilder Vue frontend. |
| `/review-api` | Review API branch changes against `origin/main`. |
| `/review-frontend` | Review frontend branch changes against `origin/main`. |
| `/summarize-pr` | Create one PR description covering all changed components. |
| `/summarize-pr-api` | Create a detailed PR description for changes under `api/`. |
| `/summarize-pr-frontend` | Create a detailed PR description for changes under `frontend/`. |
| `/neodash-ai-documentation` | Create NeoDash report documentation with annotated screenshots. |

The API and frontend component directories also contain implementation details
and conventions for their respective skills.