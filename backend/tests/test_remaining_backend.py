from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import time
import uuid
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models, worker
from app.api import assets as assets_api
from app.config import settings
from app.database import Base
from app.services import contextual_metadata as context
from app.services import crawler, document_readers, embeddings, hybrid_search, media, search_segments
import local_ml_service


def _tiny_png_bytes(color: tuple[int, int, int] = (40, 120, 200)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (64, 64), color=color)
    for x in range(16, 48):
        for y in range(16, 48):
            img.putpixel((x, y), (240, 180, 50))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_pdf_file(path: Path, pages: list[str]) -> None:
    writer = PdfWriter()
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    for page_text in pages:
        page = writer.add_blank_page(width=612, height=792)
        font_dict = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        resources = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})
        })
        page[NameObject("/Resources")] = resources
        escaped = page_text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)


def _create_docx_file(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


def _create_odt_file(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>{body}</office:text></office:body></office:document-content>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", xml)


def _create_xlsx_file(path: Path, sheet_name: str, rows: list[list[str]]) -> None:
    shared_strings: list[str] = []
    sheet_rows_xml: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells_xml: list[str] = []
        for c_idx, val in enumerate(row):
            col_letter = chr(ord("A") + c_idx)
            s_idx = len(shared_strings)
            shared_strings.append(val)
            cells_xml.append(f'<c r="{col_letter}{r_idx}" t="s"><v>{s_idx}</v></c>')
        sheet_rows_xml.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')

    sst_items = "".join(f"<si><t>{s}</t></si>" for s in shared_strings)
    sst_xml = f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{sst_items}</sst>'
    wb_xml = f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets></workbook>'
    ws_xml = f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(sheet_rows_xml)}</sheetData></worksheet>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", sst_xml)
        archive.writestr("xl/workbook.xml", wb_xml)
        archive.writestr("xl/worksheets/sheet1.xml", ws_xml)


def _create_ods_file(path: Path, sheet_name: str, rows: list[list[str]]) -> None:
    rows_xml = []
    for row in rows:
        cells = "".join(f"<table:table-cell><text:p>{cell}</text:p></table:table-cell>" for cell in row)
        rows_xml.append(f"<table:table-row>{cells}</table:table-row>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f'<office:body><office:spreadsheet><table:table table:name="{sheet_name}">{"".join(rows_xml)}</table:table></office:spreadsheet></office:body></office:document-content>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", xml)


def _create_pptx_file(path: Path, slides: list[tuple[str, str]]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for idx, (slide_text, note_text) in enumerate(slides, start=1):
            s_xml = f'<?xml version="1.0" encoding="UTF-8"?><p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{slide_text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
            archive.writestr(f"ppt/slides/slide{idx}.xml", s_xml)
            if note_text:
                n_xml = f'<?xml version="1.0" encoding="UTF-8"?><p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{note_text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>'
                archive.writestr(f"ppt/notesSlides/notesSlide{idx}.xml", n_xml)


def _create_odp_file(path: Path, slides: list[str]) -> None:
    pages = "".join(f'<draw:page draw:name="Slide {idx}"><draw:frame><draw:text-box><text:p>{text}</text:p></draw:text-box></draw:frame></draw:page>' for idx, text in enumerate(slides, start=1))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:presentation>{pages}</office:presentation></office:body></office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content.xml", xml)


# ---------------------------------------------------------------------------
# 1. Contextual metadata, aliases, temporal extraction & recursive crawler
# ---------------------------------------------------------------------------
def test_contextual_metadata_aliases_dates_and_parent_inheritance():
    context.load_vocabulary.cache_clear()
    parent_ctx = context.infer_path_context("2023-08-14_Housing_Workshop/SES/UFP_Cohort_8/Word_Labs")
    child_ctx = context.infer_path_context(
        "2023-08-14_Housing_Workshop/SES/UFP_Cohort_8/Word_Labs/City_Campus_Session_01.jpg",
        parent_context=parent_ctx,
    )
    matched_kw = {item["keyword"] for item in child_ctx["matched_keywords"]}
    assert "Word Lab" in matched_kw
    assert "Urban Fellows Programme" in matched_kw
    assert "School of Environment and Sustainability" in matched_kw
    assert "IIHS Bengaluru City Campus" in matched_kw
    assert 2023 in child_ctx["extracted_years"]
    assert "2023-08-14" in child_ctx["extracted_dates"]
    # Folder/filename context must never be marked as a visual claim
    assert all(item.get("is_visual_claim") is False for item in child_ctx["matched_keywords"])
    assert child_ctx["is_visual_claim"] is False
    assert "Word Lab" in child_ctx["facets"]["labs"]
    assert "Urban Fellows Programme" in child_ctx["facets"]["programmes"]
    assert "School of Environment and Sustainability" in child_ctx["facets"]["departments_and_schools"]


def test_crawler_hierarchy_resumable_and_excluded_directories(tmp_path):
    root = tmp_path / "IIHS_HDD"
    (root / "$RECYCLE.BIN").mkdir(parents=True)
    (root / "$RECYCLE.BIN" / "deleted.jpg").write_bytes(_tiny_png_bytes())
    (root / "Media_Lab" / "2023_Urban_Housing").mkdir(parents=True)
    (root / "Media_Lab" / "2023_Urban_Housing" / "photo1.png").write_bytes(_tiny_png_bytes())
    (root / "Media_Lab" / "2023_Urban_Housing" / "report.pdf").write_bytes(b"%PDF-1.4 dummy")
    (root / "Media_Lab" / "2023_Urban_Housing" / "interview.mp3").write_bytes(b"ID3 dummy")

    result = crawler.crawl_archive_root(root)
    keys = [c.relative_key for c in result.items]
    assert len(keys) == 3
    assert not any("$RECYCLE.BIN" in k for k in keys)
    # Related files in the same folder share related_group_key and sibling references
    first = result.items[0]
    assert first.context["related_group_key"]
    assert first.context["related_files_count"] == 2
    assert len(first.context["related_files_sample"]) == 2
    assert any(k["keyword"] == "Media Lab" for k in first.context["matched_keywords"])

    # Resumable scan after first key
    resumed = crawler.crawl_archive_root(root, resume_after_key=keys[0])
    assert [c.relative_key for c in resumed.items] == keys[1:]


# ---------------------------------------------------------------------------
# 2. All 11 supported document formats & structured extraction
# ---------------------------------------------------------------------------
def test_all_eleven_document_formats_extract_structured_segments(tmp_path):
    # 1. PDF
    pdf_path = tmp_path / "annual_report.pdf"
    _create_pdf_file(pdf_path, ["IIHS Annual Report 2023 Page One Urban Fellows", "Page Two Climate Resilience Budget"])
    pdf_segs = document_readers.extract_document_text(pdf_path)["segments"]
    assert len(pdf_segs) >= 2
    assert pdf_segs[0]["page_number"] == 1
    assert pdf_segs[0]["segment_type"] == "pdf_page"
    assert "Urban Fellows" in pdf_segs[0]["text"]

    # 2. DOCX
    docx_path = tmp_path / "notes.docx"
    _create_docx_file(docx_path, ["Media Lab field notes from Kengeri campus", "Second paragraph on sustainable housing"])
    docx_segs = document_readers.extract_document_text(docx_path)["segments"]
    assert docx_segs and "Kengeri campus" in docx_segs[0]["text"]

    # 3. ODT
    odt_path = tmp_path / "brief.odt"
    _create_odt_file(odt_path, ["OpenDocument policy brief on water sanitation"])
    odt_segs = document_readers.extract_document_text(odt_path)["segments"]
    assert odt_segs and "water sanitation" in odt_segs[0]["text"]

    # 4. RTF
    rtf_path = tmp_path / "memo.rtf"
    rtf_path.write_text(r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Times New Roman;}} \f0\fs24 Word Labs poetry workshop archive memo.}", encoding="utf-8")
    rtf_segs = document_readers.extract_document_text(rtf_path)["segments"]
    assert rtf_segs and "Word Labs poetry workshop" in rtf_segs[0]["text"]

    # 5. HTML
    html_path = tmp_path / "event.html"
    html_path.write_text("<html><head><title>Ignored</title><style>body{color:red}</style></head><body><h1>Urban ARC 2023</h1><p>Keynote panel discussion</p></body></html>", encoding="utf-8")
    html_segs = document_readers.extract_document_text(html_path)["segments"]
    assert html_segs and "Urban ARC 2023" in html_segs[0]["text"]

    # 6. TXT
    txt_path = tmp_path / "readme.txt"
    txt_path.write_text("Plain text archive manifest for School of Environment and Sustainability.", encoding="utf-8")
    txt_segs = document_readers.extract_document_text(txt_path)["segments"]
    assert txt_segs and "School of Environment and Sustainability" in txt_segs[0]["text"]

    # 7. MD
    md_path = tmp_path / "summary.md"
    md_path.write_text("# Housing Workshop 2023\n\nSummary of field interviews and budget allocations.", encoding="utf-8")
    md_segs = document_readers.extract_document_text(md_path)["segments"]
    assert md_segs and "Housing Workshop 2023" in md_segs[0]["text"]

    # 8. XLSX
    xlsx_path = tmp_path / "budget_spreadsheet.xlsx"
    _create_xlsx_file(xlsx_path, "Budget_2023", [["Programme", "Amount"], ["Urban Fellows Programme", "1500000"]])
    xlsx_segs = document_readers.extract_document_text(xlsx_path)["segments"]
    assert xlsx_segs and xlsx_segs[0]["segment_type"] == "spreadsheet_sheet"
    assert "Budget_2023" in xlsx_segs[0]["title"]
    assert "Urban Fellows Programme" in xlsx_segs[0]["text"]

    # 9. ODS
    ods_path = tmp_path / "finances.ods"
    _create_ods_file(ods_path, "Grants_Q3", [["Grant", "Status"], ["Climate Resilience", "Approved"]])
    ods_segs = document_readers.extract_document_text(ods_path)["segments"]
    assert ods_segs and "Grants_Q3" in ods_segs[0]["title"]
    assert "Climate Resilience" in ods_segs[0]["text"]

    # 10. PPTX
    pptx_path = tmp_path / "deck.pptx"
    _create_pptx_file(pptx_path, [("City Campus Masterplan", "Speaker notes on solar microgrid")])
    pptx_segs = document_readers.extract_document_text(pptx_path)["segments"]
    assert pptx_segs and pptx_segs[0]["segment_type"] == "presentation_slide"
    assert pptx_segs[0]["page_number"] == 1
    assert "solar microgrid" in pptx_segs[0]["text"]

    # 11. ODP
    odp_path = tmp_path / "slides.odp"
    _create_odp_file(odp_path, ["Urban Practitioners Programme Overview"])
    odp_segs = document_readers.extract_document_text(odp_path)["segments"]
    assert odp_segs and odp_segs[0]["page_number"] == 1
    assert "Urban Practitioners Programme" in odp_segs[0]["text"]


# ---------------------------------------------------------------------------
# 3, 4, 5, 6, 8. End-to-End Multimodal Ingestion, Persistent Segments,
# Hybrid Search (Section 7 queries), Incremental Sync & Telemetry
# ---------------------------------------------------------------------------
def test_end_to_end_multimodal_ingestion_hybrid_search_and_incremental_sync(tmp_path, monkeypatch):
    db_file = tmp_path / "archive_e2e.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    source_root = tmp_path / "hdd_root"
    storage_root = tmp_path / "storage_root"
    source_root.mkdir(parents=True)
    storage_root.mkdir(parents=True)

    custom_settings = replace(
        settings,
        demo_mode=False,
        approved_source_roots=(source_root,),
        storage_root=storage_root,
        copy_source_originals=False,
        min_storage_free_bytes=0,
        local_ml_token="JYi19tf-zHWpHGTk6nbBXHVQXvnoYQWIAe98NbR2g1w",
    )
    monkeypatch.setattr(worker, "settings", custom_settings)
    monkeypatch.setattr(media, "settings", custom_settings)
    monkeypatch.setattr(hybrid_search, "settings", custom_settings)
    monkeypatch.setattr(worker, "SessionLocal", TestingSession)

    def fake_text_embeddings(texts: list[str]):
        return [
            (local_ml_service._portable_text_vector(t), custom_settings.embedding_model, custom_settings.embedding_revision)
            for t in texts
        ]

    def fake_text_embedding(text: str):
        return fake_text_embeddings([text])[0]

    def fake_image_embedding(path: Path):
        return (local_ml_service._portable_image_vector(path), custom_settings.embedding_model, custom_settings.embedding_revision)

    def fake_transcribe_audio(content: bytes):
        return {
            "text": "Field interview audio discussing affordable urban housing and climate resilience in Bengaluru.",
            "language": "en",
            "english_translation": "Field interview audio discussing affordable urban housing and climate resilience in Bengaluru.",
            "segments": [
                {"start": 0.0, "end": 18.5, "text": "Field interview audio discussing affordable urban housing"},
                {"start": 18.5, "end": 42.0, "text": "and climate resilience panel discussion in Bengaluru."},
            ],
            "model_id": "openai-whisper/small",
            "model_revision": "9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794",
        }

    def fake_inspect_media(path: Path):
        suffix = path.suffix.lower()
        size = int(path.stat().st_size)
        if suffix in {".png", ".jpg", ".jpeg"}:
            return {"media_type": "image", "mime": "image/png", "size": size, "width": 64, "height": 64, "duration_ms": None, "exif": {}}
        if suffix in {".mp4", ".mov"}:
            return {"media_type": "video", "mime": "video/mp4", "size": size, "width": 1920, "height": 1080, "duration_ms": 14000, "exif": {}, "codec": "h264"}
        if suffix in {".mp3", ".wav"}:
            return {"media_type": "audio", "mime": "audio/mpeg", "size": size, "width": None, "height": None, "duration_ms": 42000, "exif": {}, "codec": "mp3"}
        return {"media_type": "document", "mime": "application/pdf", "size": size, "width": None, "height": None, "duration_ms": None, "exif": {"document_format": suffix.lstrip(".")}}

    def fake_video_frame(source_path: Path, timestamp_ms: int, key: str):
        out_path = storage_root / "derivatives" / key
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Timestamps 0 and 5000 are identical (to test dHash deduplication!), timestamp 10000 is distinct
        if timestamp_ms in {0, 5000}:
            out_path.write_bytes(_tiny_png_bytes((20, 40, 60)))
        else:
            img3 = Image.new("RGB", (64, 64), color=(200, 30, 30))
            for x in range(0, 32):
                for y in range(0, 64):
                    img3.putpixel((x, y), (10, 220, 40))
            img3.save(out_path, format="PNG")
        return out_path

    def fake_extract_audio(source_path: Path, duration_ms: int = 0):
        return b"FAKE_MP3_AUDIO_BYTES"

    monkeypatch.setattr(worker, "text_embeddings", fake_text_embeddings)
    monkeypatch.setattr(worker, "image_embedding", fake_image_embedding)
    monkeypatch.setattr(worker, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(worker, "inspect_media", fake_inspect_media)
    monkeypatch.setattr(worker, "video_frame", fake_video_frame)
    monkeypatch.setattr(worker, "extract_audio", fake_extract_audio)
    monkeypatch.setattr(hybrid_search, "text_embedding", fake_text_embedding)

    # Create representative archive hierarchy on simulated HDD
    (source_root / "Word_Labs" / "2023_Housing_Workshop").mkdir(parents=True)
    img_file = source_root / "Word_Labs" / "2023_Housing_Workshop" / "City_Campus_Banner.png"
    img_file.write_bytes(_tiny_png_bytes())

    (source_root / "Media_Lab" / "SES_UFP_2023").mkdir(parents=True)
    vid_file = source_root / "Media_Lab" / "SES_UFP_2023" / "panel_discussion_urban_housing.mp4"
    vid_file.write_bytes(b"FAKE_MP4_VIDEO_PAYLOAD_001")

    aud_file = source_root / "Media_Lab" / "SES_UFP_2023" / "field_interview_audio.mp3"
    aud_file.write_bytes(b"FAKE_MP3_AUDIO_PAYLOAD_001")

    (source_root / "Documents" / "2023_Reports").mkdir(parents=True)
    pdf_file = source_root / "Documents" / "2023_Reports" / "annual_report_2023.pdf"
    _create_pdf_file(
        pdf_file,
        [
            "IIHS Annual Report 2023 covering Urban Fellows Programme and City Campus.",
            "School of Environment and Sustainability climate resilience research outcomes.",
        ],
    )
    xlsx_file = source_root / "Documents" / "2023_Reports" / "budget_spreadsheet_2023.xlsx"
    _create_xlsx_file(
        xlsx_file,
        "Annual_Budget",
        [
            ["Department", "Allocation", "Notes"],
            ["Media Lab", "2500000", "Equipment and archival storage"],
            ["Word Lab", "1200000", "Housing workshop 2023 publication"],
        ],
    )

    source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"iihs-source:{source_root.resolve()}"))
    with TestingSession() as db:
        admin = models.User(
            username="admin.user",
            role="admin",
            password_hash="x",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        src_def = models.SourceDefinition(
            id=source_id,
            name="External HDD Archive",
            root_path=str(source_root.resolve()),
            enabled=True,
        )
        db.add(src_def)
        db.commit()

        preview_job = models.IngestionJob(
            source_id=source_id,
            requested_by=admin.id,
            kind="preview",
            state="queued",
            max_files=0,
        )
        db.add(preview_job)
        db.commit()
        job_id = preview_job.id
        admin_id = admin.id

    # Step 1: Run preview scan
    assert worker.run_once() is True

    with TestingSession() as db:
        job = db.get(models.IngestionJob, job_id)
        assert job.state == "preview_ready"
        assert job.discovered_files == 5
        # Promote preview job to ingest job
        job.kind = "ingest"
        job.state = "queued"
        db.commit()

    # Step 2: Run full multimodal ingestion
    assert worker.run_once() is True

    with TestingSession() as db:
        job = db.get(models.IngestionJob, job_id)
        assert job.state == "completed"
        assert job.processed_files == 5
        assert job.failed_files == 0
        telemetry = worker.compute_job_telemetry(job)
        assert telemetry["processed_bytes"] > 0

        # Verify all 4 modalities were created
        assets = db.scalars(select(models.MediaAsset)).all()
        modalities = {a.media_type for a in assets}
        assert modalities == {"image", "video", "audio", "document"}

        # Verify persistent SearchSegment rows exist with page numbers, timestamps, and deduplicated video frames
        segments = db.scalars(select(models.SearchSegment)).all()
        seg_types = {s.segment_type for s in segments}
        assert "image_visual" in seg_types
        assert "video_frame" in seg_types
        assert "audio_transcript_window" in seg_types
        assert "pdf_page" in seg_types
        assert "spreadsheet_sheet" in seg_types
        assert "contextual_metadata" in seg_types

        # Video had 3 timestamps (0ms, 5000ms, 10000ms); 0ms & 5000ms were identical -> only 2 video_frame segments stored!
        video_frame_segs = [s for s in segments if s.segment_type == "video_frame"]
        assert len(video_frame_segs) == 2

        # Test all sample queries from Section 7 via execute_hybrid_search
        section_7_queries = [
            ("word labs", "City_Campus_Banner.png"),
            ("media lab", "panel_discussion_urban_housing.mp4"),
            ("urban fellows programme", "annual_report_2023.pdf"),
            ("school of environment and sustainability", "annual_report_2023.pdf"),
            ("city campus", "City_Campus_Banner.png"),
            ("housing workshop 2023", "City_Campus_Banner.png"),
            ("climate resilience panel discussion", "field_interview_audio.mp3"),
            ("budget spreadsheet", "budget_spreadsheet_2023.xlsx"),
            ("annual report pdf", "annual_report_2023.pdf"),
            ("field interview audio", "field_interview_audio.mp3"),
            ("panel discussion video about urban housing", "panel_discussion_urban_housing.mp4"),
        ]
        for query_str, expected_filename in section_7_queries:
            hits = hybrid_search.execute_hybrid_search(
                db=db,
                q=query_str,
                mode="hybrid",
                limit=5,
            )
            assert len(hits) >= 1, f"No results for query: {query_str}"
            top_filenames = [asset.original_filename for asset, _payload in hits[:3]]
            assert expected_filename in top_filenames, f"Expected {expected_filename} in top 3 for '{query_str}', got {top_filenames}"
            for _asset, payload in hits:
                assert payload["search_score"] > 0
                assert payload["matched_evidence"]
                assert payload["project_context"]["is_visual_claim"] is False

        # Verify PDF page provenance in search results
        pdf_hits = hybrid_search.execute_hybrid_search(
            db=db,
            q="climate resilience research outcomes",
            mode="hybrid",
            media_type="document",
            limit=5,
        )
        assert pdf_hits[0][0].original_filename == "annual_report_2023.pdf"
        assert pdf_hits[0][1].get("matched_page") == 2

        # Verify audio timestamp window provenance in search results
        aud_hits = hybrid_search.execute_hybrid_search(
            db=db,
            q="climate resilience panel discussion in Bengaluru",
            mode="hybrid",
            media_type="audio",
            limit=5,
        )
        assert aud_hits[0][0].original_filename == "field_interview_audio.mp3"
        assert aud_hits[0][1].get("matched_timestamp_ms") is not None

    # -----------------------------------------------------------------------
    # 6. Incremental Ingestion: Unchanged skip, Move/Rename without re-embed,
    #    Duplicate source path preservation, and Complete-Scan Deletion
    # -----------------------------------------------------------------------
    renamed_img = source_root / "Word_Labs" / "2023_Housing_Workshop" / "Renamed_Campus_Banner.png"
    img_file.rename(renamed_img)

    dup_dir = source_root / "Word_Labs" / "Duplicate_Copy"
    dup_dir.mkdir(parents=True)
    dup_img = dup_dir / "Duplicate_Banner.png"
    dup_img.write_bytes(renamed_img.read_bytes())

    with TestingSession() as db:
        job2 = models.IngestionJob(
            source_id=source_id,
            requested_by=admin_id,
            kind="preview",
            state="queued",
            max_files=0,
        )
        db.add(job2)
        db.commit()
        job2_id = job2.id

    assert worker.run_once() is True

    with TestingSession() as db:
        job2 = db.get(models.IngestionJob, job2_id)
        assert job2.state == "preview_ready"
        # 4 unchanged files skipped + 1 moved file ready + 1 duplicate file marked duplicate
        assert job2.summary["skipped"] == 4
        assert job2.summary["ready"] == 1
        assert job2.summary["duplicate"] == 1
        job2.kind = "ingest"
        job2.state = "queued"
        db.commit()

    # Track if image_embedding is called during the moved-file ingest (it should NOT be called!)
    embed_calls = []
    monkeypatch.setattr(worker, "image_embedding", lambda p: embed_calls.append(p) or fake_image_embedding(p))

    assert worker.run_once() is True
    assert embed_calls == [], "Moved/renamed file should update path metadata without re-running visual embedding!"

    # Now delete the duplicate file and run a complete preview scan to verify deletion reconciliation
    dup_img.unlink()
    with TestingSession() as db:
        job3 = models.IngestionJob(
            source_id=source_id,
            requested_by=admin_id,
            kind="preview",
            state="queued",
            max_files=0,
        )
        db.add(job3)
        db.commit()
        job3_id = job3.id

    assert worker.run_once() is True

    with TestingSession() as db:
        job3 = db.get(models.IngestionJob, job3_id)
        assert job3.state == "preview_ready"
        # All 5 remaining files are now unchanged and skipped!
        assert job3.summary["skipped"] == 5
        active_assets = db.scalars(select(models.MediaAsset)).all()
        assert len(active_assets) == 5

    engine.dispose()


# ---------------------------------------------------------------------------
# 5b. Perceptual dHash video frame deduplication & transcript windowing
# ---------------------------------------------------------------------------
def test_video_frame_dhash_and_transcript_windows(tmp_path):
    p1 = tmp_path / "f1.png"
    p2 = tmp_path / "f2.png"
    p1.write_bytes(_tiny_png_bytes((50, 100, 150)))
    p2.write_bytes(_tiny_png_bytes((50, 100, 150)))
    h1 = search_segments.compute_frame_dhash(p1)
    h2 = search_segments.compute_frame_dhash(p2)
    assert search_segments.hamming_distance(h1, h2) == 0

    windows = search_segments.build_transcript_windows(
        transcript_result={
            "text": "First segment text. Second segment text.",
            "language": "en",
            "segments": [
                {"start": 0.0, "end": 15.0, "text": "First segment text."},
                {"start": 15.0, "end": 38.0, "text": "Second segment text."},
            ],
        },
        duration_ms=38000,
        window_ms=20000,
        hop_ms=15000,
    )
    assert len(windows) >= 2
    assert windows[0]["start_ms"] == 0
    assert windows[0]["end_ms"] >= 15000


# ---------------------------------------------------------------------------
# 9. Portable Local ML Runtime Health & Inference (CPU/GPU fallback)
# ---------------------------------------------------------------------------
def test_local_ml_service_portable_runtime(monkeypatch):
    token = "JYi19tf-zHWpHGTk6nbBXHVQXvnoYQWIAe98NbR2g1w"
    monkeypatch.setenv("LOCAL_ML_TOKEN", token)
    local_ml_service._init_runtime()

    client = TestClient(local_ml_service.app)
    health = client.get("/healthz")
    assert health.status_code == 200
    data = health.json()
    assert data["status"] == "ready"
    assert data["model"]["dim"] == 768
    assert data["max_text_chars"] >= 4000

    # Long document chunk (1200+ chars) succeeds
    long_chunk = "IIHS Urban Fellows Programme and Media Lab archival indexing. " * 25
    assert len(long_chunk) > 1200
    emb_resp = client.post(
        "/embed/text",
        json={"texts": [long_chunk, "Word Lab housing workshop"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert emb_resp.status_code == 200
    vectors = emb_resp.json()["embeddings"]
    assert len(vectors) == 2
    assert len(vectors[0]) == 768

    img_resp = client.post(
        "/embed/image",
        content=_tiny_png_bytes(),
        headers={"Authorization": f"Bearer {token}", "X-Image-Suffix": ".png"},
    )
    assert img_resp.status_code == 200
    assert len(img_resp.json()["embedding"]) == 768
