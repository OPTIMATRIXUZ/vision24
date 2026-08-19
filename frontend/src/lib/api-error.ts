export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly details?: unknown;

  constructor(
    status: number,
    message: string,
    opts: { code?: string; requestId?: string; details?: unknown } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = opts.code ?? "error";
    this.requestId = opts.requestId;
    this.details = opts.details;
  }

  get isAuth(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

type Envelope = {
  error?: { code?: string; message?: string; request_id?: string; details?: unknown };
  detail?: unknown;
};

function messageFrom(body: Envelope, fallback: string): string {
  if (body.error?.message) return body.error.message;

  if (Array.isArray(body.detail)) {
    const parts = body.detail
      .map((d) => (typeof d === "object" && d && "msg" in d ? String(d.msg) : ""))
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }

  if (typeof body.detail === "string" && body.detail) return body.detail;
  return fallback;
}

export async function toApiError(res: Response): Promise<ApiError> {
  const fallback = `Request failed (${res.status})`;
  let raw = "";
  try {
    raw = await res.text();
  } catch {
    return new ApiError(res.status, fallback);
  }

  let body: Envelope;
  try {
    body = JSON.parse(raw) as Envelope;
  } catch {
    return new ApiError(res.status, raw.trim().slice(0, 200) || fallback);
  }

  return new ApiError(res.status, messageFrom(body, fallback), {
    code: body.error?.code,
    requestId: body.error?.request_id ?? res.headers.get("X-Request-ID") ?? undefined,
    details: body.error?.details,
  });
}
