# 05 — API Contract Guide

## 1. General rules

This guide sets conventions for the existing FastAPI API. It does not assert that every endpoint below already exists. Inspect the current router files and publish the actual OpenAPI schema before freezing a contract.

- Prefix API routes consistently (for example `/api/v1`) only if compatible with the current app.
- Use Pydantic request/response models.
- Validate every request on the server.
- Return predictable error envelopes.
- Enforce authentication and authorization on every protected route.
- Use pagination for collection endpoints.
- Add request/correlation IDs to logs and error responses.
- Do not expose internal stack traces, absolute filesystem paths, secrets, or raw exception text.
- Do not accept arbitrary file paths from the client.
- Use idempotency keys for operations that may be retried and create jobs or exports.
- Keep API docs private or access-controlled in production where appropriate.

## 2. Error envelope

Recommended shape:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request could not be accepted.",
    "request_id": "server-generated-correlation-id",
    "details": []
  }
}
```

`details` must not reveal secrets or internal infrastructure. Validation detail may be returned when safe. Use suitable HTTP status codes:
- `400` malformed or invalid operation.
- `401` unauthenticated.
- `403` authenticated but unauthorized.
- `404` missing or intentionally undisclosed resource.
- `409` conflict / duplicate / invalid state transition.
- `413` payload too large.
- `415` unsupported media type.
- `422` schema validation if following FastAPI defaults.
- `429` rate limited.
- `500` generic unexpected error.
- `503` required dependency unavailable.

## 3. API areas to document

Map these to actual endpoints after repository inspection.

### Authentication
Login/session, logout, current user, password/session recovery if supported, and role administration.

### Media
List/search media, get asset metadata, get authorized thumbnail/preview, get authorized original when allowed, and update permitted metadata.

### Ingestion
List approved source definitions; create dry-run proposal; inspect proposal; confirm/start job; list jobs; inspect job; request cancellation; retry only retryable failures.

The local UI also supports authenticated `POST /api/ingestions/imports` multipart form uploads (`files`, repeated), protected by the session, CSRF token, and an idempotency key. It validates selected media and queues an ingestion job directly. File count, per-file bytes, aggregate bytes, media type, content validity, and free storage are checked server-side. The browser does not submit a filesystem path.

### Annotation and review
List pending suggestions; accept/reject/edit an annotation; query review history; batch actions only with explicit scope and authorization.

### Semantic search
Submit query, filters, page cursor, and optional model/index selector only if supported. Return result records the user is authorized to view.

### Video
Read video metadata and sampled frame references; access frame previews through authorization checks.

### Face enrollment
Only after approval: create enrollment proposal, validate candidate images, review ambiguous cases, confirm enrollment, perform restricted matching, and deactivate/delete enrollment.

### Operations
Health/readiness endpoints; safe job and storage status for authorized operators. Public health checks must not expose detailed configuration.

## 4. Pagination

Choose a consistent pagination method. Offset pagination may be sufficient for small administrative tables; cursor pagination is preferable for large, changing media collections. Return:
- Items.
- Next cursor or page metadata.
- Total count only when it can be computed efficiently and accurately.
- Stable sort order with a tie-breaker.

Never return the entire archive in a single response.

## 5. Media delivery

- Authorize every original, preview, and thumbnail request.
- Do not expose raw filesystem paths.
- Avoid guessable public media URLs.
- Set correct content types and safe content-disposition headers.
- Consider `X-Content-Type-Options: nosniff`.
- Apply range-request support to video only where needed and correctly authorized.
- Do not serve untrusted uploads as executable HTML or scripts from the application origin.

## 6. Long-running operations

Starting ingestion or model processing should return a job identifier and a current state, not wait for the entire job to finish inside the HTTP request.

Job status should report only persisted facts. Cancellation should be a request acknowledged by the worker at a safe checkpoint; the API must not claim instant cancellation if the underlying operation cannot stop immediately.

## 7. Contract testing

- Generate or inspect the OpenAPI document from the running FastAPI app.
- Test status codes, response schemas, authorization, validation, and error behaviour.
- Add frontend API contract tests for the service layer.
- Do not silently change field names or status enums.
- Update API documentation and tests in the same change as a breaking contract modification.
