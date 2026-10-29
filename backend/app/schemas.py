from pydantic import BaseModel, Field
from typing import Literal


class IngestPreviewRequest(BaseModel):
    source_kind: Literal["directory", "upload_batch", "people_directory"]
    source_ref: str = Field(min_length=1, max_length=2048)
    recursive: bool = True
    extract_metadata: bool = True
    generate_thumbnails: bool = True
    suggest_tags: bool = False
    dry_run: bool = True


class TagCreate(BaseModel):
    label: str = Field(min_length=1, max_length=180)
    source: Literal["manual", "ai"] = "manual"
    confidence: float | None = Field(default=None, ge=0, le=1)


class EmployeeProfileCreate(BaseModel):
    employee_code: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=250)
    department: str | None = None
    profile_image_path: str = Field(min_length=1, max_length=2048)
    consent_recorded: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=20, ge=1, le=100)
