import type { ProcessResult, UploadResult } from "./contracts";
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(
      "Cannot reach the local API. Check that it is running, then try again.",
      0,
      "network_error",
    );
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: {
        message?: string;
        code?: string;
        details?: Record<string, unknown>;
      };
    } | null;
    throw new ApiError(
      body?.error?.message ?? `Request failed (${response.status}). Try again.`,
      response.status,
      body?.error?.code ?? "http_error",
      body?.error?.details,
    );
  }
  return response.json() as Promise<T>;
}
export function uploadFile(
  file: File,
  operatorName: string,
  idempotencyKey: string,
) {
  const body = new FormData();
  body.append("file", file);
  body.append("operator_name", operatorName);
  body.append("idempotency_key", idempotencyKey);
  return request<UploadResult>("/uploads", { method: "POST", body });
}
export function processRun(runId: string) {
  return request<ProcessResult>(`/runs/${encodeURIComponent(runId)}/process`, {
    method: "POST",
  });
}
