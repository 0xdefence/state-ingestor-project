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
          expected_domain: "A whole number",
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

const known = (value: string | number) => ({
  state: "known",
  value,
  source_refs: [],
  transformation_refs: [],
  issue_refs: [],
});
export const conflictDetail = {
  ...detail,
  item: {
    ...item,
    entity_type: "product",
    business_identifier: "SKU-2004",
    reason_summaries: ["A different value is already current for this product"],
  },
  evidence: {
    nodes: [
      {
        kind: "raw_record",
        id: "raw-1",
        attributes: {
          fields: ["PRODUCT", "SKU-2004", "New Widget"],
          kind: "data",
          source_line_start: 9,
          source_line_end: 9,
        },
      },
      {
        kind: "candidate_revision",
        id: "candidate-1",
        attributes: {
          raw_record_id: "raw-1",
          entity_type: "product",
          created_at: instant,
          payload: {
            sku: known("SKU-2004"),
            name: known("New Widget"),
            stock_qty: known(12),
          },
        },
      },
      {
        kind: "candidate_revision",
        id: "prior-candidate",
        attributes: {
          raw_record_id: "raw-prior",
          entity_type: "product",
          created_at: instant,
          payload: {
            sku: known("SKU-2004"),
            name: known("Old Widget"),
            stock_qty: known(5),
          },
        },
      },
      {
        kind: "candidate_revision",
        id: "historical-candidate",
        attributes: {
          raw_record_id: "raw-older",
          entity_type: "product",
          created_at: instant,
          payload: {
            sku: known("SKU-2004"),
            name: known("Original Widget"),
            stock_qty: known(3),
          },
        },
      },
      {
        kind: "canonical_identity",
        id: "product-identity",
        attributes: {
          entity_type: "product",
          current_revision_id: "canonical-current",
        },
      },
      {
        kind: "canonical_revision",
        id: "canonical-current",
        attributes: {
          identity_id: "product-identity",
          candidate_revision_id: "prior-candidate",
          revision_number: 2,
          staged_at: instant,
        },
      },
      {
        kind: "canonical_revision",
        id: "canonical-older",
        attributes: {
          identity_id: "product-identity",
          candidate_revision_id: "historical-candidate",
          revision_number: 1,
          staged_at: instant,
        },
      },
      {
        kind: "canonical_identity",
        id: "customer-identity",
        attributes: {
          entity_type: "customer",
          current_revision_id: "customer-canonical",
        },
      },
      {
        kind: "canonical_revision",
        id: "customer-canonical",
        attributes: {
          identity_id: "customer-identity",
          candidate_revision_id: "customer-candidate",
          revision_number: 1,
          staged_at: instant,
        },
      },
      {
        kind: "candidate_revision",
        id: "customer-candidate",
        attributes: {
          entity_type: "customer",
          payload: {
            customer_id: known("CUST-1001"),
            name: known("Dependency customer"),
          },
        },
      },
    ],
  },
};
export const statusDetail = {
  ...detail,
  item: {
    ...item,
    entity_type: "product",
    business_identifier: "SKU-2004",
    reason_summaries: ["Unsupported status"],
  },
  evidence: {
    nodes: [
      {
        kind: "raw_record",
        id: "raw-1",
        attributes: { fields: ["PRODUCT", "SKU-2004", "out_of_stock"] },
      },
      {
        kind: "candidate_revision",
        id: "candidate-1",
        attributes: {
          raw_record_id: "raw-1",
          entity_type: "product",
          created_at: instant,
          payload: {
            sku: known("SKU-2004"),
            status: {
              state: "unresolved",
              value: null,
              source_refs: [{ raw_record_id: "raw-1", field_index: 2 }],
              issue_refs: ["status-issue"],
              transformation_refs: [],
            },
          },
        },
      },
      {
        kind: "data_quality_issue",
        id: "status-issue",
        attributes: {
          candidate_revision_id: "candidate-1",
          code: "INVALID_STATUS",
          field_path: "product.status",
          summary: "Unsupported status",
          expected_domain:
            "One of: in_stock, discontinued, pending_review, backordered",
          source_refs: [{ raw_record_id: "raw-1", field_index: 2 }],
        },
      },
    ],
  },
};

export const orderConflictDetail = {
  ...detail,
  item: {
    ...item,
    entity_type: "order",
    business_identifier: "ORD-3001",
    reason_summaries: ["This order has different current values"],
  },
  evidence: {
    nodes: [
      {
        kind: "raw_record",
        id: "raw-1",
        attributes: {
          fields: ["ORDER", "ORD-3001", "SKU-new", "2"],
          kind: "data",
          source_line_start: 9,
          source_line_end: 9,
        },
      },
      {
        kind: "candidate_revision",
        id: "candidate-1",
        attributes: {
          entity_type: "order",
          raw_record_id: "raw-1",
          created_at: instant,
          payload: {
            entity_type: "order",
            order_id: known("ORD-3001"),
            sku: known("SKU-new"),
            quantity: known(2),
            status: known("pending"),
          },
        },
      },
      {
        kind: "candidate_revision",
        id: "prior-order",
        attributes: {
          entity_type: "order",
          raw_record_id: "raw-old",
          created_at: instant,
          payload: {
            entity_type: "order",
            order_id: known("ORD-3001"),
            sku: known("SKU-old"),
            quantity: known(1),
            status: known("shipped"),
          },
        },
      },
      {
        kind: "canonical_identity",
        id: "order-identity",
        attributes: {
          entity_type: "order",
          current_revision_id: "order-current",
        },
      },
      {
        kind: "canonical_revision",
        id: "order-current",
        attributes: {
          identity_id: "order-identity",
          candidate_revision_id: "prior-order",
          revision_number: 1,
          staged_at: instant,
        },
      },
      {
        kind: "candidate_revision",
        id: "linked-product",
        attributes: {
          entity_type: "product",
          payload: {
            entity_type: "product",
            sku: known("SKU-new"),
            name: known("Linked product"),
            stock_qty: known(99),
          },
        },
      },
      {
        kind: "canonical_identity",
        id: "product-identity",
        attributes: {
          entity_type: "product",
          current_revision_id: "product-current",
        },
      },
      {
        kind: "canonical_revision",
        id: "product-current",
        attributes: {
          identity_id: "product-identity",
          candidate_revision_id: "linked-product",
          revision_number: 1,
          staged_at: instant,
        },
      },
    ],
  },
};
