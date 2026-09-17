# Backend-first plan

## Current scope

English questions answered from archived Bangladesh CrPC text using Flask, Groq,
Gemini embeddings and Railway PostgreSQL/pgvector. Keep one app and one database.
Current files and their responsibilities are listed in README.md.

## Completed

- Checked source extraction and loaded 515 section/range records and embeddings.
- Connected /ask to retrieval, answer generation and backend-supplied references.
- Prepared the current 20-question evaluation and recorded grounded-v0.3.json.
- Verified the clean local copy with a fresh environment and live API request.
- Added setup documentation, progress tracking and AI_USAGE.md.
- Added conversational routing and a white-theme browser workspace with PDF references.

## Current result

20/20 responses, zero request errors. Provisional semantic review: 9/14 complete
answers, 7/14 complete answers with supporting references. A response is not proof
of correctness. Older test files were removed; the current dataset/results remain.

## Next work

1. Keep evaluation.json fixed. Improve source selection for Q10/Q11, preserve
   conditions and handle clarification/current-law requests correctly.
2. Include relevant footnotes and reviewed amendments with traceable versions.
3. Save a new evaluation separately; compare the same questions and configurations.
4. Demonstrate adding/replacing a document and updating only affected embeddings.
5. Review the browser workspace, then deploy the application or record a demo video.
6. Record subsequent changes as focused commits; publish when ready. The current
   implementation is one checkpoint, not a reconstruction of earlier development history.

## Limits

The PDFs are not verified current law. Footnotes are stored but not passed to the
answer model. Schedules/forms and other Acts are not indexed. A full incremental
update demo and production deployment remain pending. Hybrid retrieval/reranking
should follow measured needs rather than be added automatically.

## Local setup

Follow README.md from this folder. .env and .venv are local and ignored by Git.
Reuse the prepared Railway database; copying the project does not require re-embedding.
