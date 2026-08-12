"""Valid cross-boundary Proposal V2.1 fixture with independently derived IDs."""

import hashlib

from clinical_mdr_api.services.integrations.canonical_json import canonical_hash

OPENAPI_HASH = "a" * 64
CONTEXT_HASH = "c" * 64
DOCUMENT_HASH = "d" * 64
FACT_HASH = "f" * 64


def build_candidate(context_hash=CONTEXT_HASH, uid="Activity_1"):
    identity = {
        "resourceFamily": "activities",
        "resourceType": "Activity",
        "uid": uid,
        "version": "1.0",
        "packageUid": None,
        "catalogueName": None,
        "packageVersion": None,
        "packageEffectiveDate": None,
        "libraryName": "Sponsor",
        "parentResourceType": "Library",
        "parentUid": "Sponsor",
        "parentVersion": "unversioned-library-name",
        "parentSubmissionValue": None,
        "modelUid": None,
        "modelVersion": None,
        "implementationGuideUid": None,
        "implementationGuideVersion": None,
        "mappingTargetUid": None,
        "mappingTargetVersion": None,
        "status": "Final",
        "validFrom": None,
        "validTo": None,
        "submissionValue": None,
        "ucumExpression": None,
        "extensible": None,
        "dimension": None,
        "conversionFactorToMaster": None,
        "stableOid": None,
        "criteriaTypeUid": None,
        "parameterCount": 0,
        "contextHash": context_hash,
        "code": None,
    }
    return {
        "candidateKey": canonical_hash(identity),
        **identity,
        "label": "Blood Pressure",
    }


def build_context(context_hash=CONTEXT_HASH, uid="Activity_1"):
    del context_hash  # The repository key carries the exact context identity.
    return {
        "schemaVersion": "osb-mapping-context/2.0",
        "mappingAuthority": "OpenStudyBuilder",
        "osbOpenApiHash": OPENAPI_HASH,
        "governed": True,
        "candidates": {
            "activities": [
                {
                    "identity_schema_version": "osb-candidate-key/2.0",
                    "resource_family": "activities",
                    "resource_type": "Activity",
                    "uid": uid,
                    "version": "1.0",
                    "package_uid": None,
                    "catalogue_name": None,
                    "package_version": None,
                    "package_effective_date": None,
                    "library_name": "Sponsor",
                    "parent_resource_type": "Library",
                    "parent_uid": "Sponsor",
                    "parent_version": "unversioned-library-name",
                    "parent_submission_value": None,
                    "model_uid": None,
                    "model_version": None,
                    "implementation_guide_uid": None,
                    "implementation_guide_version": None,
                    "mapping_target_uid": None,
                    "mapping_target_version": None,
                    "status": "Final",
                    "valid_from": None,
                    "valid_to": None,
                    "submission_value": None,
                    "ucum_expression": None,
                    "extensible": None,
                    "dimension": None,
                    "conversion_factor_to_master": None,
                    "stable_oid": None,
                    "criteria_type_uid": None,
                    "parameter_count": 0,
                    "label": "Blood Pressure",
                    "code": None,
                }
            ]
        },
    }


def _object(source_build_hash, target_key, section, resource_type, candidate=None):
    concept_id = canonical_hash(
        {
            "factId": "fact-1",
            "revision": 1,
            "factContentHash": FACT_HASH,
            "targetKey": target_key,
        }
    )
    object_id = canonical_hash(
        {
            "sourceBuildHash": source_build_hash,
            "conceptId": concept_id,
            "targetKey": target_key,
            "section": section,
            "proposedResourceType": resource_type,
        }
    )
    candidates = [candidate] if candidate else []
    evidence_text = "Blood Pressure"
    return {
        "proposalObjectId": object_id,
        "conceptId": concept_id,
        "targetKey": target_key,
        "section": section,
        "dependencyTargetKeys": [],
        "source": {
            "assertionType": "ASSESSMENT",
            "clinicalDomain": "vital_signs",
            "exactQuote": evidence_text,
            "values": [
                {
                    "name": "assessment",
                    "sourcePath": "/assessment",
                    "valueType": "string",
                    "value": evidence_text,
                }
            ],
            "label": evidence_text,
        },
        "mapping": {
            "factIds": ["fact-1"],
            "evidence": [
                {
                    "provenanceId": "prov-1",
                    "documentVersionId": "document-1",
                    "documentContentHash": DOCUMENT_HASH,
                    "sourceSpanRef": "span-1",
                    "documentPartRef": "part-1",
                    "page": 1,
                    "box": {
                        "x": 1,
                        "y": 2,
                        "width": 3,
                        "height": 4,
                        "space": "pdf_points",
                        "renderScale": None,
                    },
                    "exact": True,
                    "verbatimText": evidence_text,
                    "textHash": hashlib.sha256(evidence_text.encode()).hexdigest(),
                }
            ],
            "proposedResourceType": resource_type,
            "candidates": candidates,
            "selectedCandidate": None,
            "matchMethod": "exact_text" if candidate else "none",
            "extractionConfidence": 0.9,
            "mappingConfidence": 0.8 if candidate else None,
            "disposition": "review" if candidate else "unresolved",
            "reviewerDecision": None,
        },
    }


def build_proposal(context_hash=CONTEXT_HASH, candidate_uid="Activity_1"):
    source_documents = [
        {"documentVersionId": "document-1", "contentHash": DOCUMENT_HASH}
    ]
    source_fact_refs = [
        {
            "factId": "fact-1",
            "revision": 1,
            "eligibleApproved": True,
            "lifecycle": "approved",
            "lastOperation": "approved",
            "factContentHash": FACT_HASH,
            "reviewDecision": "accepted",
            "signatureId": None,
            "documentVersionIds": ["document-1"],
        }
    ]
    source_build_hash = canonical_hash(
        {
            "tenantId": "tenant-1",
            "studyId": "study-1",
            "projectId": None,
            "authorityMode": "shadow",
            "sourceRunIds": ["run-1"],
            "sourceDocuments": source_documents,
            "sourceFactRefs": source_fact_refs,
        }
    )
    candidate = build_candidate(context_hash, candidate_uid)
    activity = _object(
        source_build_hash,
        "activity",
        "activitiesItems",
        "StudySelectionActivity",
        candidate,
    )
    item = _object(source_build_hash, "odm-item", "odm", "OdmItem")
    objects = [activity, item]
    sections = {
        "studySetup": [],
        "standards": [],
        "objectives": [],
        "endpoints": [],
        "criteria": [],
        "productsDosing": [],
        "armsCohortsBranches": [],
        "epochsElementsCells": [],
        "visitsTiming": [],
        "activitiesItems": [activity],
        "soa": [],
        "odm": [item],
        "extensions": [],
        "retainedNarrative": [],
        "unresolved": [],
    }
    reconciliation = {
        "sourceFacts": 1,
        "eligibleApprovedFacts": 1,
        "proposedObjects": 2,
        "nativeStudyMutationTargets": 1,
        "governedLibraryReferenceTargets": 1,
        "governedExtensionTargets": 0,
        "retainedNarrativeTargets": 0,
        "unresolvedTargets": 0,
        "nativeTargetSourceFacts": 1,
        "fullyNativeTargetSourceFacts": 1,
        "mappedSourceFacts": 1,
        "exact": 0,
        "review": 1,
        "createRequests": 0,
        "unresolved": 1,
        "notApplicable": 0,
        "excluded": 0,
        "quarantined": 0,
        "rejected": 0,
        "notBuildFeeding": 0,
        "archived": 0,
        "unreviewed": 0,
        "duplicateLinks": 0,
        "supersessionLinks": 0,
        "signedExclusions": 0,
        "balanced": True,
        "duplicateFactIds": [],
        "missingSourceFactIds": [],
        "dispositions": [],
    }
    proposal_id = canonical_hash(
        {
            "tenantId": "tenant-1",
            "studyId": "study-1",
            "projectId": None,
            "authorityMode": "shadow",
            "sourceBuildHash": source_build_hash,
            "osbOpenApiHash": OPENAPI_HASH,
            "osbMappingContextHash": context_hash,
            "proposalObjects": objects,
            "sourceDispositions": [],
        }
    )
    content = {
        "formatVersion": "osb-proposal/2.1",
        "canonicalizationVersion": "canonical-json/1.0",
        "proposalId": proposal_id,
        "sourceBuildHash": source_build_hash,
        "tenantId": "tenant-1",
        "studyId": "study-1",
        "projectId": None,
        "sourceRunIds": ["run-1"],
        "sourceDocumentVersionIds": ["document-1"],
        "sourceDocuments": source_documents,
        "previousProposalHash": None,
        "osbOpenApiHash": OPENAPI_HASH,
        "osbMappingContextHash": context_hash,
        "authorityMode": "shadow",
        "sections": sections,
        "reconciliation": reconciliation,
        "sourceFactRefs": source_fact_refs,
    }
    return {**content, "proposalHash": canonical_hash(content)}
