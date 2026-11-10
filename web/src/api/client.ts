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

export const SERVER_ERROR = "Something went wrong on the server. Please try again.";
/** 5xx statuses whose `detail` the API writes for the shopper (the model provider failed,
 * the assistant is busy). Any other 5xx body may be a stack trace or a proxy's page. */
const READABLE_5XX = new Set([502, 503]);

export async function readError(response: Response): Promise<ApiError> {
  const text = await response.text().catch(() => "");
  const server = response.status >= 500;
  const fallback = server
    ? SERVER_ERROR
    : text || response.statusText || `Request failed (${response.status}).`;
  let detail = fallback;
  try {
    const body: unknown = JSON.parse(text);
    if (!server || READABLE_5XX.has(response.status)) detail = errorDetail(body, fallback);
  } catch {
    // A plain-text error: shown for a 4xx; a 5xx (e.g. a crashed worker) stays generic.
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

/** A multipart POST (e.g. a photo upload). The browser sets the multipart content type and
 * its boundary from the FormData, so no content-type header is given here. */
export function postForm<T>(path: string, form: FormData, signal?: AbortSignal): Promise<T> {
  return apiFetch<T>(path, { method: "POST", body: form, signal });
}

/** A product photo, served by the API (404 when the product has none). */
export function productImageUrl(productId: number): string {
  return `/products/${productId}/image`;
}

/** An example query photo (a garment outside the catalog) for photo search. */
export function examplePhotoUrl(photoId: number): string {
  return `/search/examples/${photoId}/image`;
}
