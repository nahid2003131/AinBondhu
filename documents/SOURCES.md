# Source documents

The runnable project requires the four CrPC PDFs below. The Penal Code and Evidence Act
were downloaded as additional reference material but are not indexed and are optional in
a clean copy. The assessment brief is development material, not a retrieval source.

These files are downloaded originals. Their amendment coverage has not been certified.
The CrPC archive is an older base edition. This phase uses English questions, answers and source
text. The three Bangla amendment Gazettes were moved to Trash on 2026-09-17 and are excluded
from ingestion. Relevant amendments remain part of the assessment; suitable English amendment
sources and version review are pending. Do not describe these files as a complete current-law corpus.

| PDF | Pages | Official source |
|---|---:|---|
| [crpc-1898-part-1.pdf](crpc-1898-part-1.pdf) | 38 | [Download](http://old.bdlaws.minlaw.gov.bd/upload/bdcodeactpart/2021-11-23-13-51-15-41_1_The_Code_of_Criminal_Procedure-sayed-1.pdf) |
| [crpc-1898-part-2.pdf](crpc-1898-part-2.pdf) | 123 | [Download](http://old.bdlaws.minlaw.gov.bd/upload/bdcodeactpart/2021-11-23-13-52-22-41_2_The_Code_of_Criminal_Procedure-Sayed-2.pdf) |
| [crpc-1898-part-3.pdf](crpc-1898-part-3.pdf) | 120 | [Download](http://old.bdlaws.minlaw.gov.bd/upload/bdcodeactpart/2021-11-23-13-52-49-41_3_The_Code_of_Criminal_Procedure-Sayed-3.pdf) |
| [crpc-1898-part-4.pdf](crpc-1898-part-4.pdf) | 160 | [Download](http://old.bdlaws.minlaw.gov.bd/upload/bdcodeactpart/2021-11-23-13-53-25-41_4_The_Code_of_Criminal_Procedure-Sayed-4.pdf) |
| `penal-code-1860.pdf` (optional) | 217 | [Download](http://old.bdlaws.minlaw.gov.bd/upload/bdcodeact/2023-11-30-13-16-59-18.-Penal-code.pdf) |
| `evidence-act-1872.pdf` (optional) | 92 | [Download](http://old.bdlaws.minlaw.gov.bd/upload/bdcodeact/2023-12-03-10-51-51-31.-THE-EVIDENCE-ACT.pdf) |

Original downloaded collection: 6 PDFs, 750 pages. The runnable CrPC copy uses 4 PDFs, 441 pages. The five-page extraction sample covers ordinary
CrPC text, footnotes, a provision continuing across pages and a schedule table.

The five sample pages were compared with the rendered PDFs on 2026-09-17. The extractor now
normalizes font symbols, separates margin headings/footnotes, preserves section 32's column pairs
and joins section 173 across PDF pages 98-99. All 11 footnotes retain traceable references. Partial
sections and the schedule table are explicitly marked. The originals remain unchanged.

The sample-specific layout rules cover only the selected pages. The separate full-corpus
`ingest.py` now validates all four CrPC parts and prepares section/range records from the
statutory body in Parts 2 and 3. Part 1 contents/index parsing, Part 4 table/form parsing and
current amendment coverage remain pending. See [../tracker.txt](../tracker.txt) for details.

The legacy Bangladesh Code archive was downloaded over HTTP because its HTTPS endpoint was unavailable. Local checksums can detect changes to the saved files; they do not certify legal currency.

## File checksums and download times

- **crpc-1898-part-1.pdf**
  - Downloaded: 2026-09-16T18:19:20.215342+00:00
  - SHA-256: `d343152757946aec345a81c6c92c254338e4066f0acd205b8b8574555914218c`
- **crpc-1898-part-2.pdf**
  - Downloaded: 2026-09-16T18:19:20.494218+00:00
  - SHA-256: `691ae36aa388b46396c7c51e344beb2b162c31d9c36ededa8c34c70467bc8ff0`
- **crpc-1898-part-3.pdf**
  - Downloaded: 2026-09-16T18:19:20.609298+00:00
  - SHA-256: `f13e13b04fb4bd9f7fc13a77897d2572a0373d9f84d78f527af5bbecfa6bb1f8`
- **crpc-1898-part-4.pdf**
  - Downloaded: 2026-09-16T18:19:21.285036+00:00
  - SHA-256: `71902dd0f7746298e3c950a2fb407d675f4a1adbf84ecc8bc8b541692972b627`
- **penal-code-1860.pdf**
  - Downloaded: 2026-09-16T18:19:58.571987+00:00
  - SHA-256: `1bdcaaa4ea7b1b93e04450aeb4089e2a5272ebcbda30e2b06cf75bdc020821b7`
- **evidence-act-1872.pdf**
  - Downloaded: 2026-09-16T18:16:42.428404+00:00
  - SHA-256: `66d33bdfbf99ef2d396f8e9a8a58ccce230e2f5b32350b787276784ccc233d59`
