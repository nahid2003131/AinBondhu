# Experiments and Failure Analysis

## Scope

This report documents the experiments performed after the grounded v0.4 evaluation of the Bangladesh criminal-law Q&A assistant.

The goal was not to optimize for the evaluation set directly. The goal was to identify recurring failure modes, make controlled changes to the pipeline, rerun the same frozen evaluation set, and record whether each change should be kept.

The frozen evaluation set contains 20 cases. The active retrieval corpus contains 697 indexed records across:

- Code of Criminal Procedure, 1898
- Evidence Act, 1872

The v0.4 run is the baseline for the experiments below.

---

## Baseline: grounded-v0.4

Configuration:

- Provider: Groq
- Model: `openai/gpt-oss-120b`
- Reasoning effort: low
- Temperature: 0
- Retrieval: vector search, top-k 5
- Embedding profile: `gemini-embedding-2:768:document-v1`
- Corpus: 697 active legal section/range records
- Evidence budget: 16,000 characters

Baseline results:

| Metric | v0.4 |
|---|---:|
| HTTP delivery | 20/20 (100%) |
| Expected behavior | 15/20 (75.00%) |
| Strict answer correctness | 9/14 (64.29%) |
| Required-point coverage | 30/42 (71.43%) |
| Gold evidence retrieval | 13/14 (92.86%) |
| Citation quote validity | 27/27 (100%) |
| Answer-level groundedness | 13/18 (72.22%) |
| Semantic citation support | 11/18 (61.11%) |
| Strict answers with supporting citations | 7/14 (50.00%) |
| Mean latency | 6.092 s |
| Median latency | 6.202 s |
| Maximum latency | 7.201 s |
| Total tokens | 67,470 |

The retrieval layer was already finding the expected legal material in most answer cases. The larger problems were in answer generation, qualification preservation, citation choice, ambiguity handling, and current-law/version handling.

---

## Failure Groups Observed in v0.4

### 1. Missing legal conditions and qualifications

Examples:

- Q02 omitted some of the special-law/jurisdiction qualifications.
- Q08 omitted the Section 170 applicability condition.
- Q09 omitted the fact that further evidence may be oral or documentary and omitted the rule that subsections (1)–(3A) apply to the further report “as far as may be.”
- Q12 and Q19 over-interpreted the 120-day provision.

### 2. Wrong statute attribution

Q08 described Section 161(3) as a provision of the Evidence Act even though the retrieved provision was from the Code of Criminal Procedure.

### 3. Citation precision failures

The model sometimes selected a valid retrieved evidence ID that was related to the topic but did not directly support the final answer.

Examples:

- Q10 selected Section 172 instead of the supporting Section 173 passage.
- Q11 included an unrelated Section 265I citation.

### 4. Version-sensitive questions

The corpus is an archived legal source set and does not certify the currently operative law.

Examples:

- Q18 asked for the maximum fine in 2026, but v0.4 presented the archived amount as current.
- Q19 asked for the latest investigation deadline, but the system treated an archived 120-day bail-related provision as the current general deadline.

### 5. Routing and ambiguity failures

These failures occur before grounded generation and therefore cannot be fully solved by the RAG answer prompt.

- Q14 should ask who “they” are and what the circumstances are.
- Q15 should ask which document and the user’s role in the case.
- Q17 asks about the actual filing status of a specific case, which the statutory corpus cannot establish.

### 6. Missing historical amendment provenance

Q13 asks which law introduced particular Section 173 rules. The relevant amendment history is present in historical source notes/footnotes, but the current retrieval path does not expose that provenance as independent retrievable evidence.

---

# Experiment 1: Two-Pass Evidence Audit

## Hypothesis

A second model call after the initial grounded answer could audit the draft against the retrieved passages and correct unsupported claims, lost qualifications, wrong statute attribution, and irrelevant citations.

Proposed flow:

```text
question
  -> retrieval
  -> grounded draft
  -> evidence audit
  -> final answer
  -> citation resolution
```

The audit was designed to receive only:

- the original question
- the retrieved passages
- the draft answer
- the draft evidence IDs
- the archive-currentness flag

It did not receive evaluation answers or gold rubric information.

## Outcome

The experiment was abandoned before a full benchmark.

The additional model call approximately doubled generation work for grounded questions and quickly caused provider quota/rate-limit failures during evaluation. The provider returned long `Retry-After` windows, making the approach impractical for the current deployment and evaluation budget.

## Decision

**Rejected for the current version.**

The idea may improve verification quality, but the extra request cost is not acceptable for the current provider limits. A future version could revisit this with a cheaper verifier, a deterministic entailment check, or selective auditing only for high-risk answers.

---

# Experiment 2: Single-Pass Grounding Prompt Strengthening

## Hypothesis

The most common v0.4 generation failures could be reduced without changing retrieval or adding another model call by strengthening the existing grounded-generation instruction.

The change should improve:

- preservation of legal conditions and exceptions
- correct statute attribution
- citation precision
- resistance to converting conditional rules into universal deadlines
- abstention on current/latest-law questions when the archive cannot verify currentness

## Controlled Change

Only the grounded `INSTRUCTION` in `rag.py` was changed.

The following were intentionally left unchanged:

- `app.py`
- `evaluate.py`
- retrieval algorithm
- retrieval top-k
- embeddings
- database
- ingestion
- citation resolver
- model
- temperature
- evaluation dataset

The new instruction explicitly tells the model to:

1. check substantive legal claims against the retrieved passages;
2. preserve material conditions, exceptions, actor requirements, scope, and cross-references;
3. avoid converting conditional rules, thresholds, or possible consequences into universal duties or deadlines;
4. attribute provisions to the correct statute;
5. select only the smallest set of evidence IDs that directly support the final answer; and
6. return `insufficient_evidence` for current/latest/current-year legal rules when the archived corpus cannot verify currentness.

This produced `grounded-v0.5.json`.

---

## v0.4 vs v0.5

| Metric | v0.4 | v0.5 | Change |
|---|---:|---:|---:|
| HTTP delivery | 20/20 | 20/20 | no change |
| Expected behavior | 15/20 (75.00%) | 16/20 (80.00%) | +5 pp |
| Strict answer correctness | 9/14 (64.29%) | 9/14 (64.29%) | no change |
| Required-point coverage | 30/42 (71.43%) | 30/42 (71.43%) | no change |
| Gold evidence retrieval | 13/14 (92.86%) | 13/14 (92.86%) | no change |
| Strict answers with supporting citations | 7/14 (50.00%) | 8/14 (57.14%) | improved |
| Mean latency | 6.092 s | 6.050 s | essentially unchanged |
| Total tokens | 67,470 | 70,882 | +5.1% |

The experiment improved some safety and citation behavior but did not improve overall strict answer correctness or required-point coverage.

---

## Observed Improvements

### Q10 — citation selection improved

The answer about obtaining a copy of the police report remained correct, and v0.5 selected the supporting Section 173 evidence rather than an unrelated Section 172 passage.

This is a direct improvement in semantic citation support.

### Q12 — 120-day rule interpretation improved

v0.4 treated the 120-day provision too much like a hard investigation maximum.

v0.5 correctly stated that Section 173 requires investigation to be completed without unnecessary delay and framed the 120-day provision as a bail-related consequence rather than a universal completion deadline.

The answer still did not satisfy every rubric point, but the main legal interpretation became safer.

### Q18 — current-law abstention improved

The question asked for the largest fine a first-class magistrate can impose in 2026.

v0.4 presented the archived amount as if it answered the current-law question.

v0.5 returned `insufficient_evidence` and explicitly explained that the archived amount could not be verified as the current 2026 maximum.

This changed the expected-behavior score from 15/20 to 16/20.

---

## Remaining Failures After v0.5

### Q08 — statute attribution and applicability condition

The answer still called Section 161 a provision of the Evidence Act, even though the relevant retrieved passage was CrPC Section 161.

It also still omitted the condition that Section 173(3A) applies when the report is in a case to which Section 170 applies.

**Likely cause:** passage metadata identifies the document, but the model can still confuse a cross-reference with a statute identity. Prompt instruction alone was not sufficient.

**Future experiment:** expose clearer law-name metadata beside every passage and validate statute/section pairs before returning the answer.

### Q09 — incomplete further-investigation rule

The answer correctly allowed further investigation and a further report but still omitted:

- that the new evidence may be oral or documentary; and
- that subsections (1)–(3A) apply to the further report “as far as may be.”

**Likely cause:** generation compression. The model answers the user’s main question but drops secondary qualifiers.

**Future experiment:** a deterministic completeness checklist for provisions with known multi-part conditions, or a structured extraction step before prose generation.

### Q11 — citation precision still inconsistent

The answer text was correct, but the selected citation was unrelated Section 265I rather than the supporting Section 173 passage.

**Likely cause:** the current backend validates whether an evidence ID exists, not whether it semantically supports the claim.

**Future experiment:** semantic citation verification or deterministic support scoring before rendering citations.

### Q13 — amendment provenance unavailable to retrieval

The answer identified Section 173 but did not identify the Law Reforms Ordinance, 1978 (Ordinance No. XLIX of 1978), section 2 and Schedule.

**Likely cause:** amendment history is stored separately from the ordinary section text and is not retrieved as independent evidence.

**Future experiment:** index amendment notes/footnotes as page-level historical-provenance records without falsely attaching every page footnote to a particular subsection.

### Q14 and Q15 — clarification routing

Both cases still received legal answers even though essential context was missing.

**Likely cause:** these cases are decided by the router before the grounded `rag.py` instruction is used.

**Future experiment:** strengthen routing rules for unspecified actors, documents, roles, and circumstances.

### Q17 — case-status request

The system returned `clarify`, but the expected behavior is `insufficient_evidence`.

The statutory corpus cannot establish whether a police report has actually been filed in a specific case.

**Likely cause:** the router interprets the request as unclear rather than recognizing it as a clear request for external/private case-status data.

**Future experiment:** add explicit case-status handling at the router level.

### Q19 — latest-law handling still failed

The answer still treated the archived 120-day provision as the latest investigation deadline.

**Likely cause:** prompt-only currentness protection is not deterministic enough for all version-sensitive questions.

**Future experiment:** detect terms such as `current`, `latest`, or explicit current years before generation and enforce a version-aware abstention unless a verified-current source is available.

---

# Decision

**Keep the v0.5 grounded prompt change.**

Reasons:

- expected-behavior accuracy improved from 75% to 80%;
- strict answers with supporting citations improved from 50% to 57.14%;
- Q18 became a safer current-law abstention;
- Q10 citation selection improved;
- Q12 avoided the strongest form of the 120-day deadline misinterpretation;
- mean latency stayed effectively unchanged; and
- the change required no additional model call or retrieval redesign.

The experiment did **not** improve strict answer correctness or required-point coverage. Therefore the result should not be described as a general accuracy improvement. It is better characterized as a targeted improvement in grounding discipline, current-law caution, and citation behavior.

---

# Engineering Conclusions

The evaluation suggests that retrieval is not the main bottleneck: gold evidence was retrieved in 13 of 14 answer cases in both v0.4 and v0.5.

The remaining failures fall into separate system layers:

```text
routing
  -> ambiguity and case-status failures

retrieval/indexing
  -> missing amendment-provenance evidence

generation
  -> lost qualifiers and occasional statute confusion

citation selection
  -> valid evidence IDs that do not always support the final claim

version control
  -> archived law being mistaken for current law
```

Treating all of these as a single prompt problem would hide the actual architecture issues. Future work should target each layer independently and rerun the affected evaluation cases after each change.

---

## Experiment Artifacts

- `grounded-v0.4.json` — baseline grounded run
- `grounded-v0.5.json` — single-pass grounding-prompt experiment
- `evaluation.json` — frozen evaluation dataset and rubrics
- `EVALUATION.md` — overall evaluation methodology and results
- `EXPERIMENTS.md` — this experiment and failure-analysis report
