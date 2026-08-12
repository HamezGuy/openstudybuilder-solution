# Governed Proposal V2 review boundary

## Indexes and constraints

Adds node-key constraints for immutable/content-addressed integration records:

- `OsbMappingContextSnapshot.context_hash`
- `OsbProposalReview.proposal_hash`
- `OsbProposalReviewObject.object_key`
- `OsbProposalReviewDecision.decision_id`

These constraints make concurrent context/proposal intake idempotent at the Neo4j
boundary rather than relying on application-level `MERGE` timing.