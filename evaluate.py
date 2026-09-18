"""Evaluate the current grounded pipeline or a new LLM-only comparison."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import tempfile
import time

import app as backend

ROOT = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def build_instruction(dataset, mode="grounded"):
    if mode == "llm-only":
        # No source metadata, passages or gold references.
        return backend.SYSTEM_INSTRUCTION
    if mode == "grounded":
        return backend.rag.INSTRUCTION
    raise ValueError("Unknown evaluation mode")


def rate(numerator, denominator):
    return {"passed": numerator, "total": denominator,
            "percent": round(100 * numerator / denominator, 2) if denominator else None}


def review_ready(result, case):
    review = result.get("review") or {}
    points = review.get("required_points_met", [])
    return (
        len(points) == len(case["required_points"])
        and all(type(value) is bool for value in points)
        and type(review.get("behavior_correct")) is bool
        and isinstance(review.get("forbidden_claims_made"), list)
        and isinstance(review.get("material_errors"), list)
        and bool(review.get("reviewer"))
        and bool(review.get("notes"))
    )


def retrieval_metrics(run, dataset):
    """Separate quote provenance, evidence coverage, and semantic review."""
    cases = {case["id"]: case for case in dataset["cases"]}
    citations, gold, support, citation_support = [], [], [], []
    for result in run["results"]:
        evidence = result["metadata"].get("retrieved_evidence", [])
        checks = []
        for citation in result["response"]["citations"]:
            valid = any(
                citation["document"] == item["document"]
                and citation["section"] == item["section"]
                and citation["pdf_pages"] == [item["pdf_page"]]
                and citation["printed_pages"] == [item["printed_page"]]
                and citation["excerpt"] in item["text"]
                for item in evidence
            )
            checks.append(valid)
            citations.append(valid)
        result["citation_quote_checks"] = checks
        case = cases[result["id"]]
        if case["sources"]:
            checks = []
            for source in case["sources"]:
                section = source["section"].split("(")[0].strip()
                text = " ".join(
                    item["text"] for item in evidence
                    if item["document"] == Path(source["file"]).name
                    and item["section"] == section
                    and item["pdf_page"] in source["pdf_pages"]
                )
                checks.extend(" ".join(snippet.split()) in text for snippet in source["evidence"])
            result["gold_evidence_retrieved"] = all(checks)
            gold.append(all(checks))
        else:
            result["gold_evidence_retrieved"] = None
        if result["http_status"] == 200 and result["response"]["status"] == "answer":
            supported = result["review"].get("all_substantive_claims_supported")
            if type(supported) is bool:
                support.append(supported)
            supported_citations = result["review"].get("citations_support_answer")
            if type(supported_citations) is bool:
                citation_support.append(supported_citations)
    comparable = run["results"]
    answer_results = [result for result in run["results"]
                      if cases[result["id"]]["expected_behavior"] == "answer"]
    reviewed_citations = all("citations_support_answer" in result["review"] for result in answer_results)
    correct_and_cited = sum(
        result["http_status"] == 200
        and all(result["review"]["required_points_met"])
        and not result["review"]["forbidden_claims_made"]
        and not result["review"]["material_errors"]
        and result["review"].get("citations_support_answer") is True
        for result in answer_results
    )
    return {
        "citation_quote_validity": rate(sum(citations), len(citations)),
        "gold_evidence_retrieval_case_coverage": rate(sum(gold), len(gold)),
        "groundedness_answer_level": rate(sum(support), len(support)),
        "semantic_citation_support_answer_level": rate(sum(citation_support), len(citation_support)),
        "strict_answers_with_supporting_citations": rate(correct_and_cited, len(answer_results))
            if reviewed_citations else None,
        "behavior_accuracy_comparable_cases": rate(
            sum(result["review"]["behavior_correct"] for result in comparable), len(comparable)),
        "non_comparable_cases": {},
        "metric_limits": "Quote validity is backend-gated provenance, not claim correctness. "
            "Groundedness is provisional response-level rubric review, not an independent legal audit. "
            "This small development set concentrates on a few archived provisions and is not held out.",
    }


def summarize(run, dataset):
    cases = {case["id"]: case for case in dataset["cases"]}
    results = run["results"]
    by_id = {result["id"]: result for result in results}
    reviewed = [result for result in results if review_ready(result, cases[result["id"]])]
    answer_cases = [case for case in cases.values() if case["expected_behavior"] == "answer"]
    answer_reviews_complete = all(
        case["id"] in by_id and review_ready(by_id[case["id"]], case) for case in answer_cases
    )
    strict_passes = points_met = points_total = 0
    if answer_reviews_complete:
        for case in answer_cases:
            result = by_id[case["id"]]
            review = result["review"]
            points = review["required_points_met"]
            points_met += sum(points)
            points_total += len(points)
            strict_passes += bool(
                result["http_status"] == 200 and all(points)
                and not review["forbidden_claims_made"] and not review["material_errors"]
            )
    successful = [result for result in results if result["http_status"] == 200]
    latencies = [result["latency_seconds"] for result in successful]
    usage = [result["metadata"].get("usage") for result in results]
    token_totals = {}
    for name in ("prompt_token_count", "candidates_token_count", "thoughts_token_count",
                 "output_token_count_including_reasoning", "total_token_count"):
        counts = [item[name] for item in usage if item and item.get(name) is not None]
        token_totals[name] = {"reported_sum": sum(counts) if counts else None,
                              "requests_with_count": len(counts)}
    summary = {
        "planned_questions": len(cases), "attempted": len(results),
        "successful_requests": len(successful), "errors": len(results) - len(successful),
        "reviewed_cases": len(reviewed),
        "status_label_match": rate(sum(result["status_matches_expected"] for result in results), len(results)),
        "behavior_accuracy": rate(sum(result["review"]["behavior_correct"] for result in reviewed), len(cases))
            if len(reviewed) == len(cases) else None,
        "strict_answer_correctness": rate(strict_passes, len(answer_cases)) if answer_reviews_complete else None,
        "required_point_coverage_answer_cases": rate(points_met, points_total) if answer_reviews_complete else None,
        "groundedness": None, "citation_accuracy": None,
        "not_applicable_reason": None if run["experiment"] == "grounded" else "No retrieved passages or generated PDF citations in this baseline.",
        "successful_request_latency_seconds": {
            "mean": round(statistics.mean(latencies), 3) if latencies else None,
            "median": round(statistics.median(latencies), 3) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "token_usage": token_totals,
        "cost": None,
        "cost_note": "Token counts are recorded; monetary cost is not inferred from unverified pricing.",
    }


    if run["experiment"] == "grounded" and len(reviewed) == len(cases):
        summary.update(retrieval_metrics(run, dataset))
    return summary


def save(path, run, dataset):
    run["updated_at"] = now()
    run["summary"] = summarize(run, dataset)
    # Replace atomically so interruption cannot leave a half-written result file.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        json.dump(run, file, ensure_ascii=False, indent=2)
        file.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("grounded", "llm-only"), default="grounded",
                        help="Current grounded pipeline (default), or a new comparison without retrieval.")
    parser.add_argument("--output", type=Path, help="Separate result file; existing results are protected.")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation.json",
                        help="Question dataset; defaults to evaluation.json.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum new requests (1-20).")
    parser.add_argument("--delay", type=int, default=30, help="Seconds between requests (0-60); default 30 for free-tier testing.")
    parser.add_argument("--resume", action="store_true", help="Append only questions not yet attempted.")
    parser.add_argument("--summarize", action="store_true", help="Recalculate saved reviews; makes no API calls.")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error("--limit must be between 1 and 20")
    if not 0 <= args.delay <= 60:
        parser.error("--delay must be between 0 and 60 seconds")
    dataset_path = args.dataset.resolve()
    dataset = json.loads(dataset_path.read_text())
    prefix = "grounded" if args.mode == "grounded" else "llm-only"
    default_output = f"{prefix}-v{dataset['dataset_version']}.json"
    path = (args.output or ROOT / default_output).resolve()
    source = dataset["source_edition"]
    for name, expected in ((source["file"], source["sha256"]),
                           (source["prepared_text"], source["prepared_text_sha256"])):
        if sha256(ROOT / name) != expected:
            parser.error("A frozen source hash changed. Review the dataset before evaluating.")
    instruction = build_instruction(dataset, args.mode)
    configuration = {
        "evaluation_mode": args.mode,
        "dataset_version": dataset["dataset_version"], "dataset_sha256": sha256(dataset_path),
        "app_sha256": sha256(ROOT / "app.py"), "runner_sha256": sha256(Path(__file__)),
        "source_edition_id": source["id"], "provider": backend.MODEL_PROVIDER,
        "model": backend.app.config["GROQ_MODEL"], "reasoning_effort": backend.REASONING_EFFORT,
        "temperature": backend.TEMPERATURE, "max_output_tokens": backend.MAX_OUTPUT_TOKENS,
        "http_timeout_ms": backend.HTTP_TIMEOUT_MS, "attempts_per_question": 1,
        "delay_seconds": args.delay,
        "system_instruction": instruction,
    }
    if args.mode == "grounded" and not args.summarize:
        import psycopg

        source_versions = backend.rag.embed.active_source_versions()
        configuration.update({"rag_sha256": sha256(ROOT / "rag.py"),
                              "routing_instruction": backend.ROUTING_INSTRUCTION,
                              "routing_schema": backend.ROUTING_SCHEMA,
                              "embedding_code_sha256": sha256(ROOT / "embed.py"),
                              "embedding_profile": backend.rag.embed.PROFILE,
                              "retrieval_top_k": 5,
                              "evidence_character_budget": backend.rag.MAX_EVIDENCE_CHARACTERS})
        with psycopg.connect(backend.app.config["DATABASE_URL"], connect_timeout=15) as conn:
            rows = conn.execute(
                """SELECT source_version,section_ref,content_sha256
                   FROM legal_sections
                   WHERE source_version = ANY(%s)
                   ORDER BY source_version,section_ref""",
                (source_versions,),
            ).fetchall()
            source_files = conn.execute(
                """SELECT source_version,source_file,source_sha256
                   FROM source_documents
                   WHERE source_version = ANY(%s)
                   ORDER BY source_version,source_file""",
                (source_versions,),
            ).fetchall()
            configuration["corpus"] = {
                "source_versions": source_versions,
                "section_count": len(rows),
                "section_manifest_sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
                "source_files": source_files,
            }
        configuration = json.loads(json.dumps(configuration))
    if path.exists():
        if not (args.resume or args.summarize):
            parser.error("Output already exists. Use --resume, --summarize, or a new --output filename.")
        run = json.loads(path.read_text())
        if run["configuration"]["dataset_sha256"] != configuration["dataset_sha256"]:
            parser.error("Dataset changed; saved results cannot be graded against a different dataset.")
        if not args.summarize and run["configuration"] != configuration:
            parser.error("Run configuration changed. Use a new --output filename.")
    else:
        if args.resume or args.summarize:
            parser.error("Output does not exist yet.")
        if not backend.app.config["GROQ_API_KEY"]:
            parser.error("Set GROQ_API_KEY in .env before running.")
        run = {
            "experiment": "llm_only" if args.mode == "llm-only" else "grounded",
            "created_at": now(), "configuration": configuration,
            "review_policy": {
                "correctness": "Semantically meets all required points, with no forbidden or other material erroneous/unsupported claims; identical wording is not required.",
                "completeness": "Binary per required point; a combined point requires all its qualifiers. Answer cases only.",
                "behavior": "Review actual response meaning; a matching status label alone is insufficient. All 20 cases.",
                "reviewer": "Record the review method and reasons per case. Automated rubric checks are provisional.",
                "errors": "Record provider errors separately; never treat them as correct abstentions.",
                "scope": "Archive development set, not a current-law or held-out accuracy benchmark.",
            },
            "interpretation": {
                "prompt_scope": (
                    "General-knowledge prompt. No source-edition metadata, "
                    "PDFs, evidence passages or reference answers are supplied."
                    if args.mode == "llm-only" else
                    "Current /ask pipeline: conversational routing, then query embedding, archive retrieval and grounded generation for legal requests; no gold references sent."
                ),
                "grading_scope": "The frozen archive rubric is used only after generation. Claims outside it "
                    "may be unsupported for this evaluation without necessarily being factually false.",
                "comparison_limit": "Prompts and corpus scope differ across experiments; this is a pipeline comparison, not a controlled retrieval ablation or model-quality benchmark.",
            },
            "results": [],
        }
        # Exclusive creation protects an existing experiment from accidental replacement.
        with path.open("x", encoding="utf-8") as file:
            json.dump(run, file, ensure_ascii=False, indent=2)
    if args.summarize:
        save(path, run, dataset)
        print(json.dumps(run["summary"], indent=2))
        return
    completed = {result["id"] for result in run["results"]}
    pending = [case for case in dataset["cases"] if case["id"] not in completed][:args.limit]
    for index, case in enumerate(pending):
        if index and args.delay:
            time.sleep(args.delay)
        metadata = {}
        started = time.perf_counter()
        body, status = backend.answer_question(
            case["question"], system_instruction=instruction, metadata=metadata, grounded=args.mode == "grounded",
        )
        latency = round(time.perf_counter() - started, 3)
        run["results"].append({
            "id": case["id"], "question": case["question"], "completed_at": now(),
            "expected_behavior": case["expected_behavior"], "http_status": status,
            "response": body, "latency_seconds": latency, "metadata": metadata,
            "status_matches_expected": status == 200 and body["status"] == case["expected_behavior"],
            "review": {
                "reviewer": None, "required_points_met": [None] * len(case["required_points"]),
                "forbidden_claims_made": None, "material_errors": None,
                "behavior_correct": None, "notes": None,
            },
        })
        save(path, run, dataset)
        print(f"{case['id']}: HTTP {status}, {body['status']}, {latency:.2f}s", flush=True)
        if status != 200:
            print("Stopped after request failure; completed attempts are saved. No automatic retry.")
            if "retry_after_seconds" in metadata:
                print(f"Provider Retry-After: {metadata['retry_after_seconds']} seconds.")
            break
    save(path, run, dataset)
    print(f"Saved {len(run['results'])}/{len(dataset['cases'])} attempts to {path}")


if __name__ == "__main__":
    main()
