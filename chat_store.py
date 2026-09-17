"""Persistent conversation storage for the legal assistant."""

from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


VALID_ROLES = {"user", "assistant"}
VALID_STATUSES = {"answer", "clarify", "insufficient_evidence"}


class ChatStoreError(RuntimeError):
    pass


def _uuid(value, name):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid {name}") from exc


def ensure_tables(database_url):
    """Create conversation tables and indexes if they do not already exist."""
    if not database_url:
        raise ChatStoreError("DATABASE_URL is not configured.")

    statements = [
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id UUID PRIMARY KEY,
            client_id UUID NOT NULL,
            title TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_conversations_client_updated
        ON conversations (client_id, updated_at DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
                REFERENCES conversations(id)
                ON DELETE CASCADE,
            role TEXT NOT NULL
                CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            status TEXT
                CHECK (
                    status IS NULL OR
                    status IN (
                        'answer',
                        'clarify',
                        'insufficient_evidence'
                    )
                ),
            citations JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
        ON messages (conversation_id, created_at)
        """,
    ]

    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
    except psycopg.Error as exc:
        raise ChatStoreError(
            "Could not initialize conversation storage."
        ) from exc


def create_conversation(database_url, client_id, title):
    """Create and return a new conversation owned by client_id."""
    client_id = _uuid(client_id, "client_id")

    title = " ".join(str(title).split()).strip()
    if not title:
        title = "New conversation"

    title = title[:120]
    conversation_id = uuid4()

    try:
        with psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            row = conn.execute(
                """
                INSERT INTO conversations (id, client_id, title)
                VALUES (%s, %s, %s)
                RETURNING id, client_id, title, created_at, updated_at
                """,
                (conversation_id, client_id, title),
            ).fetchone()

        return dict(row)

    except psycopg.Error as exc:
        raise ChatStoreError("Could not create conversation.") from exc


def list_conversations(database_url, client_id, limit=30):
    """Return the client's conversations, newest activity first."""
    client_id = _uuid(client_id, "client_id")
    limit = max(1, min(int(limit), 100))

    try:
        with psycopg.connect(
            database_url,
            row_factory=dict_row,
        ) as conn:
            rows = conn.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM conversations
                WHERE client_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (client_id, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    except psycopg.Error as exc:
        raise ChatStoreError("Could not load conversations.") from exc


def get_owned_conversation(database_url, client_id, conversation_id):
    """Return conversation metadata only when it belongs to client_id."""
    client_id = _uuid(client_id, "client_id")
    conversation_id = _uuid(conversation_id, "conversation_id")

    try:
        with psycopg.connect(
            database_url,
            row_factory=dict_row,
        ) as conn:
            row = conn.execute(
                """
                SELECT id, client_id, title, created_at, updated_at
                FROM conversations
                WHERE id = %s AND client_id = %s
                """,
                (conversation_id, client_id),
            ).fetchone()

        return None if row is None else dict(row)

    except psycopg.Error as exc:
        raise ChatStoreError("Could not load conversation.") from exc


def get_recent_messages(
    database_url,
    client_id,
    conversation_id,
    limit=8,
):
    """Return recent messages for an owned conversation in chronological order.

    Returns None when the conversation does not exist for this client.
    """
    client_id = _uuid(client_id, "client_id")
    conversation_id = _uuid(conversation_id, "conversation_id")
    limit = max(1, min(int(limit), 20))

    try:
        with psycopg.connect(
            database_url,
            row_factory=dict_row,
        ) as conn:
            owner = conn.execute(
                """
                SELECT 1
                FROM conversations
                WHERE id = %s AND client_id = %s
                """,
                (conversation_id, client_id),
            ).fetchone()

            if owner is None:
                return None

            rows = conn.execute(
                """
                SELECT id, role, content, status, citations, created_at
                FROM (
                    SELECT id, role, content, status, citations, created_at
                    FROM messages
                    WHERE conversation_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                ) recent
                ORDER BY created_at ASC, id ASC
                """,
                (conversation_id, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    except psycopg.Error as exc:
        raise ChatStoreError("Could not load recent messages.") from exc


def get_conversation(database_url, client_id, conversation_id):
    """Return one conversation and all of its messages."""
    client_id = _uuid(client_id, "client_id")
    conversation_id = _uuid(conversation_id, "conversation_id")

    try:
        with psycopg.connect(
            database_url,
            row_factory=dict_row,
        ) as conn:
            conversation = conn.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM conversations
                WHERE id = %s AND client_id = %s
                """,
                (conversation_id, client_id),
            ).fetchone()

            if conversation is None:
                return None

            messages = conn.execute(
                """
                SELECT id, role, content, status, citations, created_at
                FROM messages
                WHERE conversation_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (conversation_id,),
            ).fetchall()

        result = dict(conversation)
        result["messages"] = [dict(row) for row in messages]
        return result

    except psycopg.Error as exc:
        raise ChatStoreError("Could not load conversation.") from exc


def add_message(
    database_url,
    client_id,
    conversation_id,
    role,
    content,
    status=None,
    citations=None,
):
    """Append a message after verifying conversation ownership."""
    client_id = _uuid(client_id, "client_id")
    conversation_id = _uuid(conversation_id, "conversation_id")

    if role not in VALID_ROLES:
        raise ValueError("Invalid message role")

    content = str(content).strip()
    if not content:
        raise ValueError("Message content cannot be empty")

    if status is not None and status not in VALID_STATUSES:
        raise ValueError("Invalid assistant status")

    if role == "user" and status is not None:
        raise ValueError("User messages cannot have an assistant status")

    if citations is not None and not isinstance(citations, list):
        raise ValueError("Citations must be a list")

    message_id = uuid4()

    try:
        with psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
        ) as conn:
            owner = conn.execute(
                """
                SELECT 1
                FROM conversations
                WHERE id = %s AND client_id = %s
                """,
                (conversation_id, client_id),
            ).fetchone()

            if owner is None:
                return None

            row = conn.execute(
                """
                INSERT INTO messages (
                    id,
                    conversation_id,
                    role,
                    content,
                    status,
                    citations
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING
                    id,
                    conversation_id,
                    role,
                    content,
                    status,
                    citations,
                    created_at
                """,
                (
                    message_id,
                    conversation_id,
                    role,
                    content,
                    status,
                    Jsonb(citations) if citations is not None else None,
                ),
            ).fetchone()

            conn.execute(
                """
                UPDATE conversations
                SET updated_at = NOW()
                WHERE id = %s
                """,
                (conversation_id,),
            )

        return dict(row)

    except psycopg.Error as exc:
        raise ChatStoreError("Could not save message.") from exc
