# Architecture

## Three-Layer DDD Architecture

The codebase strictly follows DDD with a three-layer responsibility segregation:

### 1. Domain Layer (`clinical_mdr_api/domains/`)
- Contains all business logic
- Aggregate roots are the central concept
- Independent of all other layers (no external dependencies)
- Each aggregate typically has its own subdirectory

### 2. Repository Layer (`clinical_mdr_api/domain_repositories/`)
- Responsible for persistence and restoration of aggregates to/from Neo4j
- Handles concurrency control and transaction semantics
- Only depends on the domain layer (specifically the aggregate it persists)
- Uses Memento pattern for state transformation between domain objects and database

### 3. Service Layer (`clinical_mdr_api/`)
- **Routers** (`routers/`) - Define FastAPI routes
- **Services** (`services/`) - Convert API requests to repository/domain calls and results to responses
- **Models** (`models/`) - Pydantic models for request/response validation
- **Exceptions** (`exceptions/`) - Custom API exceptions
- Only services depend on repositories; routers depend on services

**Key Principle**: Layers communicate through public interfaces. Names without leading underscores are public API. This enables loose coupling and independent evolution of each layer.

## Development Workflow for New Features

Follow this order when implementing features:

1. **Domain layer first** - Design and test business logic (can be done independently)
2. **Repository layer** - Implement persistence (depends only on domain layer)
3. **Service layer** - Wire up API endpoints (depends on repositories)

This inside-out approach allows incremental development with testing at each layer.


