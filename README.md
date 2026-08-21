[[_TOC_]]


# Status quo of the OpenStudyBuilder

The OpenStudyBuilder solution introduces a new approach for working with studies that once fully implemented will drive end-to-end consistency and more efficient processes - all the way from protocol development and CRF design - to creation of datasets, analysis, reporting, submission to health authorities and public disclosure of study information.

OpenStudyBuilder is a next generation end-to-end clinical data standards and study specification solution, enabling clinical study data solutions to use linked metadata for higher degree of automation, limiting manual document driven work processes, enabling a Digital Data Flow approach.

OpenStudyBuilder is the open source version of the internal StudyBuilder solution at Novo Nordisk. Not all titles or logos in the application are yet changed to be 'OpenStudyBuilder' - when the term 'StudyBuilder' is used, it is therefore a synonym for 'OpenStudyBuilder'. This will be changed in coming updates.

For further information on the OpenStudyBuilder solution, please refer to the [OpenStudyBuilder homepage](https://openstudybuilder.com).

## Related Repositories

The following repositories are related to OpenStudyBuilder

- [OpenStudyBuilder-Word-Add-In](https://github.com/NovoNordisk-OpenSource/openstudybuilder-word-addin)
- [OpenStudyBuilder-accelerators](https://github.com/NovoNordisk-OpenSource/openstudybuilder-accelerators)


# Introduction

StudyBuilder consists of a few main components, that are all included as subdirectories in this repository.

- neo4j-mdr-db: Configuration files and initialization scripts for the Neo4j database.
- mdr-standards-import: Scripts for populating the database with clinical standards.
- clinical-mdr-api: The Python/FastAPI backend.
- studybuilder-import: Python scripts for populating the database with sponsor standards and codelists.
- studybuilder-export: Python scripts for exporting the database.
- studybuilder: The Vue.js frontend.
- documentation-portal: Solution documentation.
- system-tests/ui-tests: End-to-end tests for the OpenStudyBuilder solution with Gherkin and Cypress.
- osb-neodash: Configuration for NeoDash in OpenStudyBuilder style.

Each directory contains a more detailed ReadMe for that component.

## AccuraTrial Command Center deployment

In the AccuraTrial platform, OpenStudyBuilder is a private, authenticated child
application behind Command Center:

- Command Center owns interactive login and issues five-minute RS256 tokens with
  audience `accuratrial-openstudybuilder`.
- OSB API/RBAC stays enabled; the Vue OAuth UI stays disabled to avoid a second
  login. Browser API traffic must enter through the managed OSB origin so Command
  Center can inject the audience token.
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

The OpenStudyBuilder landscape has connected tools available. You can find the following:

- [OpenStudyBuilder Word-Addin](https://github.com/NovoNordisk-OpenSource/openstudybuilder-word-addin) - a Microsoft Word Add-in to support the creation of clinical protocols, using the OpenStudyBuilder study definitions.

# System Requirements

A Docker environment with at least 6GB of memory allocated is required.

The solution is tested on Ubuntu and Windows (WSL 2).
For alternative platforms, please refer to [Platform architecture notes](platform-architecture-notes).

The Docker environment can be either Docker Desktop or Docker Engine 
(community version), with Compose V2 integrated as a CLI plugin. 

OpenStudyBuilder has been tested on the following Docker environments:

- Windows 11 (WSL 2)
- Ubuntu 20.04 Focal - Docker Engine community version 23.0.1 

To see versions of the installed Docker engine, run: `docker version`

To check if the Docker Compose V2 is also included, run: `docker compose version`

Windows installation link: [Windows installation](https://docs.docker.com/desktop/install/windows-install/)

Ubuntu installation link: [Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/)

To test your local docker installation run the following command
in a non administrator or root shell: `docker run hello-world`

If this is not working see this link for Ubuntu rootless configuration:
[Docker Rootless](https://docs.docker.com/engine/security/rootless/)

The seeded database build defaults to a bounded 1/3 GiB heap and 1 GiB
page-cache so it can complete in an 8 GiB Docker VM. A larger release builder
can override these without editing Compose:
```
NEO4J_BUILD_HEAP_INITIAL=2G
NEO4J_BUILD_HEAP_MAX=4G
NEO4J_BUILD_PAGECACHE=2G
```

Mind that this will choke the performance of the Neo4j database,
which leads to increased building time of the database component,
up to a few hours.

On Windows installations the WSL engine can take up all system resources.
It is recommended to configure limits. Create a `.wslconfig` file in the user directory, typically `C:\Users\username\`.
Put the following content in the `.wslconfig` file, and change `memory` to a suitable value for the given system. Half the physical RAM is a good starting point.
```
[wsl2]
processors=2
memory=6GB
```
See [Advanced settings configuration in WSL](https://docs.microsoft.com/en-us/windows/wsl/wsl-config)
for all available options.

Git for Windows can translate line endings when files are checked out. The repository
`.gitattributes` file forces Unix line endings for shell and AWK scripts used inside
Linux containers, including the frontend Nginx entrypoint. A fresh checkout needs no
global Git configuration. The effective rules can be checked with:

```shell
git check-attr eol -- studybuilder/config/nginx/sb-config.sh studybuilder/scripts/update-config.awk
```

See [Configuring Git to handle line endings](https://docs.github.com/en/get-started/getting-started-with-git/configuring-git-to-handle-line-endings?platform=windows)
for more information.

## Known Issues with Dockerfiles on ARM64 Architecture

There have been reports of issues when running the Dockerfiles on ARM64 architecture. Recent updates have resolved these problems on several ARM64 machines. If you encounter any other issues, please refer to the following link for more information and to report the inconvenience: [GitLab Issues](https://gitlab.com/Novo-Nordisk/nn-public/openstudybuilder/OpenStudyBuilder-Solution/-/issues).


# Using the preview environment

Your folder structure should look like this:

```
─ OpenStudyBuilder-Solution
  ├─ clinical-mdr-api
  ├─ db-schema-migration
  ├─ documentation-portal
  ├─ mdr-standards-import
  ├─ neo4j-mdr-db
  ├─ osb-neodash
  ├─ studybuilder
  ├─ studybuilder-export
  ├─ studybuilder-import
  └─ system-tests
```

The Docker Compose configuration is `compose.yaml` for the preview 
environment and building containers in pipelines.

The following services are part of this Docker Compose environment.

- _database_ (A Neo4j graph database container including initial data)
- _api_ (A FastAPI container hosting the clinical-mdr-api backend application)
- _consumerapi_ (A FastAPI container hosting API for additional integrations)
- _neodash_ (Container for NeoDash, holding dashboards for OpenStudyBuilder)
- _frontend_ (A Nginx container hosting Vue.js StudyBuilder UI application)
- _documentation_ (A Nginx container hosting Vue.js Study Builder documentation portal)


## Building the Docker images

The Docker container images has to be built before the first use,
and rebuilt on each subsequent release:

```shell
docker compose build
```

Building the Docker images may take 30 minutes or more to complete, 
especially for the database image.

The database build pins the seed tarball, runtime image, and APOC plugin to the
same Neo4j release through `NEO4J_VERSION_PINNED` (default `2026.06.0`). Do not
replace the runtime image with a floating `neo4j:enterprise` tag because Neo4j
cannot open a store created by a newer kernel.

The default project seed includes the two curated CDISC example studies and does
not load the large `DummyStudy` fixture set. Developers who explicitly need those
fixtures can opt in for a database image build:

```shell
INCLUDE_DUMMY_STUDIES=true docker compose build database
```

PowerShell equivalent:

```powershell
$env:INCLUDE_DUMMY_STUDIES = "true"
docker compose build database
Remove-Item Env:INCLUDE_DUMMY_STUDIES
```


## Starting the services

To start up the services for a local evaluation environment, use:

```shell
docker compose up
```

If you add the `-d` option to the command, it will bring up the services to 
run in the background, detached from the terminal.

To validate that the environment is running, inspect the output of this command:

```shell
docker compose ps
```

The output should look like this:

```
NAME                          IMAGE                       COMMAND                  SERVICE         CREATED        STATUS                            PORTS
OpenStudyBuilder-Solution-api-1             build-tools-api             "pipenv run uvicorn"     api             21 hours ago   Up 38 seconds (healthy)           8000/tcp
OpenStudyBuilder-Solution-consumerapi-1     build-tools-consumerapi     "pipenv run uvicorn"     consumerapi     21 hours ago   Up 38 seconds (healthy)           8000/tcp
OpenStudyBuilder-Solution-database-1        build-tools-database        "tini -g -- /startup…"   database        21 hours ago   Up 59 seconds (healthy)           7473/tcp, 127.0.0.1:5001->7474/tcp, 127.0.0.1:5002->7687/tcp
OpenStudyBuilder-Solution-documentation-1   build-tools-documentation   "/docker-entrypoint.…"   documentation   21 hours ago   Up 59 seconds (healthy)           80/tcp, 5006/tcp
OpenStudyBuilder-Solution-frontend-1        build-tools-frontend        "/docker-entrypoint.…"   frontend        21 hours ago   Up 7 seconds (health: starting)   80/tcp, 127.0.0.1:5005->5005/tcp
OpenStudyBuilder-Solution-neodash-1         build-tools-neodash         "/docker-entrypoint.…"   neodash         21 hours ago   Up 38 seconds (healthy)           80/tcp, 5005/tcp, 127.0.0.1:5007->5007/tcp
```


## Accessing the application

- StudyBuilder main application: <http://localhost:5005/>

- StudyBuilder documentation: <http://localhost:5005/doc/>

  It can also be accessed from main web application from the ? sign in top right corner.

- StudyBuilder API (backend application): <http://localhost:5005/api/docs>

- StudyBuilder Consumer API (backend application): <http://localhost:5005/consumer-api/docs/>

- Neo4j dashboard web client: <http://localhost:5005/neodash/>

- Neo4j database web client: <http://localhost:5001/browser/>

  The default username is `neo4j` and the default password is `changeme1234`

## Running the governed Proposal V2 importer against the preview stack

Build the importer image once:

```shell
docker compose -f studybuilder-import/compose.yaml build import
```

The override joins the importer to the root stack's Docker network and addresses
the API as `http://api:5003`. Proposal V2 reads immutable Facts/outbox jobs,
validates the pinned OSB mapping context, and hands itemized targets to OSB review:

```shell
docker compose \
  -f studybuilder-import/compose.yaml \
  -f studybuilder-import/compose.override.yaml \
  run --rm import pipenv run import_osb_proposal_v2 \
  --study <source-study-id> \
  --target-study-uid <osb-study-uid> \
  --target-study-version <draft-version>
```

The default external network is `opensourcebuilder_default`. If the root stack
uses a different Compose project name, set `OSB_NETWORK_NAME` to its network name.
Database credentials and tenant settings remain supplied through the importer's
environment file or command environment; do not commit them. The former
`import_360i` StudyBundleV1 carrier command is retired from runtime wiring. Its
module and historical tables remain only for controlled forensic/migration work
and require explicit unsafe-legacy opt-in when invoked directly.

Command Center invokes the same worker with `--study <study-id>`. That option
scopes intake, review-polling, and native-execution leases to the requested source
study. Native execution additionally requires the explicit target UID/version
arguments (or `OSB_TARGET_STUDY_UID` and `OSB_TARGET_STUDY_VERSION`). The worker
verifies that target is still the same DRAFT version before and after all writes.
Omit the target arguments only for a worker that must stop at review; native jobs
then remain `review_complete` rather than being assigned to an arbitrary study.


## Stopping the services

This command will stop the docker containers, but keep the contents of the 
database for the next start.

```shell
docker compose down --remove-orphans
```


## Updating to a new release

When checking out a new release, the Docker images has to be rebuilt.
Usually the database schema gets changed between releases, so
**the old database has to be destroyed**.
A new database will be initialized when starting the  _database_ service
from the recently rebuilt Docker image.
(The OpenStudyBuilder release does not come with a database migration tool.)

```shell
docker compose build --no-cache  # rebuilds the docker images
docker compose down --remove-orphans --volumes # DESTROYS THE DATABASE volume 
docker compose up -d  # the database service re-creates the database volume on the first start 
```

For hosted environments with database migrations and sequential upgrades,
see the [Environment Update Process](./update_process.md).

## Cleaning up the Docker environment

To clean up the entire Docker environment use the following commands:

**Will delete volumes and cache NOT restricted for the OpenStudyBuilder components.**

```shell
docker compose down --remove-orphans --volumes
docker rmi $(docker images --filter=reference="*_database" -q) -f
docker rmi $(docker images --filter=reference="*_api" -q) -f
docker rmi $(docker images --filter=reference="*_ui" -q) -f
docker rmi $(docker images --filter=reference="*_docs" -q) -f
docker rmi $(docker images --filter=reference="*_sonarqube" -q) -f
docker volume prune # (Will delete all volumes not used, only needed if -v was not used on docker-compose down command)
docker builder prune --all # (Will clean all docker cache files)
```


