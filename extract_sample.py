"""Clean five reviewed English PDF pages. Run: python3 extract_sample.py.

Uses Poppler and Python's standard library. Layout rules below apply only to
these sample pages; this is not yet a full-corpus ingestion script.
"""

import hashlib
import re
import shutil
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


DOCUMENTS = Path(__file__).resolve().parent / "documents"
OUTPUT = DOCUMENTS / "sample.txt"
PROSE = "crpc-1898-part-2.pdf"
TABLE = "crpc-1898-part-4.pdf"
# PDF page: (printed page, body left/right edges, footnote top, heading's section).
# Coordinates are PDF points, measured on the reviewed source pages.
PAGES = {
    1: (39, 120, 415, 580, "1"),
    21: (59, 120, 415, 420, "32"),
    98: (136, 195, 500, 650, "173"),
    99: (137, 120, 419, 630, "174"),
}
FONT_CODES = str.maketrans({"\uf020": " ", "\uf02d": "-"})
REFERENCE = r"\[fn:\d+:(?:\d+|\*)\]"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def normalize(text, page):
    text = text.translate(FONT_CODES).replace("\uf0a7", f"[fn:{page}:*]")
    require(not any(c == "\ufffd" or unicodedata.category(c) == "Co"
                    or (unicodedata.category(c) == "Cc" and c not in "\n\r\t")
                    for c in text), f"Unrecognized font/control character on PDF page {page}.")
    return text


def extract(source, page, mode):
    result = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), mode, "-nopgbrk",
         "-enc", "UTF-8", str(source), "-"],
        capture_output=True, text=True, encoding="utf-8",
    )
    require(result.returncode == 0 and not result.stderr.strip(),
            f"Extraction failed for {source.name}, page {page}: {result.stderr.strip()}")
    return result.stdout


def read_words(source, page, printed_page):
    root = ET.fromstring(extract(source, page, "-bbox-layout"))
    words = []
    for element in root.iter():
        if element.tag.endswith("}word"):
            word = {key: float(element.get(key)) for key in ("xMin", "yMin", "xMax", "yMax")}
            word["text"] = normalize(element.text or "", page).strip()
            if word["text"]:
                words.append(word)
    require(any(w["text"] == str(printed_page) and w["yMin"] < 120 for w in words),
            f"Incorrect printed page label: PDF page {page}.")
    return words


def region(words, box, page):
    """Read a rectangular region in line order and retain superscript references."""
    left, top, right, bottom = box
    selected = [w for w in words if left <= w["xMin"] < right and top <= w["yMin"] < bottom]
    rows = []
    for word in sorted(selected, key=lambda w: (w["yMin"], w["xMin"])):
        if not rows or word["yMin"] - rows[-1][0]["yMin"] > 4:
            rows.append([])
        rows[-1].append(word)
    lines = []
    previous_y = None
    for row in rows:
        if previous_y is not None and row[0]["yMin"] - previous_y > 16:
            lines.append("")
        previous_y = row[0]["yMin"]
        text, previous = "", None
        for word in sorted(row, key=lambda w: w["xMin"]):
            value = word["text"]
            if value.isdigit() and word["yMax"] - word["yMin"] < 7:
                value = f"[fn:{page}:{value}]"
            gap = word["xMin"] - previous["xMax"] if previous else 0
            text += (" " if gap > 0.8 else "") + value
            previous = word
        lines.append(text.strip())
    return lines


def paragraphs(lines):
    """Join visual wrapping, keeping section/subsection/list boundaries."""
    result, new_paragraph = [], False
    for line in lines:
        if not line:
            new_paragraph = True
            continue
        marker = re.sub(REFERENCE, "", line).lstrip("[")
        if not result or new_paragraph or re.match(r"(?:\d+\.\s|\([0-9A-Za-z]+\)|Provided that)", marker):
            result.append(line)
        else:
            result[-1] += ("" if result[-1].endswith("-") else " ") + line
        new_paragraph = False
    return "\n\n".join(result)


def parse_page(words, page):
    printed, left, right, foot_top, heading_section = PAGES[page]
    # This tiny standalone dot is a known PDF artifact, not a legal-text glyph.
    words = [w for w in words if not (page == 98 and w["text"] == "."
                                     and w["xMax"] < 130 and w["yMax"] - w["yMin"] < 2)]
    body_top = 310 if page == 1 else 120
    if page == 21:
        lines = region(words, (left, 120, right, 155), page)
        for top, bottom in [(155, 251), (251, 340), (340, foot_top)]:
            court = " ".join(region(words, (left, top, 290, bottom), page))
            sentence = " ".join(region(words, (290, top, right, bottom), page))
            require(court and sentence, "Missing section 32 court/sentence column.")
            lines.append(court + " " + sentence)
    else:
        lines = region(words, (left, body_top, right, foot_top), page)
    heading_box = (120, body_top, left, foot_top) if page == 98 else (right, body_top, 500, foot_top)
    heading = " ".join(region(words, heading_box, page)).replace("Commence- ment", "Commencement")
    notes = " ".join(region(words, (0, foot_top, 1000, 1000), page))
    footnotes = dict(re.findall(rf"({REFERENCE})\s*(.*?)(?={REFERENCE}|$)", notes))
    expected_notes = 7 if page == 21 else 2 if page == 1 else 1
    require(len(footnotes) == expected_notes, f"Unexpected footnote count on PDF page {page}.")
    preamble = paragraphs(region(words, (0, 120, 1000, 310), page)) if page == 1 else ""
    body = paragraphs(lines)
    # Detect omissions/duplication when separating regions. Reference numbers are
    # metadata; all other letters and digits must survive exactly once.
    original = " ".join(w["text"] for w in words if w["yMin"] >= 120
                        and not (w["text"].isdigit() and w["yMax"] - w["yMin"] < 7))
    cleaned = " ".join([body, heading, preamble, *footnotes.values()])
    counts = lambda text: Counter(c for c in re.sub(REFERENCE, "", text) if c.isalnum())
    require(counts(original) == counts(cleaned), f"Text lost or duplicated on PDF page {page}.")
    return body, {heading_section: heading}, footnotes, preamble


def build_sections(pages):
    """Join continuations only across consecutive PDF pages of this source."""
    sections, active, previous_page = [], None, None
    for page, body in pages.items():
        if previous_page is None or page != previous_page + 1:
            active = None
        for part in re.split(r"(?m)(?=^\d+\.\s)", body):
            part = part.strip()
            if not part:
                continue
            match = re.match(r"(\d+)\.\s", part)
            if match:
                if active is not None and active["section"]:
                    active["complete"] = True
                active = {"section": match[1], "pages": [], "text": "", "complete": False}
                sections.append(active)
            elif active is None:
                active = {"section": None, "pages": [], "text": "", "complete": False}
                sections.append(active)
            active["pages"].append(page)
            active["text"] += ("\n\n" if active["text"] else "") + part
        previous_page = page
    return sections


def validate(sections, footnotes, preamble):
    numbered = {s["section"]: s for s in sections if s["section"]}
    require(list(numbered) == ["1", "2", "3", "32", "173", "174"], "Unexpected section boundaries.")
    require([s["section"] for s in sections if s["complete"]] == ["1", "2", "173"],
            "Unexpected section completeness.")
    section = numbered["173"]
    require(section["pages"] == [98, 99] and "174." not in section["text"], "Section 173 join failed.")
    markers = re.findall(r"(?m)^(?:\[fn:\d+:\d+\])?\[?\(([^)]+)\)", section["text"])
    require(markers == ["a", "b", "2", "3", "3A", "a", "b", "3B", "4"],
            "Section 173 subsection sequence changed.")
    require("Report of police-officer" not in section["text"], "Margin heading leaked into body.")
    body32 = numbered["32"]["text"]
    for label, court, term in [("a", "first", "five"), ("b", "second", "three"), ("c", "third", "two")]:
        row = re.search(rf"(?m)^\({label}\).*", body32)
        require(row and f"{court} class" in row[0] and re.search(rf"\[{term}\s+years\]", row[0]),
                f"Section 32({label}) court/sentence pairing failed.")
    text = preamble + "\n" + "\n".join(s["text"] for s in sections)
    references = set(re.findall(REFERENCE, text))
    require(references == set(footnotes), "Missing or unlinked footnote references.")


def main():
    require(shutil.which("pdftotext"), "Missing pdftotext. On macOS: brew install poppler")
    manifest = (DOCUMENTS / "SOURCES.md").read_text(encoding="utf-8")
    checksums = dict(re.findall(
        r"\*\*([^*]+\.pdf)\*\*\n  - Downloaded: [^\n]+\n  - SHA-256: `([0-9a-f]{64})`", manifest))
    for filename in (PROSE, TABLE):
        actual = hashlib.sha256((DOCUMENTS / filename).read_bytes()).hexdigest()
        require(checksums.get(filename) == actual, f"Source checksum missing or changed: {filename}")

    pages, headings, footnotes, preamble = {}, {}, {}, ""
    for page, (printed, *_) in PAGES.items():
        words = read_words(DOCUMENTS / PROSE, page, printed)
        body, titles, notes, context = parse_page(words, page)
        require(body.strip(), f"Empty body on PDF page {page}.")
        pages[page] = body
        headings.update(titles)
        footnotes.update(notes)
        preamble += context
    sections = build_sections(pages)
    validate(sections, footnotes, preamble)
    table = normalize(extract(DOCUMENTS / TABLE, 1, "-layout"), 1).strip()
    require(table.split()[0] == "282" and "SCHEDULE II" in table, "Incorrect schedule page.")

    blocks = [
        "STEP 01: CLEANED ENGLISH SAMPLE\n"
        "Five source pages; automatic checks passed. No routine manual step required.\n"
        "Section status describes completeness within this sample, not legal currency.\n"
        "Scope: archived English PDFs; current amendment coverage remains unverified.\n"
        "Layout rules apply only to these five pages. Original PDF wording is preserved;\n"
        "font codes, wrapping, column order and footnote references are normalized.\n"
        "PARTIAL fragments and the unparsed schedule are excluded from retrieval examples.\n"
        "Footnote IDs use [fn:PDF-page:marker]; their text is stored separately below.\n"
        f"SOURCE: {PROSE}\nSHA-256: {checksums[PROSE]}\n"
        f"SOURCE: {TABLE}\nSHA-256: {checksums[TABLE]}",
        "CONTEXT: title, preamble and hierarchy\n"
        f"SOURCE: {PROSE} | PDF page 1 | printed page 39\n" + preamble,
    ]
    for section in sections:
        number = section["section"]
        label = f"SECTION {number}" if number else "UNASSIGNED CONTINUATION"
        status = "COMPLETE IN SAMPLE" if section["complete"] else "PARTIAL - excluded from retrieval examples"
        sources = "; ".join(f"PDF {p} / printed {PAGES[p][0]}" for p in section["pages"])
        heading = f"\nHEADING: {headings[number]}" if number in headings else ""
        blocks.append(f"{label}\nSTATUS: {status}\nSOURCE: {PROSE} | {sources}{heading}\n\n{section['text']}")
    for page in PAGES:
        notes = [f"{key} {value.strip()}" for key, value in footnotes.items() if key.startswith(f"[fn:{page}:")]
        blocks.append(f"FOOTNOTES\nSOURCE: {PROSE} | PDF {page} / printed {PAGES[page][0]}\n\n" + "\n\n".join(notes))
    blocks.append(
        "SCHEDULE II - TABLE SAMPLE\nSTATUS: UNPARSED TABLE - excluded from retrieval examples\n"
        f"SOURCE: {TABLE} | PDF 1 / printed 282\n"
        "NEXT: parse eight columns and resolve 'Ditto'; numbers refer to Penal Code sections.\n"
        "Text below retains the table's spatial layout; it is not a prose section.\n\n" + table)
    output = ("\n\n" + "=" * 72 + "\n\n").join(blocks) + "\n"
    normalize(output, 0)  # Reject any unresolved font codes before replacing output.
    OUTPUT.write_text(output, encoding="utf-8")
    print(f"Saved {OUTPUT}")
    print("Checks passed: hashes, page labels, text preservation, boundaries, column pairings and 11 footnote links.")
    print("3 complete sections; 3 partial sections; 1 unassigned continuation; 1 unparsed table.")
    print("Original PDFs unchanged. Amendment coverage and full-corpus parsing remain pending.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, ET.ParseError) as error:
        raise SystemExit(f"ERROR: {error}\nSample was not updated.") from error
