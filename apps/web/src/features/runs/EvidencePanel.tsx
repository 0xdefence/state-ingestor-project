import type {
  AbsoluteInstant,
  EvidenceNode,
  EvidenceValue,
  EvidenceView,
} from "../../api/contracts";
import { AbsoluteTime } from "../../components/AbsoluteTime";
import { displayLabel, expectedValue } from "../../labels";
export function object(
  value: EvidenceValue | undefined,
): Record<string, EvidenceValue> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};
}
export function Value({ value }: { value: EvidenceValue | undefined }) {
  if (value === null || value === undefined)
    return <span className="muted">Not supplied</span>;
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  if (typeof value !== "object")
    return <span className="exact-value">{String(value) || "(empty)"}</span>;
  if (Array.isArray(value))
    return (
      <ul className="evidence-values">
        {value.map((v, i) => (
          <li key={i}>
            <Value value={v} />
          </li>
        ))}
      </ul>
    );
  if (
    typeof value.instant === "string" &&
    typeof value.display === "string" &&
    typeof value.timezone === "string"
  )
    return <AbsoluteTime value={value as unknown as AbsoluteInstant} />;
  return (
    <dl className="facts">
      {Object.entries(value)
        .filter(([key]) => !key.endsWith("_label"))
        .map(([key, v]) => (
          <div key={key}>
            <dt>{displayLabel(key)}</dt>
            <dd>
              {[
                "state",
                "verdict",
                "operation",
                "origin",
                "event_type",
                "stage",
                "action",
                "kind",
                "readiness",
                "entity_type",
                "code",
              ].includes(key) && typeof v === "string" ? (
                displayLabel(v)
              ) : (
                <Value value={v} />
              )}
            </dd>
          </div>
        ))}
    </dl>
  );
}
export function TechnicalEvidence({ nodes }: { nodes: EvidenceNode[] }) {
  return (
    <details>
      <summary>Technical evidence</summary>
      {nodes.map((node) => (
        <details key={`${node.kind}:${node.id}`}>
          <summary>
            {displayLabel(node.kind)} ·{" "}
            <span className="identifier">{node.id}</span>
          </summary>
          <pre>{JSON.stringify(node.attributes, null, 2)}</pre>
        </details>
      ))}
    </details>
  );
}
export function EvidencePanel({
  evidence,
  rawId,
  candidateId,
}: {
  evidence: EvidenceView;
  rawId: string;
  candidateId: string | null;
}) {
  const nodes = evidence.nodes;
  const raw = nodes.find((n) => n.kind === "raw_record" && n.id === rawId);
  const candidates = nodes.filter(
    (n) =>
      n.kind === "candidate_revision" &&
      (n.id === candidateId || n.attributes.raw_record_id === rawId),
  );
  const candidate = nodes.find((n) => n.id === candidateId);
  const related = (kind: string) => nodes.filter((n) => n.kind === kind);
  const fields = object(candidate?.attributes.payload);
  const ownIssues = related("data_quality_issue").filter((n) =>
    candidates.some((c) => c.id === n.attributes.candidate_revision_id),
  );
  const transformations = related("transformation_event")
    .filter((n) =>
      candidates.some((c) => c.id === n.attributes.candidate_revision_id),
    )
    .sort(
      (a, b) => Number(a.attributes.sequence) - Number(b.attributes.sequence),
    );
  const rawFields = Array.isArray(raw?.attributes.fields)
    ? raw.attributes.fields
    : [];
  return (
    <div className="record-evidence">
      <section>
        <h3>Source values</h3>
        <p>Exact values read from the file, in column order.</p>
        <details>
          <summary>All source fields ({rawFields.length})</summary>
          <ol className="source-fields">
            {rawFields.map((v, i) => (
              <li key={i}>
                <span className="muted">Column {i + 1}</span>
                <Value value={v} />
              </li>
            ))}
          </ol>
        </details>
      </section>
      <section>
        <h3>Interpreted values</h3>
        {candidate ? (
          <>
            <Value value={candidate.attributes.created_at} />
            <dl className="interpreted-fields">
              {Object.entries(fields)
                .filter(([key]) => key !== "entity_type")
                .map(([key, v]) => {
                  const field = object(v);
                  const refs = Array.isArray(field.source_refs)
                    ? field.source_refs
                    : [];
                  return (
                    <div key={key}>
                      <dt>{displayLabel(key)}</dt>
                      <dd>
                        {typeof field.state === "string" ? (
                          <>
                            <span>{displayLabel(field.state)}</span>
                            {field.state === "known" && (
                              <Value value={field.value} />
                            )}
                            <div className="source-comparison">
                              {refs.map((ref, i) => {
                                const r = object(ref);
                                const source = nodes.find(
                                  (n) =>
                                    n.kind === "raw_record" &&
                                    n.id === r.raw_record_id,
                                );
                                const values = source?.attributes.fields;
                                return Array.isArray(values) &&
                                  typeof r.field_index === "number" ? (
                                  <div key={i}>
                                    <span className="muted">
                                      Exact source value
                                    </span>
                                    <Value value={values[r.field_index]} />
                                  </div>
                                ) : null;
                              })}
                            </div>
                          </>
                        ) : (
                          <Value value={v} />
                        )}
                      </dd>
                    </div>
                  );
                })}
            </dl>
          </>
        ) : (
          <p>No interpreted values are available yet.</p>
        )}
      </section>
      <section>
        <h3>Registered issues</h3>
        {ownIssues.length ? (
          ownIssues.map((n) => (
            <div className="evidence-entry" key={n.id}>
              <strong>{displayLabel(String(n.attributes.code))}</strong>
              <p>{String(n.attributes.summary)}</p>
              <p>Field: {displayLabel(String(n.attributes.field_path))}</p>
              {expectedValue[String(n.attributes.code)] && (
                <p>
                  Expected value: {expectedValue[String(n.attributes.code)]}
                </p>
              )}
              {n.attributes.tentative_cause && (
                <p>Possible cause: {String(n.attributes.tentative_cause)}</p>
              )}
            </div>
          ))
        ) : (
          <p>No registered issues for this record.</p>
        )}
      </section>
      <details>
        <summary>Transformations ({transformations.length})</summary>
        {transformations.map((n) => (
          <div className="evidence-entry" key={n.id}>
            <h3>
              {displayLabel(String(n.attributes.operation))} ·{" "}
              {displayLabel(String(n.attributes.field_path))}
            </h3>
            <dl className="facts">
              <div>
                <dt>Before</dt>
                <dd>
                  <Value value={n.attributes.before} />
                </dd>
              </div>
              <div>
                <dt>After</dt>
                <dd>
                  <Value value={n.attributes.after} />
                </dd>
              </div>
            </dl>
          </div>
        ))}
      </details>
      <section>
        <h3>Dependencies and relationships</h3>
        {related("dependency_record").length ? (
          related("dependency_record").map((n) => (
            <div className="evidence-entry" key={n.id}>
              <strong>{String(n.attributes.referenced_business_value)}</strong>
              <p>
                {displayLabel(String(n.attributes.kind))} ·{" "}
                {displayLabel(String(n.attributes.state))}
              </p>
              <details>
                <summary>Dependency references</summary>
                <Value value={n.attributes} />
              </details>
            </div>
          ))
        ) : (
          <p>No dependencies recorded.</p>
        )}
        {related("duplicate_relation").map((n) => (
          <div className="evidence-entry" key={n.id}>
            <p>Duplicate source relationship</p>
            <Value value={n.attributes} />
          </div>
        ))}
      </section>
      <section>
        <h3>Canonical lineage</h3>
        {related("canonical_revision").length ? (
          <>
            <p>
              Linked canonical revisions and their current position. Dependency
              revisions may also appear here.
            </p>
            {related("canonical_revision").map((n) => {
              const identity = nodes.find(
                (i) =>
                  i.kind === "canonical_identity" &&
                  i.id === n.attributes.identity_id,
              );
              return (
                <div className="evidence-entry" key={n.id}>
                  <strong>
                    Revision {String(n.attributes.revision_number)} ·{" "}
                    {identity?.attributes.current_revision_id === n.id
                      ? "Current"
                      : "Historical"}
                  </strong>
                  <p className="identifier">{n.id}</p>
                  <p>
                    {n.attributes.candidate_revision_id === candidateId
                      ? "From this candidate"
                      : "From a linked candidate"}
                  </p>
                  <Value value={n.attributes.staged_at} />
                </div>
              );
            })}
            {related("canonical_promotion_event").map((n) => (
              <details key={n.id}>
                <summary>
                  {displayLabel(String(n.attributes.action))} · Promotion
                  history
                </summary>
                <Value value={n.attributes} />
              </details>
            ))}
          </>
        ) : (
          <p>No canonical revision is linked to this evidence.</p>
        )}
      </section>
      <details>
        <summary>Candidate revisions ({candidates.length})</summary>
        {candidates.map((n) => (
          <div className="evidence-entry" key={n.id}>
            <p className="identifier">{n.id}</p>
            <Value value={n.attributes} />
          </div>
        ))}
      </details>
      <TechnicalEvidence nodes={nodes} />
    </div>
  );
}

export function recordEvidence(
  evidence: EvidenceView,
  rawId: string,
): EvidenceView {
  const selected = new Set<string>([rawId]);
  const references = (value: EvidenceValue): string[] =>
    typeof value === "string"
      ? [value]
      : Array.isArray(value)
        ? value.flatMap(references)
        : value && typeof value === "object"
          ? Object.values(value).flatMap(references)
          : [];
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of evidence.nodes) {
      const owners = [
        "raw_record_id",
        "candidate_revision_id",
        "classification_id",
        "review_item_id",
        "canonical_revision_id",
      ];
      if (
        selected.has(node.id) ||
        owners.some(
          (key) =>
            typeof node.attributes[key] === "string" &&
            selected.has(String(node.attributes[key])),
        )
      ) {
        if (!selected.has(node.id)) {
          selected.add(node.id);
          changed = true;
        }
        for (const ref of references(node.attributes)) {
          if (evidence.nodes.some((n) => n.id === ref) && !selected.has(ref)) {
            selected.add(ref);
            changed = true;
          }
        }
      }
    }
  }
  return { nodes: evidence.nodes.filter((n) => selected.has(n.id)) };
}
