import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { processRun, request, uploadFile } from "../../api/client";
import type { RunDetail, UploadResult } from "../../api/contracts";
import { Status } from "../../components/Status";
export function UploadPanel() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const active = useRef(false);
  const key = useRef<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [operator, setOperator] = useState("");
  const [phase, setPhase] = useState<"idle" | "uploading" | "processing">(
    "idle",
  );
  const [error, setError] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);
  const progress = useQuery({
    queryKey: ["run", result?.run_id],
    queryFn: ({ signal }) =>
      request<RunDetail>(`/runs/${result!.run_id}`, { signal }),
    enabled: phase === "processing" && !!result,
    refetchInterval: phase === "processing" ? 1000 : false,
  });
  const reset = () => {
    key.current = null;
    setResult(null);
    setError("");
  };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (active.current) return;
    if (files.length !== 1) {
      setError("Choose one CSV file.");
      return;
    }
    if (!files[0].name.toLowerCase().endsWith(".csv")) {
      setError("Choose a file ending in .csv.");
      return;
    }
    if (!operator.trim()) {
      setError("Enter your operator name.");
      return;
    }
    active.current = true;
    setError("");
    try {
      key.current ??= crypto.randomUUID();
      setPhase(result ? "processing" : "uploading");
      const uploaded =
        result ?? (await uploadFile(files[0], operator.trim(), key.current));
      setResult(uploaded);
      await client.invalidateQueries({ queryKey: ["workspace"] });
      setPhase("processing");
      try {
        await processRun(uploaded.run_id);
      } finally {
        await client.invalidateQueries({ queryKey: ["workspace"] });
      }
      navigate(`/runs/${uploaded.run_id}`, {
        state: {
          duplicateNotice: uploaded.duplicate_upload
            ? `This exact file was already uploaded. Reused run ${uploaded.run_id}.`
            : undefined,
        },
      });
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "The request failed. Try again.",
      );
    } finally {
      active.current = false;
      setPhase("idle");
    }
  }
  return (
    <section className="panel upload-panel" aria-labelledby="upload-title">
      <div>
        <h2 id="upload-title">Upload CSV</h2>
        <p>Process a file and review the records that need attention.</p>
      </div>
      <form onSubmit={submit} noValidate>
        <fieldset disabled={phase !== "idle"}>
          <div className="upload-fields">
            <label>
              CSV file
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => {
                  reset();
                  setFiles(Array.from(e.target.files ?? []));
                }}
              />
            </label>
            <label>
              Operator name
              <input
                value={operator}
                autoComplete="name"
                onChange={(e) => {
                  reset();
                  setOperator(e.target.value);
                }}
              />
            </label>
            <button className="primary" type="submit">
              {phase === "uploading"
                ? "Uploading…"
                : phase === "processing"
                  ? "Processing…"
                  : result
                    ? "Retry processing"
                    : "Upload and process"}
            </button>
          </div>
        </fieldset>
      </form>
      {phase !== "idle" && (
        <p role="status">
          {phase === "uploading"
            ? "Uploading the source file."
            : "Processing the uploaded file."}{" "}
          {progress.data && (
            <Status
              value={progress.data.run.stage_failure ?? progress.data.run.state}
            />
          )}
        </p>
      )}
      {result?.duplicate_upload && (
        <p className="notice">
          This exact file was already uploaded. Reused run{" "}
          <Link to={`/runs/${result.run_id}`} className="identifier">
            {result.run_id}
          </Link>
          .
        </p>
      )}
      {error && (
        <div role="alert" className="error-message">
          <p>{error}</p>
          <p>
            {result
              ? "The upload is saved. Retry processing to continue this run."
              : "Check the file and operator name, then try again."}
          </p>
          {result && (
            <Link to={`/runs/${result.run_id}`}>Open uploaded run</Link>
          )}
        </div>
      )}
    </section>
  );
}
