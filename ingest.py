"""Load legal PDFs from documents/sources.json into PostgreSQL incrementally.

The manifest defines each source, checksum, page range, section range, parser
profile, and optional validation rules. The loader extracts section-aware legal
records, compares them with PostgreSQL, and writes only added, changed, or
removed records so unchanged embeddings remain reusable.

Modes:
    --check  Validate configured PDFs and extraction without database access.
    --diff   Compare prepared records with PostgreSQL without writing.
    --load   Apply the detected incremental document and section updates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
DOCUMENTS = ROOT / "documents"
SOURCE_MANIFEST = DOCUMENTS / "sources.json"


@dataclass(frozen=True)
class Source:
    filename: str
    sha256: str
    pages: int
    printed_page_offset: int
    role: str
    source_version: str
    law_code: str
    body_pdf_start: int = 1
    section_min: int | None = None
    section_max: int | None = None
    parser_profile: str = "standard_statute"
    validation_profile: str | None = None
    expected_section_count: int | None = None
    required_sections: tuple[str, ...] = ()
    layout: dict = field(default_factory=dict)


def load_sources() -> tuple[Source, ...]:
    require(SOURCE_MANIFEST.is_file(), f"Missing source manifest: {SOURCE_MANIFEST}")
    try:
        payload = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {SOURCE_MANIFEST.name}: {exc}") from exc

    raw_sources = payload.get("sources")
    require(isinstance(raw_sources, list) and raw_sources, "sources.json must contain a non-empty 'sources' list")
    sources = []
    filenames = set()
    for index, raw in enumerate(raw_sources, 1):
        require(isinstance(raw, dict), f"Source entry {index} must be an object")
        required = ("filename", "sha256", "pages", "printed_page_offset", "role", "source_version", "law_code")
        missing = [name for name in required if name not in raw]
        require(not missing, f"Source entry {index} is missing: {', '.join(missing)}")
        require(raw["filename"] not in filenames, f"Duplicate source filename: {raw['filename']}")
        filenames.add(raw["filename"])
        require(re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]) is not None,
                f"Invalid SHA-256 for {raw['filename']}")
        require(isinstance(raw["pages"], int) and raw["pages"] > 0,
                f"Invalid page count for {raw['filename']}")

        role = raw["role"]
        parser_profile = raw.get("parser_profile", "standard_statute")
        require(parser_profile in {"none", "crpc", "standard_statute"},
                f"Unsupported parser_profile for {raw['filename']}: {parser_profile}")
        if role == "statutory_body":
            require(raw.get("section_min") is not None and raw.get("section_max") is not None,
                    f"Statutory source needs section_min/section_max: {raw['filename']}")
            require(parser_profile != "none",
                    f"Statutory source needs a parser profile: {raw['filename']}")

        sources.append(Source(
            filename=raw["filename"],
            sha256=raw["sha256"],
            pages=raw["pages"],
            printed_page_offset=raw["printed_page_offset"],
            role=role,
            source_version=raw["source_version"],
            law_code=raw["law_code"],
            body_pdf_start=raw.get("body_pdf_start", 1),
            section_min=raw.get("section_min"),
            section_max=raw.get("section_max"),
            parser_profile=parser_profile,
            validation_profile=raw.get("validation_profile"),
            expected_section_count=raw.get("expected_section_count"),
            required_sections=tuple(raw.get("required_sections", [])),
            layout=dict(raw.get("layout", {})),
        ))
    return tuple(sources)


FONT_CODES = str.maketrans(
    {
        "\uf020": " ",
        "\uf02d": "-",
        "\uf0a7": "*",
        "\uf0be": "-",
        "\uf0ac": "←",
        "\uf02a": "*",
    }
)

SECTION_ID = r"\d{1,4}[A-Z]{0,2}"
SINGLE_START = re.compile(
    rf"^(?P<prefix>[^\n]{{0,100}}?)\b(?P<section>{SECTION_ID})\.\s*"
    rf"(?=(?:\(?\d|\[|\]|[A-Z]))"
)
RANGE_START = re.compile(
    rf"^(?P<prefix>[^\n]{{0,80}}?)\b(?P<first>{SECTION_ID})\s*"
    rf"(?:and|-)\s*(?P<last>{SECTION_ID})\.\s*(?=\[|\(?\d|[A-Z])"
)
OMITTED_DASH_RANGE = re.compile(
    rf"\bSections?\s+(?P<first>{SECTION_ID})\s*(?:-|to)\s*"
    rf"(?P<last>{SECTION_ID}).*\bOmitted\b",
    re.I,
)
OMITTED_LIST_RANGE = re.compile(
    rf"\bSections\s+(?P<first>{SECTION_ID})\s*,.*\band\s+"
    rf"(?P<last>{SECTION_ID})\s+were omitted\b",
    re.I,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for block in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def section_key(section_ref: str) -> tuple[int, str]:
    final_ref = section_ref.split("-")[-1]
    match = re.fullmatch(r"(\d+)([A-Z]*)", final_ref)
    require(match is not None, f"Invalid section reference: {section_ref}")
    return int(match.group(1)), match.group(2)


def normalize(text: str, source: Source) -> str:
    text = text.translate(FONT_CODES).replace("\r\n", "\n").replace("\r", "\n")
    bad = sorted(
        {
            f"U+{ord(char):04X}"
            for char in text
            if char == "\ufffd"
            or unicodedata.category(char) == "Co"
            or (unicodedata.category(char) == "Cc" and char not in "\n\t\f")
        }
    )
    require(not bad, f"Unrecognized characters in {source.filename}: {', '.join(bad)}")
    return text


def extract_pages(source: Source) -> list[str]:
    path = DOCUMENTS / source.filename
    require(path.is_file(), f"Missing source: {path}")
    require(file_sha256(path) == source.sha256, f"Checksum changed: {source.filename}")

    result = subprocess.run(
        ["pdftotext", "-raw", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    require(result.returncode == 0, f"pdftotext failed for {source.filename}")
    require(not result.stderr.strip(), f"pdftotext warning for {source.filename}: {result.stderr.strip()}")

    pages = result.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    require(len(pages) == source.pages, f"Page count changed for {source.filename}")
    require(all(page.strip() for page in pages), f"Empty extracted page in {source.filename}")
    return [normalize(page, source) for page in pages]


def find_page_starts(page: str, source: Source) -> list[tuple[int, str]]:
    """Return possible top-level section starts as (line offset, section ref)."""
    starts: list[tuple[int, str]] = []
    offset = 0
    for line_with_end in page.splitlines(keepends=True):
        line = line_with_end.rstrip("\n")
        omitted_match = OMITTED_DASH_RANGE.search(line) or OMITTED_LIST_RANGE.search(line)
        range_match = RANGE_START.search(line)
        if omitted_match:
            section_ref = f'{omitted_match.group("first")}-{omitted_match.group("last")}'
            starts.append((offset, section_ref))
        elif range_match and not re.search(r"\bsections?\b", range_match.group("prefix"), re.I):
            section_ref = f'{range_match.group("first")}-{range_match.group("last")}'
            starts.append((offset, section_ref))
        else:
            match = SINGLE_START.search(line)
            if match:
                prefix = match.group("prefix")
                footnote_prefix = bool(re.fullmatch(r"\s*\d+\[\s*", prefix))
                if not re.search(r"\bsections?\b", prefix, re.I) and (footnote_prefix or not re.search(r"\d", prefix)):
                    starts.append((offset, match.group("section")))
        offset += len(line_with_end)

    valid = []
    for position, section_ref in starts:
        number, _ = section_key(section_ref.split("-")[0])
        if source.section_min <= number <= source.section_max:
            valid.append((position, section_ref))
    return valid


PARSER_VERSION = "layout-v2"
FOOTNOTE_REF = r"\[fn:\d+:(?:\d+|\*)\]"


def word_lines(words: list[dict], page: int) -> list[str]:
    rows: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["yMin"], w["xMin"])):
        if not rows or word["yMin"] - rows[-1][0]["yMin"] > 4:
            rows.append([])
        rows[-1].append(word)
    lines = []
    for row in rows:
        text, previous = "", None
        for word in sorted(row, key=lambda w: w["xMin"]):
            value = word["t"]
            if value.isdigit() and word["yMax"] - word["yMin"] < 7:
                value = f"[fn:{page}:{value}]"
            gap = word["xMin"] - previous["xMax"] if previous else 0
            text += (" " if gap > 0.8 else "") + value
            previous = word
        if text.strip():
            lines.append(text.strip())
    return lines


def in_box(word: dict, box: tuple) -> bool:
    left, top, right, bottom = box
    return left <= word["xMin"] < right and top <= (word["yMin"] + word["yMax"]) / 2 < bottom


def table_lines(words: list[dict], table, page: int) -> list[str]:
    """Serialize cells once, with explicit row spans for merged cells."""
    tops = sorted({round(c[1], 2) for c in table.cells} | {round(c[3], 2) for c in table.cells})
    columns = sorted({round(c[0], 2) for c in table.cells})
    assigned = []
    lines = []
    for row, top in enumerate(tops[:-1], 1):
        cells = []
        row_cells = sorted(c for c in table.cells if abs(c[1] - top) < .02)
        for cell in row_cells:
            cell_words = [w for w in words if in_box(w, cell)]
            assigned.extend(w["id"] for w in cell_words)
            value = " ".join(word_lines(cell_words, page))
            end_row = min(range(len(tops)), key=lambda i: abs(tops[i] - cell[3]))
            col = columns.index(round(cell[0], 2)) + 1
            span = f", rows {row}-{end_row}" if end_row > row else ""
            cells.append(f"column {col}{span}: {value}")
        if len(row_cells) == 1 and row_cells[0][0] == table.bbox[0] and row_cells[0][2] == table.bbox[2]:
            lines.append(value)
        else:
            lines.append(f"Table row {row} | " + " | ".join(cells))
    require(sorted(assigned) == sorted(w["id"] for w in words),
            f"Lost or duplicated table words on PDF page {page}")
    return lines


def layout_pages(source: Source) -> list[dict]:
    import pdfplumber
    import xml.etree.ElementTree as ET

    result = subprocess.run(
        ["pdftotext", "-bbox-layout", "-enc", "UTF-8", str(DOCUMENTS / source.filename), "-"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    require(not result.stderr.strip(), f"Layout extraction warning: {source.filename}")
    ns = {"p": "http://www.w3.org/1999/xhtml"}
    xml_pages = ET.fromstring(result.stdout).findall(".//p:page", ns)
    require(len(xml_pages) == source.pages, "Layout page count changed")
    output = []
    with pdfplumber.open(DOCUMENTS / source.filename) as pdf:
        for number, (element, page) in enumerate(zip(xml_pages, pdf.pages), 1):
            words = []
            for i, node in enumerate(element.findall(".//p:word", ns)):
                value = normalize(node.text or "", source).strip()
                if value:
                    words.append(dict(t=value, id=i, **{k: float(v) for k, v in node.attrib.items()}))
            printed = number + source.printed_page_offset
            if source.parser_profile == "crpc":
                header_bottom = 120
            else:
                header_bottom = float(source.layout.get("header_bottom", 145))
            header = [w for w in words if w["yMin"] < header_bottom]
            if source.layout.get("validate_printed_page_label", True):
                require(str(printed) in [w["t"] for w in header],
                        f"Wrong printed page label: {source.filename}, PDF {number}")
            if source.parser_profile == "crpc":
                rules = [line["top"] for line in page.lines
                         if abs(line["x0"] - 126) < 1 and 140 < line["width"] < 150
                         and line["height"] < 1 and line["top"] > 120]
            else:
                detector = source.layout.get("footnote_detector", "rect")
                candidates = page.rects if detector == "rect" else page.lines if detector == "line" else []
                x0 = float(source.layout.get("footnote_x0", 122.64))
                tolerance = float(source.layout.get("footnote_x0_tolerance", 2))
                width_min = float(source.layout.get("footnote_width_min", 135))
                width_max = float(source.layout.get("footnote_width_max", 145))
                min_top = float(source.layout.get("footnote_min_top", 500))
                rules = [item["top"] for item in candidates
                         if abs(item["x0"] - x0) < tolerance
                         and width_min < item["width"] < width_max
                         and item["height"] < 1 and item["top"] > min_top]
            require(len(rules) <= 1, f"Ambiguous footnote separator on PDF page {number}")
            foot_top = rules[0] if rules else float(source.layout.get("default_foot_top", 800))
            if source.filename == "crpc-1898-part-3.pdf" and number == 34:
                require(not rules, "Unexpected footnote rule on printed page 195")
                foot_top = 515
            notes = [w for w in words if w["yMin"] >= foot_top]
            main = [w for w in words if header_bottom <= w["yMin"] < foot_top]
            artifacts = [w for w in main if w["t"] == "." and w["yMax"] - w["yMin"] < 2]
            main = [w for w in main if w not in artifacts]
            if source.parser_profile == "crpc":
                left, right = (120, 419) if printed % 2 else (195, 500)
            else:
                if printed % 2:
                    left = float(source.layout.get("odd_body_left", 120))
                    right = float(source.layout.get("odd_body_right", 405))
                else:
                    left = float(source.layout.get("even_body_left", 190))
                    right = float(source.layout.get("even_body_right", 480))
            rects = [r for r in page.rects if r["top"] > 120 and r["width"] > 10 and r["height"] < 1]
            tables = []
            if rects and source.parser_profile == "crpc":
                tables = page.find_tables({"explicit_vertical_lines": [
                    min(r["x0"] for r in rects), max(r["x1"] for r in rects)]})
                require(source.filename == "crpc-1898-part-3.pdf" and 30 <= number <= 35
                        and len(tables) == 1, f"Unreviewed table layout: {source.filename}, PDF {number}")
            table_words = [w for w in main if any(in_box(w, t.bbox) for t in tables)]
            ordinary = [w for w in main if w not in table_words]
            preamble = [w for w in ordinary if source.filename == "crpc-1898-part-2.pdf"
                        and number == 1 and w["yMin"] < 310]
            ordinary = [w for w in ordinary if w not in preamble]
            body = [w for w in ordinary if left <= w["xMin"] < right]
            margin = [w for w in ordinary if w not in body]
            groups = header + notes + artifacts + preamble + body + margin + table_words
            require(sorted(w["id"] for w in groups) == sorted(w["id"] for w in words),
                    f"Word conservation failed: {source.filename}, PDF {number}")

            if source.filename == "crpc-1898-part-2.pdf" and number == 21:
                lines = word_lines([w for w in body if w["yMin"] < 155], number)
                for top, bottom in ((155, 251), (251, 340), (340, foot_top)):
                    court = [w for w in body if top <= w["yMin"] < bottom and w["xMin"] < 290]
                    sentence = [w for w in body if top <= w["yMin"] < bottom and w["xMin"] >= 290]
                    require(court and sentence, "Incomplete section 32 table row")
                    lines.append(" ".join(word_lines(court, number) + word_lines(sentence, number)))
            elif tables:
                table = tables[0]
                lines = word_lines([w for w in body if w["yMin"] < table.bbox[1]], number)
                lines += table_lines(table_words, table, number)
                lines += word_lines([w for w in body if w["yMin"] >= table.bbox[3]], number)
            else:
                lines = word_lines(body, number)
            rendered = "\n".join(lines + word_lines(header + notes + artifacts + preamble + margin, number))
            rendered = re.sub(r"Table row \d+ \| |column \d+(?:, rows \d+-\d+)?: ", "", rendered)
            original = " ".join(w["t"] for w in words
                                if not (w["t"].isdigit() and w["yMax"] - w["yMin"] < 7))
            letters = lambda text: Counter(c for c in re.sub(FOOTNOTE_REF, "", text) if c.isalnum())
            require(letters(original) == letters(rendered),
                    f"Text conservation failed: {source.filename}, PDF {number}")
            output.append({
                "pdf_page": number, "printed_page": printed, "lines": lines,
                "header": "\n".join(word_lines(header, number)),
                "context": word_lines(preamble, number),
                "margin": "\n".join(word_lines(margin, number)),
                "footnotes": "\n".join(word_lines(notes, number)),
                "artifacts": " ".join(w["t"] for w in artifacts),
                "word_count": len(words),
            })
            page.close()
    return output


def build_sections(source: Source, pages: list[dict]) -> list[dict]:
    records, active = [], None
    previous = (-1, "")
    in_heading = False
    for page in pages:
        for line in page.pop("lines"):
            bare = re.sub(FOOTNOTE_REF, "", line)
            starts = find_page_starts(bare, source)
            starts = [(off, ref) for off, ref in starts if section_key(ref) > previous]
            if starts:
                require(len(starts) == 1, "Multiple section starts on a single line")
                _, ref = starts[0]
                previous = section_key(ref)
                active = {"section_ref": ref, "fragments": []}
                records.append(active)
                in_heading = False
            elif re.match(r"^\[?(?:PART|CHAPTER)\b|^[A-Z]\.[—–-]", bare):
                in_heading = True
            if active is None or in_heading:
                page["context"].append(line)
            else:
                if not active["fragments"] or active["fragments"][-1]["pdf_page"] != page["pdf_page"]:
                    active["fragments"].append({"pdf_page": page["pdf_page"], "text": ""})
                fragment = active["fragments"][-1]
                fragment["text"] += ("\n" if fragment["text"] else "") + line
        page["context"] = "\n".join(page["context"])

    for record in records:
        content = "\n\n".join(f["text"] for f in record["fragments"])
        require(len(content) >= 20, f"Section {record['section_ref']} has too little text")
        pdf_pages = [f["pdf_page"] for f in record["fragments"]]
        record.update({
            "source_version": source.source_version, "law_code": source.law_code,
            "source_file": source.filename, "source_sha256": source.sha256,
            "pdf_pages": pdf_pages,
            "printed_pages": [source.printed_page_offset + p for p in pdf_pages],
            "content": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        })
    return records


def prepare(sources: tuple[Source, ...]) -> tuple[list[dict], list[dict]]:
    require(shutil.which("pdftotext") is not None, "Poppler pdftotext is required")
    documents = []
    sections = []

    for source in sources:
        extract_pages(source)
        pages = layout_pages(source) if source.role == "statutory_body" else []
        body_pages = pages[source.body_pdf_start - 1:] if pages else []
        extracted = build_sections(source, body_pages) if body_pages else []

        if source.role == "statutory_body":
            if source.expected_section_count is not None:
                require(len(extracted) == source.expected_section_count,
                        f"Section boundary count changed for {source.filename}: {len(extracted)}")
            refs = {record["section_ref"] for record in extracted}
            for required_ref in source.required_sections:
                require(required_ref in refs,
                        f"Missing known section boundary in {source.filename}: {required_ref}")
            sections.extend(extracted)

        documents.append({
            "source_version": source.source_version,
            "law_code": source.law_code,
            "source_file": source.filename,
            "source_sha256": source.sha256,
            "pdf_page_count": source.pages,
            "role": source.role,
            "parser_version": PARSER_VERSION,
            "pages": pages,
            "ingestion_status": "indexed" if source.role == "statutory_body" else "validated_excluded",
        })

    keys = [(record["source_version"], record["section_ref"]) for record in sections]
    require(len(keys) == len(set(keys)), "Duplicate section references detected within a source version")

    crpc_versions = {source.source_version for source in sources if source.validation_profile == "crpc"}
    if crpc_versions:
        crpc_sections = [record for record in sections if record["source_version"] in crpc_versions]
        validate_crpc_records(documents, crpc_sections)
    return documents, sections


def validate_crpc_records(documents: list[dict], sections: list[dict]) -> None:
    """Regression checks for observed extraction failures, before any DB write."""
    by_ref = {record["section_ref"]: record for record in sections}
    for record in sections:
        require(record["pdf_pages"] == [f["pdf_page"] for f in record["fragments"]],
                "Page citations must have a nonempty body fragment")
        require(all(f["text"].strip() for f in record["fragments"]), "Empty body fragment")
    require(by_ref["442A"]["pdf_pages"] == [74, 75], "Lost section 442A continuation page")
    require(all(f"({n})" in by_ref["442A"]["content"] for n in (1, 2, 3)),
            "Missing section 442A subsection")
    require("working days shall be counted" in by_ref["442A"]["content"], "Truncated section 442A")
    require("SPECIAL PROCEEDINGS" not in by_ref["442A"]["content"], "Chapter heading leaked into section")
    require(by_ref["1"]["pdf_pages"] == [1] and by_ref["173"]["pdf_pages"] == [98, 99],
            "Incorrect section page citations")
    require("(3B)" in by_ref["173"]["content"] and "furnish it free of cost" in by_ref["173"]["content"],
            "Truncated section 173")
    require("126A." not in by_ref["126"]["content"], "Section 126A merged into section 126")
    for label, court, term in (("a", "first", "five"), ("b", "second", "three"), ("c", "third", "two")):
        match = re.search(rf"(?m)^\({label}\).*", by_ref["32"]["content"])
        require(match is not None and f"{court} class" in match[0] and f"[{term} years]" in match[0],
                f"Incorrect court/sentence pairing in section 32({label})")
    table = by_ref["345"]["content"]
    require(by_ref["345"]["pdf_pages"] == list(range(30, 37)), "Missing compounding table page")
    require(table.count("Table row") == 64, "Table row count changed")
    require("(2) The offences" in table and "(7) No offence" in table, "Lost section 345 subsections")
    require("column 2: 417 | column 3: The person cheated." in table, "Table columns mixed")
    require("[fn:32:1][Act endangering" in table, "Table superscript assigned to wrong row")
    part3 = next(d for d in documents if d["source_file"] == "crpc-1898-part-3.pdf")
    notes = part3["pages"][33]["footnotes"]
    require("Sections 421, 422, 423, 424, 428 and 429" in notes,
            "Footnotes without a separator were not preserved")
    require("Sections 421, 422, 423, 424, 428 and 429" not in table,
            "Footnote leaked into statutory body")


def compare_database(database_url: str, documents: list[dict], sections: list[dict]) -> dict:
    """Compare prepared source records with the current database without writing anything."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before using --diff or --load") from exc

    stats = {
        "documents": {"added": [], "changed": [], "unchanged": []},
        "sections": {
            "added": [], "content_changed": [], "metadata_changed": [],
            "removed": [], "unchanged": [],
        },
    }
    managed_versions = sorted({document["source_version"] for document in documents})

    with psycopg.connect(
        database_url,
        connect_timeout=15,
        options="-c statement_timeout=60000",
        row_factory=dict_row,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema=current_schema() AND table_name='source_documents'
                ) AS source_documents,
                EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema=current_schema() AND table_name='legal_sections'
                ) AS legal_sections
                """
            )
            tables = cursor.fetchone()
            current_documents = {}
            current_sections = {}

            if tables["source_documents"]:
                current_documents = {
                    (row["source_version"], row["source_file"]): row
                    for row in cursor.execute(
                        """
                        SELECT source_version, law_code, source_file, source_sha256,
                               pdf_page_count, role, ingestion_status, parser_version, pages
                        FROM source_documents WHERE source_version = ANY(%s)
                        """,
                        (managed_versions,),
                    ).fetchall()
                }

            if tables["legal_sections"]:
                current_sections = {
                    (row["source_version"], row["section_ref"]): row
                    for row in cursor.execute(
                        """
                        SELECT source_version, law_code, source_file, source_sha256, section_ref,
                               pdf_pages, printed_pages, content, content_sha256, fragments
                        FROM legal_sections WHERE source_version = ANY(%s)
                        """,
                        (managed_versions,),
                    ).fetchall()
                }

    def document_signature(row: dict) -> tuple:
        return (
            row["law_code"], row["source_sha256"], row["pdf_page_count"], row["role"],
            row["ingestion_status"], row.get("parser_version"), row.get("pages") or [],
        )

    def section_metadata_signature(row: dict) -> tuple:
        return (
            row["law_code"], row["source_file"], row["source_sha256"],
            list(row["pdf_pages"]), list(row["printed_pages"]), row.get("fragments") or [],
        )

    for document in documents:
        key = (document["source_version"], document["source_file"])
        existing = current_documents.get(key)
        label = document["source_file"]
        if existing is None:
            stats["documents"]["added"].append(label)
        elif document_signature(existing) != document_signature(document):
            stats["documents"]["changed"].append(label)
        else:
            stats["documents"]["unchanged"].append(label)

    prepared_sections = {(row["source_version"], row["section_ref"]): row for row in sections}
    managed_documents = {
        (document["source_version"], document["source_file"])
        for document in documents if document["role"] == "statutory_body"
    }
    for key, section in prepared_sections.items():
        existing = current_sections.get(key)
        label = f'{section["law_code"]} §{section["section_ref"]}'
        if existing is None:
            stats["sections"]["added"].append(label)
        elif existing["content_sha256"] != section["content_sha256"]:
            stats["sections"]["content_changed"].append(label)
        elif section_metadata_signature(existing) != section_metadata_signature(section):
            stats["sections"]["metadata_changed"].append(label)
        else:
            stats["sections"]["unchanged"].append(label)

    for key, row in current_sections.items():
        document_key = (row["source_version"], row["source_file"])
        if document_key in managed_documents and key not in prepared_sections:
            stats["sections"]["removed"].append(f'{row["law_code"]} §{row["section_ref"]}')
    stats["sections"]["removed"].sort()
    return stats

def print_database_diff(stats: dict) -> None:
    documents = stats["documents"]
    sections = stats["sections"]
    refresh = len(sections["added"]) + len(sections["content_changed"])
    print("Database comparison:")
    print(
        "  Documents: "
        f"added={len(documents['added'])}, changed={len(documents['changed'])}, "
        f"unchanged={len(documents['unchanged'])}."
    )
    print(
        "  Sections: "
        f"added={len(sections['added'])}, content_changed={len(sections['content_changed'])}, "
        f"metadata_changed={len(sections['metadata_changed'])}, removed={len(sections['removed'])}, "
        f"unchanged={len(sections['unchanged'])}."
    )
    print(f"  Embeddings requiring refresh after load: {refresh}.")

    for label in ("added", "content_changed", "metadata_changed", "removed"):
        refs = sections[label]
        if refs:
            preview = ", ".join(refs[:12])
            suffix = f", ... (+{len(refs)-12} more)" if len(refs) > 12 else ""
            print(f"  {label.replace('_', ' ').title()} sections: {preview}{suffix}")


def load_database(database_url: str, documents: list[dict], sections: list[dict]) -> None:
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before using --load") from exc

    managed_documents = [
        (document["source_version"], document["source_file"])
        for document in documents if document["role"] == "statutory_body"
    ]
    refs_by_document = {
        key: [section["section_ref"] for section in sections
              if (section["source_version"], section["source_file"]) == key]
        for key in managed_documents
    }

    with psycopg.connect(database_url, connect_timeout=15, options="-c statement_timeout=60000") as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS source_documents (
                    source_version TEXT NOT NULL,
                    law_code TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    source_sha256 CHAR(64) NOT NULL,
                    pdf_page_count INTEGER NOT NULL CHECK (pdf_page_count > 0),
                    role TEXT NOT NULL,
                    ingestion_status TEXT NOT NULL,
                    PRIMARY KEY (source_version, source_file)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS legal_sections (
                    source_version TEXT NOT NULL,
                    law_code TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    source_sha256 CHAR(64) NOT NULL,
                    section_ref TEXT NOT NULL,
                    pdf_pages INTEGER[] NOT NULL,
                    printed_pages INTEGER[] NOT NULL,
                    content TEXT NOT NULL,
                    content_sha256 CHAR(64) NOT NULL,
                    PRIMARY KEY (source_version, section_ref),
                    FOREIGN KEY (source_version, source_file)
                        REFERENCES source_documents (source_version, source_file)
                )
                """
            )
            cursor.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS parser_version TEXT")
            cursor.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS pages JSONB NOT NULL DEFAULT '[]'")
            cursor.execute("ALTER TABLE legal_sections ADD COLUMN IF NOT EXISTS fragments JSONB NOT NULL DEFAULT '[]'")

            for source_version, source_file in managed_documents:
                section_refs = refs_by_document[(source_version, source_file)]
                cursor.execute(
                    """DELETE FROM legal_sections
                       WHERE source_version=%s AND source_file=%s
                       AND NOT (section_ref = ANY(%s))""",
                    (source_version, source_file, section_refs),
                )

            cursor.executemany(
                """
                INSERT INTO source_documents
                    (source_version, law_code, source_file, source_sha256,
                     pdf_page_count, role, ingestion_status, parser_version, pages)
                VALUES
                    (%(source_version)s, %(law_code)s, %(source_file)s, %(source_sha256)s,
                     %(pdf_page_count)s, %(role)s, %(ingestion_status)s, %(parser_version)s, %(pages)s)
                ON CONFLICT (source_version, source_file) DO UPDATE SET
                    law_code=EXCLUDED.law_code, source_sha256=EXCLUDED.source_sha256,
                    pdf_page_count=EXCLUDED.pdf_page_count, role=EXCLUDED.role,
                    ingestion_status=EXCLUDED.ingestion_status,
                    parser_version=EXCLUDED.parser_version, pages=EXCLUDED.pages
                WHERE source_documents.law_code IS DISTINCT FROM EXCLUDED.law_code
                   OR source_documents.source_sha256 IS DISTINCT FROM EXCLUDED.source_sha256
                   OR source_documents.pdf_page_count IS DISTINCT FROM EXCLUDED.pdf_page_count
                   OR source_documents.role IS DISTINCT FROM EXCLUDED.role
                   OR source_documents.ingestion_status IS DISTINCT FROM EXCLUDED.ingestion_status
                   OR source_documents.parser_version IS DISTINCT FROM EXCLUDED.parser_version
                   OR source_documents.pages IS DISTINCT FROM EXCLUDED.pages
                """,
                [dict(d, pages=Jsonb(d["pages"])) for d in documents],
            )
            cursor.executemany(
                """
                INSERT INTO legal_sections
                    (source_version, law_code, source_file, source_sha256, section_ref,
                     pdf_pages, printed_pages, content, content_sha256, fragments)
                VALUES
                    (%(source_version)s, %(law_code)s, %(source_file)s, %(source_sha256)s,
                     %(section_ref)s, %(pdf_pages)s, %(printed_pages)s, %(content)s,
                     %(content_sha256)s, %(fragments)s)
                ON CONFLICT (source_version, section_ref) DO UPDATE SET
                    law_code=EXCLUDED.law_code, source_file=EXCLUDED.source_file,
                    source_sha256=EXCLUDED.source_sha256, pdf_pages=EXCLUDED.pdf_pages,
                    printed_pages=EXCLUDED.printed_pages, content=EXCLUDED.content,
                    content_sha256=EXCLUDED.content_sha256, fragments=EXCLUDED.fragments
                WHERE legal_sections.law_code IS DISTINCT FROM EXCLUDED.law_code
                   OR legal_sections.source_file IS DISTINCT FROM EXCLUDED.source_file
                   OR legal_sections.source_sha256 IS DISTINCT FROM EXCLUDED.source_sha256
                   OR legal_sections.pdf_pages IS DISTINCT FROM EXCLUDED.pdf_pages
                   OR legal_sections.printed_pages IS DISTINCT FROM EXCLUDED.printed_pages
                   OR legal_sections.content IS DISTINCT FROM EXCLUDED.content
                   OR legal_sections.content_sha256 IS DISTINCT FROM EXCLUDED.content_sha256
                   OR legal_sections.fragments IS DISTINCT FROM EXCLUDED.fragments
                """,
                [dict(s, fragments=Jsonb(s["fragments"])) for s in sections],
            )

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate and summarize without database access")
    mode.add_argument("--diff", action="store_true", help="compare validated source records with PostgreSQL; no writes")
    mode.add_argument("--load", action="store_true", help="validate, show the incremental diff, and update PostgreSQL")
    args = parser.parse_args()

    sources = load_sources()
    documents, sections = prepare(sources)
    total_characters = sum(len(record["content"]) for record in sections)
    indexed_laws = sorted({source.law_code for source in sources if source.role == "statutory_body"})
    excluded = [source.filename for source in sources if source.role != "statutory_body"]
    print(f"Validated {len(documents)} legal PDFs ({sum(source.pages for source in sources)} pages).")
    print(f"Prepared {len(sections)} section/range records ({total_characters:,} characters).")
    print("Indexed laws: " + "; ".join(indexed_laws) + ".")
    print("Checks passed: manifest, source hashes, page labels, text conservation and section boundaries.")
    print("Footnotes/headings are stored separately by source page.")
    if excluded:
        print("Validated but excluded from indexing: " + ", ".join(excluded) + ".")

    if args.diff or args.load:
        load_dotenv(ROOT / ".env")
        database_url = os.getenv("DATABASE_URL", "").strip()
        require(database_url, "DATABASE_URL is required for --diff or --load")
        stats = compare_database(database_url, documents, sections)
        print_database_diff(stats)

        if args.load:
            load_database(database_url, documents, sections)
            remaining = compare_database(database_url, documents, sections)
            unresolved = sum(
                len(remaining["documents"][name]) for name in ("added", "changed")
            ) + sum(
                len(remaining["sections"][name])
                for name in ("added", "content_changed", "metadata_changed", "removed")
            )
            require(unresolved == 0, "Database still differs from the prepared corpus after --load")
            print(f"Loaded source_documents={len(documents)}, legal_sections={len(sections)}.")
            print("Incremental update complete; unchanged section rows and embeddings were preserved.")
            versions = sorted({document["source_version"] for document in documents})
            print("Source versions: " + ", ".join(versions))
        else:
            print("Diff only: no database data was written.")
    else:
        print("Check only: no file or database data was written.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
