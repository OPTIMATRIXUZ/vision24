import { describe, expect, it } from "vitest";

import { ApiError, toApiError } from "@/lib/api-error";

function res(body: unknown, status = 400, headers: Record<string, string> = {}): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(text, { status, headers });
}

describe("toApiError", () => {
  it("reads the standard envelope", async () => {
    const err = await toApiError(
      res(
        {
          error: {
            code: "job_busy",
            message: "This source already has a queued or running job.",
            request_id: "abc123",
            details: null,
          },
          detail: "This source already has a queued or running job.",
        },
        409,
      ),
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("job_busy");
    expect(err.requestId).toBe("abc123");
    expect(err.message).toBe("This source already has a queued or running job.");
  });

  it("reads FastAPI's plain {detail} shape", async () => {
    const err = await toApiError(res({ detail: "Invalid or missing bearer token" }, 401));
    expect(err.message).toBe("Invalid or missing bearer token");
    expect(err.status).toBe(401);
  });

  it("flattens 422 validation lists into a readable sentence", async () => {
    const err = await toApiError(
      res({ detail: [{ loc: ["query", "count"], msg: "field required" }] }, 422),
    );
    expect(err.message).toBe("field required");
  });

  it("falls back to raw text when the body is not JSON", async () => {
    const err = await toApiError(res("<html>502 Bad Gateway</html>", 502));
    expect(err.message).toContain("502 Bad Gateway");
  });

  it("falls back to a generic message on an empty body", async () => {
    const err = await toApiError(res("", 500));
    expect(err.message).toBe("Request failed (500)");
  });

  it("takes the request id from the header when the body omits it", async () => {
    const err = await toApiError(res({ detail: "nope" }, 404, { "X-Request-ID": "from-header" }));
    expect(err.requestId).toBe("from-header");
  });

  it("flags auth failures so callers can redirect rather than toast", async () => {
    expect((await toApiError(res({ detail: "x" }, 401))).isAuth).toBe(true);
    expect((await toApiError(res({ detail: "x" }, 403))).isAuth).toBe(true);
    expect((await toApiError(res({ detail: "x" }, 500))).isAuth).toBe(false);
  });

  it("never renders raw JSON to the user", async () => {
    const err = await toApiError(
      res({ error: { code: "not_found", message: "No such camera." } }, 404),
    );
    expect(err.message).not.toContain("{");
    expect(String(err)).toContain("No such camera.");
  });
});
