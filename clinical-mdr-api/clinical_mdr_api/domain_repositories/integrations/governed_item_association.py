"""Bounded custody on the existing proposal review graph and native study root."""
from clinical_mdr_api.domain_repositories.integrations.selected_activity_item_observation import SelectedActivityItemRepository
from common.utils import convert_to_datetime


class GovernedItemAssociationRepository(SelectedActivityItemRepository):
    def review_custody(self, p, timeout):
        rows = self.query("""
          MATCH (proposal:OsbProposalReview {proposal_hash:$proposalHash})
            -[:HAS_REVIEW_OBJECT]->(object:OsbProposalReviewObject {proposal_object_id:$proposalObjectId})
          MATCH (proposal)-[:USES_MAPPING_CONTEXT]->(context:OsbMappingContextSnapshot)
          OPTIONAL MATCH (object)-[:LATEST_DECISION]->(decision:OsbProposalReviewDecision)
          RETURN CASE WHEN size(proposal.proposal_json)<=65536 THEN proposal.proposal_json ELSE null END,
            CASE WHEN size(object.object_json)<=65536 THEN object.object_json ELSE null END,
            CASE WHEN size(context.content_json)<=65536 THEN context.content_json ELSE null END,
            CASE WHEN size(decision.decision_id)<=128 THEN decision.decision_id ELSE null END,
            CASE WHEN size(decision.action)<=64 THEN decision.action ELSE null END,
            CASE WHEN size(decision.candidate_key)<=64 THEN decision.candidate_key ELSE null END,
            CASE WHEN size(coalesce(decision.note,''))<=16384 THEN decision.note ELSE 'oversized' END,
            CASE WHEN size(decision.signature_id)<=512 THEN decision.signature_id ELSE null END,
            decision.signature_verified,
            CASE WHEN size(decision.decision_content_hash)<=64 THEN decision.decision_content_hash ELSE null END,
            CASE WHEN size(decision.actor_id)<=512 THEN decision.actor_id ELSE null END,
            decision.decided_at,CASE WHEN size(context.context_hash)<=64 THEN context.context_hash ELSE null END
          LIMIT 2
        """, p, timeout)
        for row in rows:
            if row[11] is not None:
                row[11] = convert_to_datetime(row[11]).isoformat()
        return rows

    def associations(self, p, timeout):
        # All live decisions count, across proposal objects, for the same exact
        # CSL/native target. No latest-by-time choice between associations.
        return self.query("""
          MATCH (proposal:OsbProposalReview)-[:HAS_REVIEW_OBJECT]->(object:OsbProposalReviewObject)
            -[:LATEST_DECISION]->(decision:OsbProposalReviewDecision)
            -[:HAS_GOVERNED_ITEM_ASSOCIATION]->(association:OsbGovernedItemAssociation {
              tenant_id:$tenantId,target_key:$targetKey})
          RETURN CASE WHEN size(association.association_id)<=128 THEN association.association_id ELSE null END,
            CASE WHEN size(association.payload_hash)<=71 THEN association.payload_hash ELSE null END,
            CASE WHEN size(association.payload_json)<=16384 THEN association.payload_json ELSE null END,
            proposal.proposal_hash=$proposalHash AND object.proposal_object_id=$proposalObjectId
              AND decision.decision_id=$reviewDecisionId AND decision.decision_content_hash=$reviewDecisionHash
          LIMIT 2
        """, p, timeout)

    def append_association(self, p, timeout):
        # Native StudyRoot lock serializes associations on this target study;
        # the existing review object lock serializes against append_decision.
        # Authority is selected after that potentially blocking lock. Lock the
        # exact scope/binding too, and recheck their pins after acquisition so a
        # status/version withdrawal cannot be borrowed from a pre-lock read.
        # The original decision is never modified or relabeled as this review.
        return self.query("""
          MATCH (study:StudyRoot {uid:$nativeStudyId})
          SET study.governed_item_association_epoch=coalesce(study.governed_item_association_epoch,0)+1
          WITH study
          MATCH (proposal:OsbProposalReview {proposal_hash:$proposalHash})
            -[:HAS_REVIEW_OBJECT]->(object:OsbProposalReviewObject {proposal_object_id:$proposalObjectId})
          SET object.governed_item_association_epoch=coalesce(object.governed_item_association_epoch,0)+1
          WITH study,proposal,object
          CALL {
            CALL { MATCH (scope:DomainStudyScope {study_uid:$nativeStudyId,tenant_id:$tenantId,status:'active'})
              RETURN scope LIMIT 2 }
            RETURN collect(scope) AS scopes
          }
          WITH study,proposal,object,scopes WHERE size(scopes)=1
          WITH study,proposal,object,scopes[0] AS scope
          SET scope.governed_item_association_epoch=coalesce(scope.governed_item_association_epoch,0)+1
          WITH study,proposal,object,scope
          CALL {
            CALL { MATCH (binding:PlatformNativeStudyBinding {tenant_id:$tenantId,platform_study_id:$platformStudyId,
                namespace:'accuratrials-osb',object_type:'study-draft-root',status:'active'})
              RETURN binding LIMIT 2 }
            RETURN collect(binding) AS bindings
          }
          WITH study,proposal,object,scope,bindings WHERE size(bindings)=1
          WITH study,proposal,object,scope,bindings[0] AS binding
          SET binding.governed_item_association_epoch=coalesce(binding.governed_item_association_epoch,0)+1
          WITH study,proposal,object,scope,binding
          WHERE scope.study_uid=$nativeStudyId AND scope.tenant_id=$tenantId AND scope.status='active'
            AND binding.tenant_id=$tenantId AND binding.platform_study_id=$platformStudyId
            AND binding.namespace='accuratrials-osb' AND binding.object_type='study-draft-root'
            AND binding.native_study_id=$nativeStudyId AND binding.native_version=$nativeStudyVersion
            AND binding.binding_id=$bindingId AND binding.status='active'
          MATCH (object)-[:LATEST_DECISION]->(decision:OsbProposalReviewDecision)
          WITH study,proposal,object,collect(decision) AS decisions
          WHERE size(decisions)=1 AND decisions[0].decision_id=$reviewDecisionId
            AND decisions[0].decision_content_hash=$reviewDecisionHash
            AND proposal.proposal_json=$proposalJson AND object.object_json=$objectJson
            AND timestamp()<$credentialExpiryMillis
          WITH study,decisions[0] AS decision
          CALL {
            CALL { MATCH (:OsbProposalReviewObject)-[:LATEST_DECISION]->(:OsbProposalReviewDecision)
              -[:HAS_GOVERNED_ITEM_ASSOCIATION]->(existing:OsbGovernedItemAssociation {
                tenant_id:$tenantId,target_key:$targetKey})
              RETURN existing LIMIT 2 }
            RETURN count(existing) AS existing_count
          }
          WITH decision WHERE existing_count=0
          CREATE (association:OsbGovernedItemAssociation {tenant_id:$tenantId,target_key:$targetKey,
            association_id:$associationId,payload_hash:$associationHash,payload_json:$payloadJson,created_at:datetime()})
          CREATE (decision)-[:HAS_GOVERNED_ITEM_ASSOCIATION]->(association)
          RETURN association.association_id,association.payload_hash,association.payload_json
        """, p, timeout)
