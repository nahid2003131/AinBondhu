"""Validate the archived CrPC PDFs and load section records into PostgreSQL.

Run a read-only check:
    .venv/bin/python ingest.py --check

Load the validated records after setting DATABASE_URL:
    .venv/bin/python ingest.py --load

This deliberately does not create embeddings. It stores one record per section
or explicit section range found in the statutory body (Parts 2 and 3).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
DOCUMENTS = ROOT / "documents"
SOURCE_VERSION = "crpc-old-bdlaws-archive-2026-09-17-v1"
LAW_CODE = "Code of Criminal Procedure, 1898"


@dataclass(frozen=True)
class Source:
    filename: str
    sha256: str
    pages: int
    printed_page_offset: int
    role: str
    section_min: int | None = None
    section_max: int | None = None


SOURCES = (
    Source(
        "crpc-1898-part-1.pdf",
        "d343152757946aec345a81c6c92c254338e4066f0acd205b8b8574555914218c",
        38,
        0,
        "contents_index",
    ),
    Source(
        "crpc-1898-part-2.pdf",
        "691ae36aa388b46396c7c51e344beb2b162c31d9c36ededa8c34c70467bc8ff0",
        123,
        38,
        "statutory_body",
        1,
        220,
    ),
    Source(
        "crpc-1898-part-3.pdf",
        "f13e13b04fb4bd9f7fc13a77897d2572a0373d9f84d78f527af5bbecfa6bb1f8",
        120,
        161,
        "statutory_body",
        221,
        565,
    ),
    Source(
        "crpc-1898-part-4.pdf",
        "71902dd0f7746298e3c950a2fb407d675f4a1adbf84ecc8bc8b541692972b627",
        160,
        281,
        "schedules_forms",
    ),
)

# The source PDFs use a few custom-font code points. The mapping preserves their
# visible meaning while making the extracted text valid, searchable Unicode.
FONT_CODES = str.maketrans(
    {
        "\uf020": " ",
        "\uf02d": "-",
        "\uf0a7": "*",
        "\uf0ac": "←",
        "\uf02a": "*",
    }
)

SECTION_ID = r"\d{1,3}[A-Z]{0,2}"
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
                # References inside prose and footnotes often say "section 173" or
                # have an earlier number. Neither marks a new top-level provision.
                if not re.search(r"\bsections?\b", prefix, re.I) and not re.search(r"\d", prefix):
                    starts.append((offset, match.group("section")))
        offset += len(line_with_end)

    valid = []
    for position, section_ref in starts:
        number, _ = section_key(section_ref.split("-")[0])
        if source.section_min <= number <= source.section_max:
            valid.append((position, section_ref))
    return valid


# Body pages use alternating margins. Rules below are specific to these checked
# archive hashes, not to evaluation questions or expected answers.
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
            # Only small superscript numbers, not omitted-text asterisks.
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
            # The subsection (2) paragraph between the two tables spans all columns.
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
            header = [w for w in words if w["yMin"] < 120]
            require(str(printed) in [w["t"] for w in header],
                    f"Wrong printed page label: {source.filename}, PDF {number}")
            rules = [line["top"] for line in page.lines
                     if abs(line["x0"] - 126) < 1 and 140 < line["width"] < 150
                     and line["height"] < 1 and line["top"] > 120]
            require(len(rules) <= 1, f"Ambiguous footnote separator on PDF page {number}")
            foot_top = rules[0] if rules else 800
            # Printed page 195 has notes beneath its table without a separator.
            if source.filename == "crpc-1898-part-3.pdf" and number == 34:
                require(not rules, "Unexpected footnote rule on printed page 195")
                foot_top = 515
            notes = [w for w in words if w["yMin"] >= foot_top]
            main = [w for w in words if 120 <= w["yMin"] < foot_top]
            # Tiny standalone dot is a PDF layout artifact, retained as metadata.
            artifacts = [w for w in main if w["t"] == "." and w["yMax"] - w["yMin"] < 2]
            main = [w for w in main if w not in artifacts]
            left, right = (120, 419) if printed % 2 else (195, 500)
            rects = [r for r in page.rects if r["top"] > 120 and r["width"] > 10 and r["height"] < 1]
            tables = []
            if rects:
                tables = page.find_tables({"explicit_vertical_lines": [
                    min(r["x0"] for r in rects), max(r["x1"] for r in rects)]})
                require(source.filename == "crpc-1898-part-3.pdf" and 30 <= number <= 35
                        and len(tables) == 1, f"Unreviewed table layout: {source.filename}, PDF {number}")
            table_words = [w for w in main if any(in_box(w, t.bbox) for t in tables)]
            ordinary = [w for w in main if w not in table_words]
            # On the opening page, title/preamble spans the usual body/margin boundary.
            preamble = [w for w in ordinary if source.filename == "crpc-1898-part-2.pdf"
                        and number == 1 and w["yMin"] < 310]
            ordinary = [w for w in ordinary if w not in preamble]
            body = [w for w in ordinary if left <= w["xMin"] < right]
            margin = [w for w in ordinary if w not in body]
            groups = header + notes + artifacts + preamble + body + margin + table_words
            require(sorted(w["id"] for w in groups) == sorted(w["id"] for w in words),
                    f"Word conservation failed: {source.filename}, PDF {number}")

            if source.filename == "crpc-1898-part-2.pdf" and number == 21:
                # Section 32 is an unruled, two-column table: court then sentence.
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
            "source_version": SOURCE_VERSION, "law_code": LAW_CODE,
            "source_file": source.filename, "source_sha256": source.sha256,
            "pdf_pages": pdf_pages,
            "printed_pages": [source.printed_page_offset + p for p in pdf_pages],
            "content": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        })
    return records


def prepare() -> tuple[list[dict], list[dict]]:
    require(shutil.which("pdftotext") is not None, "Poppler pdftotext is required")
    documents = []
    sections = []
    counts = {}
    for source in SOURCES:
        extract_pages(source)
        pages = layout_pages(source) if source.role == "statutory_body" else []
        extracted = build_sections(source, pages) if pages else []
        documents.append(
            {
                "source_version": SOURCE_VERSION,
                "law_code": LAW_CODE,
                "source_file": source.filename,
                "source_sha256": source.sha256,
                "pdf_page_count": source.pages,
                "role": source.role,
                "parser_version": PARSER_VERSION,
                "pages": pages,
                "ingestion_status": "indexed" if source.role == "statutory_body" else "validated_excluded",
            }
        )
        if source.role == "statutory_body":
            sections.extend(extracted)
            counts[source.filename] = len(extracted)

    require(counts == {"crpc-1898-part-2.pdf": 230, "crpc-1898-part-3.pdf": 285},
            f"Section boundary count changed: {counts}")
    require(len(sections) == 515, "Expected 515 statutory section/range records")
    refs = [record["section_ref"] for record in sections]
    require(len(refs) == len(set(refs)), "Duplicate section references detected")
    for required_ref in ("1", "2", "126A", "173", "205D", "206-220", "221", "251-259", "266-336",
                         "488-490", "565"):
        require(required_ref in refs, f"Missing known section boundary: {required_ref}")
    validate_records(documents, sections)
    return documents, sections


def validate_records(documents: list[dict], sections: list[dict]) -> None:
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


def load_database(database_url: str, documents: list[dict], sections: list[dict]) -> None:
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install requirements.txt before using --load") from exc

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
            # Idempotent migration keeps the original two-table project structure.
            cursor.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS parser_version TEXT")
            cursor.execute("ALTER TABLE source_documents ADD COLUMN IF NOT EXISTS pages JSONB NOT NULL DEFAULT '[]'")
            cursor.execute("ALTER TABLE legal_sections ADD COLUMN IF NOT EXISTS fragments JSONB NOT NULL DEFAULT '[]'")
            # Preserve embeddings for unchanged text. Changed content hashes make
            # old embeddings ineligible until embed.py refreshes them.
            cursor.execute("DELETE FROM legal_sections WHERE source_version = %s AND NOT (section_ref = ANY(%s))",
                           (SOURCE_VERSION, [s["section_ref"] for s in sections]))
            cursor.execute("DELETE FROM source_documents WHERE source_version = %s AND NOT (source_file = ANY(%s))",
                           (SOURCE_VERSION, [d["source_file"] for d in documents]))
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
                """,
                [dict(s, fragments=Jsonb(s["fragments"])) for s in sections],
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate and summarize without writing")
    mode.add_argument("--load", action="store_true", help="validate and load PostgreSQL")
    args = parser.parse_args()

    documents, sections = prepare()
    total_characters = sum(len(record["content"]) for record in sections)
    print(f"Validated {len(documents)} CrPC PDFs ({sum(source.pages for source in SOURCES)} pages).")
    print(f"Prepared {len(sections)} section/range records ({total_characters:,} characters).")
    print("Indexed: Part 2 and Part 3 statutory body (243 pages; layout-v2).")
    print("Checks passed: source hashes, page labels, text conservation, continuations and table cells.")
    print("Footnotes/headings stored separately by source page; per-section footnote links not certified.")
    print("Excluded for now: Part 1 contents/index; Part 4 schedules/forms.")

    if args.load:
        load_dotenv(ROOT / ".env")
        database_url = os.getenv("DATABASE_URL", "").strip()
        require(database_url, "DATABASE_URL is required for --load")
        load_database(database_url, documents, sections)
        print(f"Loaded source_documents={len(documents)}, legal_sections={len(sections)}.")
        print(f"Source version: {SOURCE_VERSION}")
    else:
        print("Check only: no file or database data was written.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
