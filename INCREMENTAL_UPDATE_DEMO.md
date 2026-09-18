# Incremental Document Update Demonstration

## Purpose

This demonstration shows that the legal-document pipeline can add a new legal source without rebuilding the existing corpus from scratch.

The update mechanism is designed to:

- detect new, changed, removed, and unchanged legal records;
- preserve unchanged database rows;
- preserve reusable embeddings;
- generate embeddings only for new or changed content; and
- make newly added legal material immediately available to retrieval.

## Baseline

Before the update, the indexed corpus contained the statutory body of the Code of Criminal Procedure, 1898.

Baseline state:

| Metric | Value |
|---|---:|
| Indexed legal sections/ranges | 515 |
| Reusable embeddings | 515 |
| Embeddings requiring generation | 0 |
| Indexed source documents | 4 CrPC PDFs |

Baseline verification:

```bash
python3 ingest.py --diff
```

Result:

```text
Documents: added=0, changed=0, unchanged=4.
Sections: added=0, content_changed=0, metadata_changed=0, removed=0, unchanged=515.
Embeddings requiring refresh after load: 0.
Diff only: no database data was written.
```

Embedding baseline:

```bash
python3 embed.py --check
```

Result:

```text
Sections: 515; reusable embeddings: 515; embeddings to generate: 0.
```

## New Legal Document

The following document was added to `documents/`:

```text
evidence-act-1872.pdf
```

SHA-256:

```text
66d33bdfbf99ef2d396f8e9a8a58ccce230e2f5b32350b787276784ccc233d59
```

The ingestion configuration is stored in:

```text
documents/sources.json
```

This allows ordinary structured legal PDFs to be configured through metadata instead of hard-coding each document directly into `ingest.py`.

## Step 1 — Validate the Updated Corpus

Command:

```bash
python3 ingest.py --check
```

Result:

```text
Validated 5 legal PDFs (533 pages).
Prepared 697 section/range records (526,262 characters).
Indexed laws: Code of Criminal Procedure, 1898; Evidence Act, 1872.
Checks passed: manifest, source hashes, page labels, text conservation and section boundaries.
Check only: no file or database data was written.
```

The parser prepared:

- 515 existing CrPC section/range records; and
- 182 Evidence Act section records.

## Step 2 — Preview the Incremental Change

Command:

```bash
python3 ingest.py --diff
```

Result:

```text
Documents: added=1, changed=0, unchanged=4.
Sections: added=182, content_changed=0, metadata_changed=0, removed=0, unchanged=515.
Embeddings requiring refresh after load: 182.
Diff only: no database data was written.
```

This confirmed that the new Evidence Act document could be added without modifying the existing 515 CrPC records.

## Step 3 — Apply the Incremental Database Update

Command:

```bash
python3 ingest.py --load
```

Result:

```text
Loaded source_documents=5, legal_sections=697.
Incremental update complete; unchanged section rows and embeddings were preserved.
```

After ingestion:

| Record type | Before | After | Change |
|---|---:|---:|---:|
| Source documents | 4 | 5 | +1 |
| Legal sections/ranges | 515 | 697 | +182 |
| Unchanged CrPC records | 515 | 515 | 0 |
| New Evidence Act records | 0 | 182 | +182 |

## Step 4 — Verify Selective Embedding Work

Before generating the new embeddings:

```bash
python3 embed.py --check
```

Result:

```text
Sections: 697; reusable embeddings: 515; embeddings to generate: 182.
```

This is the key incremental behavior: the existing 515 embeddings were reusable, and only the 182 newly added Evidence Act records required embedding generation.

Command:

```bash
python3 embed.py --load
```

The loader generated and saved only the pending embeddings:

```text
Saved 25/182 pending embeddings.
Saved 50/182 pending embeddings.
Saved 75/182 pending embeddings.
Saved 100/182 pending embeddings.
Saved 125/182 pending embeddings.
Saved 150/182 pending embeddings.
Saved 175/182 pending embeddings.
Saved 182/182 pending embeddings.
Complete: 697 active section embeddings stored in PostgreSQL.
```

Final verification:

```bash
python3 embed.py --check
```

Result:

```text
Sections: 697; reusable embeddings: 697; embeddings to generate: 0.
```

## Step 5 — Verify Retrieval From the New Document

Query:

```bash
python3 embed.py --search "What does the Evidence Act say about confessions made to a police officer?"
```

Top results:

```text
Evidence Act, 1872 | Section 25 | evidence-act-1872.pdf | PDF [31] | similarity 0.8372
Evidence Act, 1872 | Section 26 | evidence-act-1872.pdf | PDF [31] | similarity 0.8014
Evidence Act, 1872 | Section 27 | evidence-act-1872.pdf | PDF [31] | similarity 0.7707
Evidence Act, 1872 | Section 24 | evidence-act-1872.pdf | PDF [30] | similarity 0.7664
Evidence Act, 1872 | Section 29 | evidence-act-1872.pdf | PDF [31] | similarity 0.7553
```

Section 25 of the newly added Evidence Act was the top retrieval result.

## What the Demonstration Proves

The update pipeline successfully:

1. detected one newly added legal document;
2. parsed 182 new legal sections;
3. preserved all 515 existing CrPC section records;
4. reused all 515 existing CrPC embeddings;
5. generated only the 182 new embeddings required by the update;
6. expanded the active retrieval index from 515 to 697 records; and
7. retrieved the newly added Evidence Act content successfully.

## Incremental Update Flow

```text
Existing corpus
515 CrPC records
515 reusable embeddings
        |
        v
Add Evidence Act PDF + source configuration
        |
        v
python3 ingest.py --diff
        |
        +-- 1 new document
        +-- 182 new sections
        +-- 515 unchanged sections
        |
        v
python3 ingest.py --load
        |
        v
697 database records
        |
        v
python3 embed.py --check
        |
        +-- 515 reusable embeddings
        +-- 182 pending embeddings
        |
        v
python3 embed.py --load
        |
        v
697 reusable embeddings
0 pending
        |
        v
Retrieval test
Evidence Act Section 25 returned as top result
```

## Adding or Replacing Future Legal Sources

For an ordinary structured legal PDF, the intended workflow is:

```text
1. Place the PDF in documents/
2. Add or update its entry in documents/sources.json
3. Run python3 ingest.py --check
4. Run python3 ingest.py --diff
5. Review the detected changes
6. Run python3 ingest.py --load
7. Run python3 embed.py --check
8. Run python3 embed.py --load if embeddings are pending
9. Verify retrieval
```

The Python ingestion code does not need to be edited for every ordinary structured legal PDF that can use an existing parser profile.

A document with a fundamentally different layout, such as scanned image-only pages or complex table-only legislation, may require an additional parser profile.

## Result

**Incremental document-update demonstration: successful.**

The system added a new legal document and made it retrievable while preserving all existing CrPC records and embeddings.
