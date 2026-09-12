# Project Overview

OpenStudyBuilder Clinical MDR API - A FastAPI-based REST API providing read/write access to clinical metadata stored in a Neo4j database. The codebase follows Domain-Driven Design (DDD) principles and uses Python 3.14.

## Multi-API Structure

The repository contains three separate APIs:

- **Main API** (`clinical_mdr_api/`) - Port 8000 - Primary API for clinical metadata
- **Consumer API** (`consumer_api/`) - Port 8008 - Read-only consumer-facing API
- **Extensions API** (`extensions/`) - Port 8009 - Extension/plugin system

**Common code** is shared via the `common/` directory (config, auth, database, telemetry, exceptions).


