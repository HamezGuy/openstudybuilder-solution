# Neo4j Database

- **ORM**: Uses `neomodel` for object-graph mapping
- **Connection**: Configured via `NEO4J_DSN` environment variable (format: `bolt://user:password@host:port/database`)
- **Setup**: Tests require local Neo4j instance (use docker image from `neo4j-mdr-db` repository)
- **Configuration**: Database is configured in `clinical_mdr_api/main.py` on startup via `common.database.configure_database()`

## Special Considerations

- **Versioning**: Domain objects use versioning (see `clinical_mdr_api/domains/versioned_object_aggregate.py`)
- **Concurrency**: Repository layer handles optimistic locking (see tests in `clinical_mdr_api/tests/integration/repositories/concurrency/`)
- **Pagination**: Default page size is 10, max is 1000 (configurable via env vars)


