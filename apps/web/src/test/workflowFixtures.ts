import { run, review } from "./fixtures";
export const instant = run.processed_at;
export const item = {
  ...review,
  business_identifier: "ORD-3001",
  entity_type: "ORDER",
};
export const detail = {
  item,
  allowed_outcomes: ["approve", "reject"],
  decisions: [],
  evidence: {
    nodes: [
      {
        kind: "raw_record",
        id: "raw-1",
        attributes: {
          fields: ["ORDER", "ORD-3001", "many"],
          source_line_start: 9,
          source_line_end: 9,
          kind: "data",
        },
      },
      {
        kind: "candidate_revision",
        id: "candidate-1",
        attributes: {
          revision_number: 1,
          created_at: instant,
          payload: {
            quantity: {
              state: "unresolved",
              value: null,
              source_refs: [{ raw_record_id: "raw-1", field_index: 2 }],
              transformation_refs: [],
              issue_refs: ["issue-1"],
            },
          },
        },
      },
      {
        kind: "data_quality_issue",
        id: "issue-1",
        attributes: {
          candidate_revision_id: "candidate-1",
          code: "INVALID_INTEGER",
          field_path: "quantity",
          summary: "A number was expected for quantity",
          source_refs: [{ raw_record_id: "raw-1", field_index: 2 }],
          tentative_cause: null,
        },
      },
      {
        kind: "transformation_event",
        id: "transform-1",
        attributes: {
          operation: "TRIM",
          field_path: "order_id",
          before: " ORD-3001 ",
          after: "ORD-3001",
          sequence: 1,
          candidate_revision_id: "candidate-1",
        },
      },
      {
        kind: "dependency_record",
        id: "dep-1",
        attributes: {
          kind: "customer",
          referenced_business_value: "CUS-1001",
          state: "resolved",
          resolved_entity_id: "identity-1",
        },
      },
      {
        kind: "canonical_identity",
        id: "identity-1",
        attributes: { current_revision_id: "canonical-1" },
      },
      {
        kind: "canonical_revision",
        id: "canonical-1",
        attributes: {
          identity_id: "identity-1",
          revision_number: 1,
          candidate_revision_id: "prior-candidate",
          staged_at: instant,
        },
      },
      {
        kind: "canonical_promotion_event",
        id: "promotion-1",
        attributes: {
          action: "activate",
          canonical_revision_id: "canonical-1",
          prior_current_revision_id: null,
          occurred_at: instant,
        },
      },
    ],
  },
};
export const runDetail = {
  run,
  occurrences: [
    {
      id: "occ-1",
      filename: run.filename,
      actor_label: "Alex",
      ingested_at: run.created_at,
      run_links: [],
    },
    {
      id: "occ-2",
      filename: "submitted-again.csv",
      actor_label: "Sam",
      ingested_at: instant,
      run_links: [],
    },
  ],
  records: [
    {
      id: "raw-1",
      kind: "data",
      source_line_start: 9,
      source_line_end: 9,
      candidate_revision_id: "candidate-1",
      classification_id: "classification-1",
      business_identifier: "ORD-3001",
      verdict: "NEEDS_REVIEW",
      review_item_id: "review-1",
    },
    {
      id: "raw-clean",
      kind: "data",
      source_line_start: 10,
      source_line_end: 10,
      candidate_revision_id: "candidate-clean",
      classification_id: "classification-clean",
      business_identifier: "SKU-2001",
      verdict: "CLEAN",
      review_item_id: null,
    },
  ],
  evidence: {
    nodes: [
      ...detail.evidence.nodes,
      {
        kind: "raw_record",
        id: "raw-clean",
        attributes: {
          fields: ["PRODUCT", "SKU-2001", "Clean source"],
          kind: "data",
          source_line_start: 10,
          source_line_end: 10,
        },
      },
      {
        kind: "candidate_revision",
        id: "candidate-clean",
        attributes: {
          raw_record_id: "raw-clean",
          revision_number: 1,
          created_at: instant,
          payload: {
            sku: {
              state: "known",
              value: "SKU-2001",
              source_refs: [{ raw_record_id: "raw-clean", field_index: 1 }],
              transformation_refs: [],
              issue_refs: [],
            },
          },
        },
      },
      {
        kind: "classification_result",
        id: "classification-clean",
        attributes: {
          candidate_revision_id: "candidate-clean",
          verdict: "CLEAN",
          readiness: "eligible",
          evaluated_at: instant,
        },
      },
    ],
  },
  checkpoints: [{ stage: "parse", record_count: 18, completed_at: instant }],
  events: ["parse", "normalise", "classify", "load"].map((stage) => ({
    id: stage,
    stage,
    event_type: "stage_completed",
    occurred_at: instant,
    facts: { record_count: 18 },
  })),
};
