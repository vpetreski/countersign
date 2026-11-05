// A thin fetch wrapper for the same-origin API. Errors are RFC 9457 problem+json and surface as ProblemError.

export interface FieldProblem {
  field: string;
  problem: string;
}

export class ProblemError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;
  readonly code: string;
  readonly errors: FieldProblem[];
  readonly body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    const title = typeof body.title === "string" ? body.title : `HTTP ${status}`;
    const detail = typeof body.detail === "string" ? body.detail : title;
    super(detail);
    this.name = "ProblemError";
    this.status = status;
    this.title = title;
    this.detail = detail;
    this.code = problemCode(body);
    this.errors = Array.isArray(body.errors) ? (body.errors as FieldProblem[]) : [];
    this.body = body;
  }
}

/** The machine-readable code of a problem: the last path segment of its `type` URI. */
export function problemCode(body: Record<string, unknown>): string {
  const type = typeof body.type === "string" ? body.type : "";
  const last = type.split("/").filter(Boolean).pop();
  return last && type.includes("/") ? last : "error";
}

export function isProblem(err: unknown, status?: number): err is ProblemError {
  return err instanceof ProblemError && (status === undefined || err.status === status);
}

export interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  token?: string;
  /** Send a fresh Idempotency-Key with this request. */
  idempotent?: boolean;
  /** Send this Idempotency-Key: one per user intent, so a retry of the same intent replays instead of repeating. */
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json, application/problem+json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  const key = opts.idempotencyKey ?? (opts.idempotent ? newIdempotencyKey() : undefined);
  if (key) headers["Idempotency-Key"] = key;
  let response: Response;
  try {
    response = await fetch(path, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ProblemError(0, {
      title: "Network error",
      detail: "The API could not be reached. Check that the backend is running.",
      type: "about:blank/network",
    });
  }
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { title: `HTTP ${response.status}`, detail: text.slice(0, 300) };
    }
  }
  if (!response.ok) {
    throw new ProblemError(response.status, (data as Record<string, unknown>) ?? {});
  }
  return data as T;
}
