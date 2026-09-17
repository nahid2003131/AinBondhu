"""Create resumable section embeddings in the existing Railway PostgreSQL database.

    .venv/bin/python embed.py --check
    .venv/bin/python embed.py --load
    .venv/bin/python embed.py --search "Can I get a copy of the police report?"
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time

import httpx
import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

from ingest import SOURCE_VERSION

MODEL = "gemini-embedding-2"
DIMENSIONS = 768
PROFILE = f"{MODEL}:{DIMENSIONS}:document-v1"
BASE_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}"
BATCH_SIZE = 25
PAUSE_SECONDS = 15


class EmbeddingAPIError(RuntimeError):
    """Safe provider failure message, without credentials or raw response bodies."""


def document_text(row):
    return f"title: {row['law_code']}, section {row['section_ref']} | text: {row['content']}"


def api_post(client, method, payload):
    response = client.post(BASE_URL + ':' + method, json=payload)
    if response.status_code == 429:
        raise EmbeddingAPIError("Gemini embedding quota reached. Wait for the quota to reset before retrying.")
    if response.status_code in (401, 403):
        raise EmbeddingAPIError("Gemini rejected the key or model access; check GEMINI_API_KEY.")
    if not response.is_success:
        raise EmbeddingAPIError(f"Gemini embedding API returned HTTP {response.status_code}. Try again later.")
    return response.json()


def make_vectors(client, texts):
    # Synchronous standard endpoint; this is not Google's paid asynchronous Batch API.
    data = api_post(client, 'batchEmbedContents', {'requests': [
        {'model': f'models/{MODEL}', 'content': {'parts': [{'text': text}]},
         'outputDimensionality': DIMENSIONS} for text in texts
    ]})
    vectors = [e.get('values') for e in data.get('embeddings', [])]
    if len(vectors) != len(texts):
        raise RuntimeError("Wrong number of embedding vectors; batch was not saved.")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != DIMENSIONS or not all(
                type(v) in (float, int) and math.isfinite(v) for v in vector):
            raise RuntimeError("Invalid embedding vector; batch was not saved.")
        if sum(v * v for v in vector) < 1e-12:
            raise RuntimeError("Zero embedding vector; batch was not saved.")
    return vectors


def ensure_schema(conn):
    with conn.transaction():
        conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
        conn.execute('ALTER TABLE legal_sections ADD COLUMN IF NOT EXISTS embedding vector(768)')
        conn.execute('ALTER TABLE legal_sections ADD COLUMN IF NOT EXISTS embedding_profile TEXT')
        conn.execute('ALTER TABLE legal_sections ADD COLUMN IF NOT EXISTS embedding_content_sha256 CHAR(64)')
        conn.execute('ALTER TABLE legal_sections ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ')


def fetch_sections(conn):
    rows = conn.execute('''SELECT source_version,section_ref,law_code,content,content_sha256
                          FROM legal_sections WHERE source_version=%s ORDER BY section_ref''',
                        (SOURCE_VERSION,)).fetchall()
    if not rows:
        raise RuntimeError("No source records. Run ingest.py --load first.")
    for row in rows:
        if hashlib.sha256(row['content'].encode()).hexdigest() != row['content_sha256']:
            raise RuntimeError("Stored source checksum does not match its text.")
    return rows


def pending_sections(conn, rows):
    exists = conn.execute('''SELECT 1 FROM information_schema.columns
                            WHERE table_schema=current_schema() AND table_name='legal_sections'
                            AND column_name='embedding' ''').fetchone()
    if not exists:
        return rows
    ready = conn.execute('''SELECT section_ref FROM legal_sections
                           WHERE source_version=%s AND embedding IS NOT NULL
                           AND embedding_profile=%s AND embedding_content_sha256=content_sha256''',
                         (SOURCE_VERSION, PROFILE)).fetchall()
    ready = {row['section_ref'] for row in ready}
    return [row for row in rows if row['section_ref'] not in ready]


def check_lengths(client, rows):
    # A byte ceiling is deliberately conservative for these English source texts.
    # Longer inputs are counted by the actual embedding model, never silently cut.
    for row in rows:
        text = document_text(row)
        if len(text.encode('utf-8')) > 7600:
            count = api_post(client, 'countTokens', {'contents': [{'parts': [{'text': text}]}]}).get('totalTokens')
            if type(count) is not int or count > 8192:
                raise RuntimeError(f"Section {row['section_ref']} exceeds model input limit; split it before embedding.")
            print(f"Long section {row['section_ref']}: {count}/8192 input tokens.", flush=True)


def load_embeddings(conn, client, rows, pause=PAUSE_SECONDS):
    pending = pending_sections(conn, rows)
    print(f"Sections: {len(rows)}; already embedded: {len(rows)-len(pending)}; pending: {len(pending)}.", flush=True)
    if not pending:
        return
    check_lengths(client, pending)
    ensure_schema(conn)
    for start in range(0, len(pending), BATCH_SIZE):
        if start:
            time.sleep(pause)
        batch = pending[start:start+BATCH_SIZE]
        vectors = make_vectors(client, [document_text(row) for row in batch])
        with conn.transaction():
            for row, vector in zip(batch, vectors):
                updated = conn.execute('''UPDATE legal_sections
                    SET embedding=%s::vector, embedding_profile=%s,
                        embedding_content_sha256=%s, embedded_at=now()
                    WHERE source_version=%s AND section_ref=%s AND content_sha256=%s''',
                    (json.dumps(vector), PROFILE, row['content_sha256'], row['source_version'],
                     row['section_ref'], row['content_sha256']))
                if updated.rowcount != 1:
                    raise RuntimeError("Source changed during embedding; rerun against the current source.")
        print(f"Saved {min(start+BATCH_SIZE,len(pending))}/{len(pending)} pending embeddings.", flush=True)
    if pending_sections(conn, fetch_sections(conn)):
        raise RuntimeError("Some source rows still need embeddings.")
    print(f"Complete: {len(rows)} section embeddings stored in Railway PostgreSQL.", flush=True)


def search(conn, client, question, limit=5):
    if not question.strip() or len(question) > 4000:
        raise ValueError("Question must be between 1 and 4000 characters.")
    rows = fetch_sections(conn)
    if pending_sections(conn, rows):
        raise RuntimeError("Corpus embeddings are incomplete. Finish --load before searching.")
    vector = make_vectors(client, [f"task: question answering | query: {question.strip()}"])[0]
    # Exact cosine search is sufficient for 515 vectors. Similarity is not accuracy.
    return conn.execute('''SELECT section_ref,source_file,pdf_pages,printed_pages,content,fragments,
                           1-(embedding <=> %s::vector) AS similarity
                           FROM legal_sections WHERE source_version=%s AND embedding_profile=%s
                           AND embedding_content_sha256=content_sha256 AND embedding IS NOT NULL
                           ORDER BY embedding <=> %s::vector,section_ref LIMIT %s''',
                        (json.dumps(vector), SOURCE_VERSION, PROFILE, json.dumps(vector), limit)).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--load', action='store_true')
    mode.add_argument('--search', metavar='QUESTION')
    args = parser.parse_args()
    load_dotenv(Path(__file__).with_name('.env'))
    url = os.getenv('DATABASE_URL', '').strip()
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if not url:
        raise RuntimeError("Set DATABASE_URL in .env.")
    if not args.check and not key:
        raise RuntimeError("Set GEMINI_API_KEY in .env.")
    with psycopg.connect(url, connect_timeout=15, autocommit=True, row_factory=dict_row,
                         options='-c statement_timeout=60000') as conn:
        rows = fetch_sections(conn)
        if args.check:
            pending = pending_sections(conn, rows)
            print(f"Sections: {len(rows)}; embedded: {len(rows)-len(pending)}; pending: {len(pending)}.")
            print(f"Model: {MODEL}; dimensions: {DIMENSIONS}. No API calls or database writes.")
            return
        with httpx.Client(headers={'x-goog-api-key': key}, timeout=45) as client:
            if args.load:
                load_embeddings(conn, client, rows)
            else:
                for row in search(conn, client, args.search):
                    print(f"Section {row['section_ref']} | {row['source_file']} | PDF {row['pdf_pages']} | similarity {row['similarity']:.4f}")


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"Error: {exc}") from None
    except httpx.RequestError:
        raise SystemExit("Embedding connection failed. Saved batches remain; rerun to resume.") from None
    except psycopg.Error:
        raise SystemExit("Database operation failed. Check Railway connectivity; rerun to resume.") from None
