# AI usage

Updated: 2026-09-17. Covers evaluation version 0.3, conversational routing and browser UI.

Project scope, framework choices and revisions were directed by the developer,
who also configured service accounts, ran manual tests and reviewed outputs.

## Tools used

| Tool | Purpose |
|---|---|
| OpenAI Codex | Development assistance: code and document drafting/edits, debugging, test execution and provisional evaluation review. |
| Groq `openai/gpt-oss-120b` | Conversational routing and answer generation; earlier recorded experiments used generation only. |
| Gemini `gemini-embedding-2` | Document and query embeddings. |
| Gemini generation API | Earlier generation experiments, documented as historical controls. |

Tasks delegated for assistance covered PDF extraction, the Flask backend,
ingestion/retrieval, citation handling, evaluation scripts and documentation. Legal source PDFs were
not AI-generated. Gold answers and reference selections were excluded from model
prompts and retrieval embeddings.

## Review process

- Checked PDF pages, source hashes, section boundaries and database readback.
- Ran local HTTP checks, provider/error checks and the 23-test offline suite.
- Saved evaluation attempts, settings, retrieved evidence and failures separately.
- Reviewed answer meaning, conditions and source support rather than exact wording.
  Semantic scores are provisional AI-assisted rubric reviews, not a legal audit.


## Corrections made during development

- Reduced the initial scaffold to the requested minimal structure.
- Removed section-number clues from questions and corrected outdated expectations.
- Separated missing-evidence controls from the actual LLM-only baseline.
- Fixed extraction boundaries and continuations before embedding.
- Replaced model-written exact quotes with source IDs and stored excerpts.
- Scored response delivery, answer correctness and citation support separately.
- Added the developer-requested white frontend to the existing Flask app; checked
  static assets and public-PDF access. Browser interaction/visual review is pending.

## Remaining limitations

Incorrect references, omitted conditions and clarification failures remain in the
recorded results. Current amendments are not verified, and relevant footnotes are
not yet supplied to the answer model. See [README.md](README.md) and
[grounded-v0.3.json](grounded-v0.3.json) for results and pending work.
