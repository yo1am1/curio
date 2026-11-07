/** Every request the app makes goes through `apiFetch`, which turns API failures into
 * `ApiError`s carrying the server's explanation (FastAPI's `detail`). */

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

export const UNAVAILABLE = "The catalog service is unavailable. Please try again.";

/** The readable message of a FastAPI error body: `detail` as a string, or the first
 * validation error's message. */
export function errorDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first: unknown = detail[0];
      if (first && typeof first === "object" && "msg" in first) {
        return String(first.msg);
      }
    }
  }
  return fallback;
}

export async function readError(response: Response): Promise<ApiError> {
  const text = await response.text().catch(() => "");
  let detail = text || response.statusText || `Request failed (${response.status}).`;
  try {
    detail = errorDetail(JSON.parse(text), detail);
  } catch {
    // A plain-text error (e.g. a crashed worker): keep the text.
  }
  return new ApiError(response.status, detail);
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        accept: "application/json",
        ...(init?.headers as Record<string, string> | undefined),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, UNAVAILABLE);
  }
  if (!response.ok) throw await readError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return apiFetch<T>(path, { signal });
}

export function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}

/** A product photo, served by the API (404 when the product has none). */
export function productImageUrl(productId: number): string {
  return `/products/${productId}/image`;
}
