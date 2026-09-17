"""Flask API. Run with: python3 app.py"""

import json
import os
from pathlib import Path

import httpx
import rag
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from werkzeug.exceptions import BadRequest, HTTPException


load_dotenv(Path(__file__).with_name(".env"), override=False)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.config["GROQ_API_KEY"] = os.environ.get("GROQ_API_KEY", "").strip()
app.config["GROQ_MODEL"] = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b").strip()
app.config["DATABASE_URL"] = os.environ.get("DATABASE_URL", "").strip()
app.config["GEMINI_API_KEY"] = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL_PROVIDER = "groq"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_QUESTION_LENGTH = 4000
MAX_OUTPUT_TOKENS = 2048
TEMPERATURE = 0
HTTP_TIMEOUT_MS = 30_000
REASONING_EFFORT = "low"
DISCLAIMER = "Legal information only; not legal advice."
ANSWER_STATUSES = ("answer", "clarify", "insufficient_evidence")
SYSTEM_INSTRUCTION = """You provide Bangladesh criminal-law information in plain English.
This is an LLM-only baseline: no documents, retrieved passages, web search or case
records are available. Give a concise general explanation when confident, while
making clear that it has not been checked against source documents or current
amendments. Never claim to have read a PDF or verified current law. Do not invent
source quotations, page references, case facts, fees or deadlines.
Use status 'clarify' and ask a focused question when essential details are missing.
Use 'insufficient_evidence' and explain the gap when you cannot support an answer,
when current-law verification or private case records are required, or when the
question is outside Bangladesh criminal law. Otherwise use 'answer'. Provide
legal information, not personalized legal advice. Keep the answer under 200 words.
"""
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(ANSWER_STATUSES)},
        "answer": {"type": "string"},
    },
    "required": ["status", "answer"],
    "additionalProperties": False,
}


def generate_answer(question, *, system_instruction=SYSTEM_INSTRUCTION, metadata=None, evidence=None):
    schema = ANSWER_SCHEMA
    if evidence is not None:
        schema = {**ANSWER_SCHEMA, "properties": {**ANSWER_SCHEMA["properties"], "references": rag.REFERENCE_SCHEMA},
                  "required": ["status", "answer", "references"]}
        system_instruction = rag.INSTRUCTION
        question = json.dumps({"question": question, "source_version": rag.embed.SOURCE_VERSION,
                               "archive_current_law_verified": False, "passages": evidence})
    # One stateless generation call; credentials never enter the model prompt.
    # Use the existing HTTP dependency; no provider SDK or new module is needed.
    with httpx.Client(timeout=HTTP_TIMEOUT_MS / 1000) as client:
        response = client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {app.config['GROQ_API_KEY']}"},
            json={
                "model": app.config["GROQ_MODEL"],
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": question},
                ],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "legal_answer", "strict": True, "schema": schema,
                }},
                "max_completion_tokens": MAX_OUTPUT_TOKENS,
                "temperature": TEMPERATURE,
                "reasoning_effort": REASONING_EFFORT,
                "include_reasoning": False,
                "stream": False,
            },
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Invalid provider response")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("Missing model response")
    choice = choices[0]
    if metadata is not None:
        metadata["model_version"] = payload.get("model")
        usage = payload.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("Invalid usage metadata")
        completion = (usage or {}).get("completion_tokens")
        reasoning = ((usage or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
        # Groq completion tokens include reasoning; do not count reasoning twice.
        visible = completion - reasoning if (
            type(completion) is int and type(reasoning) is int and 0 <= reasoning <= completion
        ) else None
        metadata["usage"] = None if usage is None else {
            "prompt_token_count": usage.get("prompt_tokens"),
            "candidates_token_count": visible,
            "thoughts_token_count": reasoning,
            "output_token_count_including_reasoning": completion,
            "cached_content_token_count": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            "total_token_count": usage.get("total_tokens"),
        }
        metadata["finish_reason"] = choice.get("finish_reason")
    # Do not serve blocked or truncated responses as successful answers.
    message = choice.get("message")
    if choice.get("finish_reason") != "stop" or not isinstance(message, dict):
        raise ValueError("No complete model response")
    result = json.loads(message.get("content") or "")
    if metadata is not None:
        metadata["model_response"] = result
    if (
        not isinstance(result, dict)
        or set(result) != ({"status", "answer", "references"} if evidence is not None else {"status", "answer"})
        or result.get("status") not in ANSWER_STATUSES
        or not isinstance(result.get("answer"), str)
        or not result["answer"].strip()
    ):
        raise ValueError("Invalid model response")
    answer, citations = result["answer"].strip(), []
    if evidence is not None:
        answer, citations = rag.resolve_references(result, evidence)
    # Citation metadata is resolved from retrieved records, not model-generated paths/pages.
    return {
        "status": result["status"],
        "answer": answer,
        "citations": citations,
        "source_version": rag.embed.SOURCE_VERSION if evidence is not None else None,
        "error": None,
        "disclaimer": DISCLAIMER + (" Based on an archived edition; current amendments are not verified." if evidence is not None else ""),
    }


def error_body(code, message):
    return {
        "status": "error",
        "answer": None,
        "citations": [],
        "source_version": None,
        "error": {"code": code, "message": message},
        "disclaimer": DISCLAIMER,
    }


def api_error(code, message, status):
    return jsonify(error_body(code, message)), status


@app.errorhandler(HTTPException)
def http_error(error):
    # Keep HTTP headers such as Allow while returning JSON instead of an HTML page.
    response = error.get_response()
    code = error.name.lower().replace(" ", "_")
    response.set_data(app.json.dumps(error_body(code, error.description)))
    response.content_type = "application/json"
    return response


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/ask")
def ask():
    if not request.is_json:
        return api_error("unsupported_media_type", "Send the request as application/json.", 415)
    try:
        payload = request.get_json()
    except BadRequest:
        return api_error("invalid_json", "The request body must contain valid JSON.", 400)

    if not isinstance(payload, dict):
        return api_error("invalid_request", "The JSON body must be an object containing question.", 400)
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        return api_error("invalid_question", "question must be a nonempty string.", 400)
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        return api_error("question_too_long", f"question must be at most {MAX_QUESTION_LENGTH} characters.", 400)

    metadata = {}
    body, status = answer_question(question, metadata=metadata, grounded=True)
    response = jsonify(body)
    if "retry_after_seconds" in metadata:
        response.headers["Retry-After"] = str(metadata["retry_after_seconds"])
    return response, status


def answer_question(question, *, system_instruction=SYSTEM_INSTRUCTION, metadata=None, grounded=False):
    """API uses grounded=True; the historical evaluation runner retains its baseline."""
    def failure(code, message, status):
        return error_body(code, message), status

    if not app.config["GROQ_API_KEY"]:
        return failure("model_not_configured", "Set GROQ_API_KEY in the server's .env file and restart.", 503)
    try:
        evidence = None
        if grounded:
            evidence = rag.retrieve(question, app.config["DATABASE_URL"], app.config["GEMINI_API_KEY"])
            if metadata is not None:
                metadata["retrieved_evidence"] = evidence
                metadata["retrieved_evidence_ids"] = [p["id"] for p in evidence]
                metadata["retrieved_sections"] = list(dict.fromkeys(p["section"] for p in evidence))
            if not evidence:
                return {"status": "insufficient_evidence", "answer": "The available archive does not provide enough information to answer this question.",
                        "citations": [], "source_version": rag.embed.SOURCE_VERSION, "error": None, "disclaimer": DISCLAIMER}, 200
        return generate_answer(question, system_instruction=system_instruction, metadata=metadata, evidence=evidence), 200
    except rag.RetrievalError as error:
        if metadata is not None:
            metadata["retrieval_error"] = str(error)
        app.logger.warning("Retrieval failed: %s", error)
        return failure("retrieval_unavailable", str(error), 503)
    except httpx.HTTPStatusError as error:
        # Never expose raw provider errors, request headers or credentials.
        code = error.response.status_code
        if metadata is not None:
            metadata["provider_http_status"] = code
            retry_after = error.response.headers.get("retry-after", "")
            if code in (429, 503) and retry_after.isascii() and retry_after.isdigit() and len(retry_after) <= 5:
                metadata["retry_after_seconds"] = int(retry_after)
        if code in (401, 403):
            return failure("model_auth_failed", "Groq authentication failed. Check GROQ_API_KEY and model permissions.", 503)
        if code == 404:
            return failure("model_not_found", "The configured Groq model is unavailable. Check GROQ_MODEL.", 503)
        if code == 429:
            return failure("model_rate_limited", "Groq free-tier or account quota reached. Wait for the applicable limit to reset.", 503)
        if code in (400, 422):
            return failure("model_request_rejected", "Groq rejected the request. Check the model and request configuration.", 502)
        return failure("model_unavailable", "Groq is temporarily unavailable. Try again later.", 502)
    except httpx.TimeoutException:
        return failure("model_timeout", "Groq did not respond in time. Try again later.", 504)
    except httpx.RequestError:
        return failure("model_connection_failed", "Could not connect to Groq. Check the server network connection.", 502)
    except (ValueError, TypeError, AttributeError) as error:
        if metadata is not None:
            metadata["validation_error"] = type(error).__name__ + ": " + str(error)[:300]
        return failure("invalid_model_response", "Groq did not return a complete valid answer. Try again.", 502)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), debug=False)
