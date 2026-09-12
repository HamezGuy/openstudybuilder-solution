"""Bounded projections from existing native nodes; no graph writes or models."""
from typing import Any

from neo4j import Query
from neomodel import db

MAX_JSON_CHARS = 65_536


class NativeItemObservationRepository:
    def query(self, text: str, params: dict[str, Any], timeout: float) -> list:
        # The route has no ambient mutation transaction. A per-query driver
        # deadline complements the total observation deadline in the service.
        rows, _ = db.cypher_query(Query(text, timeout=timeout), params)
        return rows or []

    def scope(self, p: dict, timeout: float) -> list:
        return self.query("""
          MATCH (binding:PlatformNativeStudyBinding {tenant_id:$tenantId,
            platform_study_id:$platformStudyId,namespace:'accuratrials-osb',
            object_type:'study-draft-root',status:'active'})
          MATCH (scope:DomainStudyScope {study_uid:binding.native_study_id,
            tenant_id:$tenantId,status:'active'})
          MATCH (study:StudyRoot {uid:scope.study_uid})
          RETURN binding.binding_id,binding.native_study_id,binding.native_version
          LIMIT 2
        """, p, timeout)

    def study_heads(self, p: dict, timeout: float) -> list:
        return self.query("""
          MATCH (study:StudyRoot {uid:$nativeStudyId})
          OPTIONAL MATCH (study)-[:LATEST]->(latest:StudyValue)
          MATCH (study)-[head:LATEST_DRAFT|LATEST_LOCKED|LATEST_RELEASED]->(value:StudyValue)
          RETURN type(head),head.version,head.status,
            toString(head.start_date),toString(head.end_date),value=latest LIMIT 5
        """, p, timeout)

    def custody(self, p: dict, timeout: float) -> list:
        return self.query("""
          MATCH (evidence:OsbNativeEvidenceSetV1 {tenant_id:$tenantId,
            platform_study_id:$platformStudyId,evidence_set_version_id:$evidenceSetVersionId,
            payload_hash:$evidenceSetHash,decision_hash:$decisionHash})
            -[:EXECUTED_DECISION]->(decision:StudyMappingDecisionV1 {
            tenant_id:$tenantId,platform_study_id:$platformStudyId,
            decision_id:$decisionId,decision_hash:$decisionHash})
          MATCH (candidate:OsbCandidateSetV1 {tenant_id:$tenantId,
            platform_study_id:$platformStudyId,candidate_set_version_id:$candidateSetVersionId,
            payload_hash:$candidateSetHash,context_hash:$mappingContextHash})
            -[:GENERATED_FROM]->(request:OsbCandidateRequestV1 {
            tenant_id:$tenantId,platform_study_id:$platformStudyId})
          MATCH (context:OsbMappingContextSnapshot {context_hash:$mappingContextHash})
          MATCH (operation:NativeOperationEvidenceV1 {tenant_id:$tenantId,
            platform_study_id:$platformStudyId,evidence_id:$evidenceId,decision_id:$decisionId})
          WITH [decision.payload_json,candidate.payload_json,request.payload_json,
            evidence.payload_json,context.content_json,evidence.artifact_ref_json,
            operation.payload_json] AS blobs,operation
          RETURN CASE WHEN all(blob IN blobs WHERE blob IS NOT NULL AND size(blob)<=$maxChars)
            THEN blobs ELSE null END,operation.payload_hash LIMIT 2
        """, {**p, "maxChars": MAX_JSON_CHARS}, timeout)

    def item(self, p: dict, timeout: float) -> list:
        return self.query("""
          MATCH (root:OdmItemRoot {uid:$itemUid})-[:LATEST]->(value:OdmItemValue)
          MATCH (root)-[version:HAS_VERSION]->(value)
          WHERE version.end_date IS NULL
          WITH root,value,version,
            [value.name,value.oid,value.prompt,value.datatype,value.sas_field_name,
             value.sds_var_name,value.origin,value.comment] AS strings
          RETURN root.uid,version.version,version.status,
            CASE WHEN all(v IN strings WHERE v IS NULL OR size(v)<=4096) THEN
             {name:value.name,oid:value.oid,prompt:value.prompt,datatype:value.datatype,
              length:value.length,significantDigits:value.significant_digits,
              sasFieldName:value.sas_field_name,sdsVarName:value.sds_var_name,
              origin:value.origin,comment:value.comment} ELSE null END,
            EXISTS { (value)-[:HAS_UNIT_DEFINITION|HAS_CODELIST|HAS_CODELIST_TERM]->() }
          LIMIT 2
        """, p, timeout)
