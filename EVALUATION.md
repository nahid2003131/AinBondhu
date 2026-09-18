# Evaluation Results

## Scope

This document summarizes the grounded evaluation run saved as `grounded-v0.4.json`.

The development set contains 20 questions. Its gold expectations remain focused on the Code of Criminal Procedure, 1898, while the active retrieval corpus for v0.4 contains 697 records:

- 515 CrPC section/range records;
- 182 Evidence Act, 1872 section records.

The set is a development evaluation, not a held-out benchmark and not a verified current-law benchmark.

## Run Configuration

| Item | v0.4 |
|---|---|
| Provider | Groq |
| Model | `openai/gpt-oss-120b` |
| Reasoning effort | `low` |
| Temperature | `0` |
| Retrieval | pgvector, top-k 5 |
| Embeddings | `gemini-embedding-2`, 768 dimensions |
| Active section/range records | 697 |
| Attempts | 20 |
| Successful HTTP responses | 20 |
| Provider/runtime errors | 0 |

## v0.4 Results

| Metric | Result |
|---|---:|
| HTTP delivery | 20/20 (100.00%) |
| Status-label match | 15/20 (75.00%) |
| Behavior accuracy | 15/20 (75.00%) |
| Strict answer correctness | 9/14 (64.29%) |
| Required-point coverage | 30/42 (71.43%) |
| Gold-evidence retrieval | 13/14 (92.86%) |
| Citation quote validity | 27/27 (100.00%) |
| Groundedness, answer level | 13/18 (72.22%) |
| Semantic citation support, answer level | 11/18 (61.11%) |
| Strict answers with supporting citations | 7/14 (50.00%) |
| Mean successful-request latency | 6.092s |
| Median latency | 6.202s |
| Maximum latency | 7.201s |

`Citation quote validity` verifies that returned excerpts actually came from the retrieved passage and source metadata. It does not prove that the citation semantically supports the answer.

## Comparison With v0.3

The questions and scoring expectations are materially the same, but v0.4 uses the expanded 697-record multi-source corpus. This is therefore useful development evidence, not a controlled scientific ablation.

| Metric | v0.3 | v0.4 |
|---|---:|---:|
| HTTP delivery | 20/20 (100.00%) | 20/20 (100.00%) |
| Status-label match | 16/20 (80.00%) | 15/20 (75.00%) |
| Behavior accuracy | 16/20 (80.00%) | 15/20 (75.00%) |
| Strict answer correctness | 9/14 (64.29%) | 9/14 (64.29%) |
| Required-point coverage | 32/42 (76.19%) | 30/42 (71.43%) |
| Gold-evidence retrieval | 13/14 (92.86%) | 13/14 (92.86%) |
| Citation quote validity | 32/32 (100.00%) | 27/27 (100.00%) |
| Groundedness, answer level | 10/18 (55.56%) | 13/18 (72.22%) |
| Semantic citation support | 8/18 (44.44%) | 11/18 (61.11%) |
| Strict answers + supporting citations | 7/14 (50.00%) | 7/14 (50.00%) |
| Mean latency | 5.261s | 6.092s |

## Main Observations

The expanded corpus did not reduce gold-evidence retrieval: it remained 13/14 cases (92.86%). The one persistent retrieval miss is Q13, whose required amendment provenance is in a historical footnote rather than the retrieved statutory body.

Answer-level groundedness and semantic citation support improved compared with v0.3, while behavior accuracy and required-point coverage declined. Strict answer correctness remained unchanged at 9/14, and the number of fully correct answer cases with supporting citations remained 7/14.

The new multi-source corpus also exposes retrieval-noise risk. For example, Q05 retrieved Evidence Act material ahead of the target CrPC section before the generator selected the correct CrPC evidence. Q08 additionally mislabeled CrPC section 161(3) as part of the Evidence Act. These are useful failure cases for the next experiment rather than proof that the additional source itself caused the error.

## Failure Cases to Carry Into Experiment Analysis

| Case | Observed issue | Failure type |
|---|---|---|
| Q02 | Omits special jurisdictions and powers from the section 1(2) saving rule. | Answer completeness |
| Q08 | Omits the section 170 condition and misattributes section 161(3) to the Evidence Act. | Qualification + legal-source attribution |
| Q09 | Omits the prescribed-form requirement and the `(1)-(3A) as far as may be` condition. | Answer completeness |
| Q10 | Correct answer, but displayed citation resolves to section 172 instead of section 173(4). | Citation selection/resolution |
| Q11 | Correct section 173 citation is accompanied by an unrelated section 265I citation. | Citation precision |
| Q12 | Treats the 120-day bail provision as a hard investigation deadline and omits part of section 173's rule. | Legal interpretation |
| Q13 | Fails to retrieve/identify the historical amendment footnote and amending Ordinance. | Footnote retrieval |
| Q14 | Answers arrest law instead of clarifying the unspecified actor and circumstances. | Routing/clarification |
| Q15 | Applies document-copy rules without clarifying the document or the user's role. | Routing/clarification |
| Q17 | Asks for clarification instead of stating that case-specific filing status is unavailable. | Unsupported/case-status behavior |
| Q18 | Gives the archived fine limit as the answer to a 2026 current-law question. | Version-sensitive abstention |
| Q19 | Treats section 167(5) as a current mandatory 120-day investigation deadline. | Version-sensitive abstention + interpretation |

## Per-Case Review

| Case | Expected | Actual | Review |
|---|---|---|---|
| Q01 | `answer` | `answer` | 2/2 required points. Correct statute title and commencement date; section 1 supports both points. |
| Q02 | `answer` | `answer` | 2/3 required points. Correct nationwide scope and the specific-provision exception, but it does not fully state that section 1(2) also preserves special jurisdictions and powers. |
| Q03 | `answer` | `answer` | 3/3 required points. Correct officer, recipient and Government-prescribed form, supported by section 173. |
| Q04 | `answer` | `answer` | 3/3 required points. Covers parties, nature of information, acquainted persons, and arrested-accused custody/bond status including sureties. |
| Q05 | `answer` | `answer` | 3/3 required points. Correctly explains notification of action taken to the original informant in the prescribed manner. The extra no-investigation notification statement is supported by section 157. |
| Q06 | `answer` | `answer` | 3/3 required points. Covers section 158 superior-officer routing, Government direction, and further investigation pending the Magistrate's orders. |
| Q07 | `answer` | `answer` | 2/2 required points. Correctly states that the Magistrate must make an order about discharge of the bond or another order considered fit. |
| Q08 | `answer` | `answer` | 2/5 required points. The page-99 attachment details are retrieved, but the page-98 section 170 condition is not carried into the answer. |
| Q09 | `answer` | `answer` | 1/3 required points. Correctly allows further investigation and a further report, but omits that the evidence may be oral or documentary, the prescribed-form requirement, and that subsections (1)-(3A) apply as far as may be. |
| Q10 | `answer` | `answer` | 3/3 required points. Answer content is correct, but the displayed citation resolves to section 172 rather than the retrieved section 173(4) passage that actually supports the answer. |
| Q11 | `answer` | `answer` | 3/3 required points. Correctly rejects automatic free supply and explains application plus the payment exception. One citation is the correct section 173 passage, but the additional section 265I citation is unrelated to the answer. |
| Q12 | `answer` | `answer` | 1/3 required points. It captures the no-arbitrary-delay idea, but omits the section 173 requirement to report as soon as the investigation is completed and does not preserve the evaluation's current-law limitation. |
| Q13 | `answer` | `answer` | 0/4 required points. The historical footnote remains outside the retrieved evidence, so the answer cannot satisfy the provenance question. |
| Q14 | `clarify` | `answer` | 1/3 required points. The legal propositions it gives are supported by the retrieved passages, but the expected behavior was clarification. |
| Q15 | `clarify` | `answer` | 0/2 required points. Provides several copy rules without clarifying which document is wanted or the user's role. The cited rules support the claims made, but the expected behavior was clarification. |
| Q16 | `insufficient_evidence` | `insufficient_evidence` | 2/2 required points. Correctly states that the archive does not establish a lawyer fee and does not invent an amount. |
| Q17 | `insufficient_evidence` | `clarify` | 0/2 required points. The request is already specific enough to identify a case-status question. The response should state that the system cannot determine filing status and that the actual case record or an authorized status source is required. |
| Q18 | `insufficient_evidence` | `answer` | 0/3 required points. The cited section supports the archived figure only. The static disclaimer does not adequately answer the current-year limitation. |
| Q19 | `insufficient_evidence` | `answer` | 1/3 required points. The response does not establish latest-law coverage or explain the missing amendment evidence. |
| Q20 | `answer` | `answer` | 2/2 required points. Correctly identifies immediate notification to the nearest Executive Magistrate empowered to hold inquests; section 174 supports both points. |

## Interpretation and Limits

This evaluation measures behavior against a frozen development rubric. It is not an independent legal audit, does not establish current-law accuracy, and is concentrated on a small number of CrPC provisions.

The Evidence Act was added to the runtime corpus for the incremental-update demonstration, but this 20-case set does not directly evaluate Evidence Act questions. A later dataset should include held-out questions across more CrPC sections and dedicated Evidence Act cases.

Manual rubric review is used for semantic correctness, completeness, groundedness and citation support. Quote provenance and gold-evidence retrieval are mechanically checked from saved retrieval metadata.

## Files

- `evaluation.json` — 20-case development dataset and rubric.
- `evaluate.py` — evaluation runner.
- `grounded-v0.3.json` — previous grounded run.
- `grounded-v0.4.json` — raw fresh run against the expanded corpus.
- `grounded-v0.4-reviewed.json` — v0.4 run with completed rubric review and calculated metrics.
- `EVALUATION.md` — this human-readable summary.

## Next Step

Use the v0.4 failure cases as the input to the experiment/failure-analysis deliverable. Each experiment should state the observed failure, a hypothesis about its cause, the change being tested, and the before/after result on the affected cases without rewriting the gold expectations after seeing model output.
