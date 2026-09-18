# AI Usage

AI tools were used during development as supporting assistants for coding, debugging, documentation, and evaluation review. The production application also uses hosted model services for conversational routing, grounded answer generation, and embeddings. This document summarizes where AI was used and how AI-generated suggestions were reviewed before being kept.

## Tools used

| Tool | Purpose |
|---|---|
| OpenAI Codex / ChatGPT | Help with code drafting, debugging, documentation wording, and reviewing evaluation results |
| Groq `openai/gpt-oss-120b` | Runtime conversational routing and grounded answer generation |
| Gemini `gemini-embedding-2` | Document and query embeddings |

## How AI was used

AI assistance was used to:

- suggest or revise small code changes;
- help debug implementation issues;
- improve documentation wording and structure;
- review evaluation outputs and identify possible failure patterns.

AI-generated suggestions were reviewed before being accepted. Some suggestions were rejected or reverted when they did not fit the project well. For example, a two-pass LLM evidence-audit approach was dropped because it increased provider usage and caused quota problems.
