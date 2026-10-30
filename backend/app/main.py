import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import assets, auth, jobs, ops, review, tags
from app.config import settings
from app.database import engine
from sqlalchemy import text

settings.validate()
class RequestIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


logging.getLogger().addFilter(RequestIdFilter())
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("iihs")
app = FastAPI(title="IIHS Visual Intelligence API", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    codes = {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 413: "TOO_LARGE", 415: "UNSUPPORTED_MEDIA", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 503: "UNAVAILABLE"}
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": codes.get(exc.status_code, "REQUEST_FAILED"), "message": str(exc.detail), "request_id": getattr(request.state, "request_id", None), "details": []}}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "The request could not be accepted.", "request_id": getattr(request.state, "request_id", None), "details": []}})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.exception("Unhandled request failure", extra={"request_id": getattr(request.state, "request_id", "-")})
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "The request could not be completed.", "request_id": getattr(request.state, "request_id", None), "details": []}})


app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(jobs.router)
app.include_router(review.router)
app.include_router(ops.router)
app.include_router(tags.router)


@app.get("/healthz")
def healthz():
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Required service is unavailable.") from None
    return {"status": "ready", "mode": "demo" if settings.demo_mode else "restricted"}
