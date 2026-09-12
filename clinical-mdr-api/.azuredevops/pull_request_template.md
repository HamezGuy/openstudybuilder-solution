Before submitting/completing this PR, address the following checklist:

## Basics
- [ ] Main branch is merged into your feature branch
- [ ] Links to related PRs are included
- [ ] Are new dependencies in a stable state, widely used and have appropriate licenses?

## Quality  
- [ ] Code builds without any `warnings` (e.g. about deprecated features/libraries)
- [ ] API endpoints follow [Zalando API Guidelines](https://opensource.zalando.com/restful-api-guidelines/) naming conventions and best practices
- [ ] No issues reported by SonarQube
- [ ] Tests cover the implemented functionality/code sufficiently
- [ ] Consider refactoring code that needs it, e.g. remove duplicate lines by adding reusable methods
- [ ] Relevant GET endpoints added to the list of [API checks](https://orgremoved.visualstudio.com/Clinical-MDR/_git/verifications?path=/tests/test_api.py) performed by the [verifications pipeline](https://orgremoved.visualstudio.com/Clinical-MDR/_build?definitionId=6184)
- [ ] Relevant [DB constraints checks](https://orgremoved.visualstudio.com/Clinical-MDR/_git/verifications?path=/tests/test_db.py) added to the [verifications pipeline](https://orgremoved.visualstudio.com/Clinical-MDR/_build?definitionId=6184) (in addition to performing these DB checks as part of [API tests](https://orgremoved.visualstudio.com/Clinical-MDR/_git/clinical-mdr-api?path=/clinical_mdr_api/tests/integration/api), where appropriate)

## Performance
- [ ] Relevant GET endpoints are covered by [load tests](https://orgremoved.visualstudio.com/Clinical-MDR/_git/studybuilder-load-test?path=/tests/endpoints.py) (executed by [this](https://orgremoved.visualstudio.com/Clinical-MDR/_build?definitionId=6964) pipeline)
- [ ] Performance is satisfactory, i.e. endpoints return in less than 1s for any realistic number of entities (e.g. SoA with 100 visits * 100 activities)
    - Tips
        - Can many small DB calls be replaced by fewer larger ones?
        - Can the response size be decreased, e.g. by including only a minimal set of relevant fields?
        - If filtering is applied, can we include it in the database query instead of applying it after retrieving all instances?
        - Are API calls paginated where appropriate?

## Documentation
- [ ] API specification is sufficient and in accordance with the implemented functionality
    - [ ] Descriptions/examples of API endpoints/parameters are grammatically and semantically correct, and easy to use by API consumers
    - [ ] Default/example values of API query parameters do not produce errors when consumer tries to send a request via SwaggerUI
- [ ] [Physical data model](https://orgremoved.visualstudio.com/Clinical-MDR/_git/neo4j-mdr-db?path=/model/physical_data_model) is in sync with our neomodel data model (defined in `domain_repositories/models` folder)
    - Nodes/relations names and fields
    - Relationship cardinalities

- [ ] `README` file is up-to-date

## Other
- [ ] If breaking changes to existing API endpoints or data model are introduced:
    - Inform the affected consuming systems (e.g. Word add-in).
    - Create related PR's or/and work items covering the needed changes in other repositories, e.g. [frontend](https://orgremoved.visualstudio.com/Clinical-MDR/_git/studybuilder), [studybuilder-import](https://orgremoved.visualstudio.com/Clinical-MDR/_git/studybuilder-import), [db-schema-migration](https://orgremoved.visualstudio.com/Clinical-MDR/_git/db-schema-migration), [verifications](https://orgremoved.visualstudio.com/Clinical-MDR/_git/verifications?path=/tests). 

---



