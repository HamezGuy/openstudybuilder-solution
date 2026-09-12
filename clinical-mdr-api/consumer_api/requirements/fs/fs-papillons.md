# Overview

This document describes a set of GET endpoints in the OpenStudyBuilder Consumer API, which expose information about various OpenStudyBuilder entities in the format suitable for SDTM generation.

Detailed API specification of each endpoint is found in the `consumer_api/openapi.json` file.

# Schedule of Activities

## FS-ConsumerApi-Papillons-Soa-Get-010 [`URS-ConsumerApi-Studies-Papillons`]

Consumers must be able to retrieve SoA in the Papillons-suitable format by calling the `GET /papillons/soa` endpoint.

### Request

Consumer must send `project` and `study_number` query parameters.

Endpoint must support the following optional query parameters:

- subpart
- study_version_number
- datetime

### Response

Response must include information about schedule of activities, i.e. matrix of activites (topic codes) and visits for the specified study and study version or datetime.

### Test coverage

| Test File                    | Test Function          |
| ---------------------------- | ---------------------- |
| tests/v1/test_api_studies.py | test_get_papillons_soa |


