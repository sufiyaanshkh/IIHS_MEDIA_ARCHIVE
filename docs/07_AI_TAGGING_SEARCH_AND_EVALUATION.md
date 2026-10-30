# 07 — AI Tagging, Search, and Evaluation

## 1. General rule

AI output is a suggestion, not ground truth. Store provenance and preserve the distinction between model output and human-approved annotation.

## 2. Model registry

For each model/pipeline, record:
- Internal identifier and human-readable name.
- Model version or immutable revision.
- Weight file checksum where feasible.
- License and permitted use.
- Runtime/library versions.
- Hardware and precision configuration.
- Input limits and expected outputs.
- Evaluation dataset version.
- Measured quality, latency, memory use, and known limitations.
- Approval status and owner.

Do not load arbitrary model code from an untrusted source. Prefer pinned, reviewed model artifacts and avoid runtime internet downloads in production.

## 3. Existing baseline

EmbeddingGemma 2 has been tested on approximately 390–400 images and was reported as promising. Treat this as preliminary evidence only. Preserve the current baseline until a controlled evaluation shows that a replacement or change is better.

The test set size and positive feedback do not establish production accuracy. Build a representative evaluation set with labels and edge cases before setting performance claims.

## 4. Tagging flow

- Generate candidate tags with a local model.
- Validate the output schema and allowed vocabulary.
- Store candidates with model/pipeline version and confidence only when confidence has a defined meaning.
- Apply conservative thresholds informed by evaluation.
- Send uncertain or unsupported cases to human review.
- Let reviewers accept, reject, edit, or defer.
- Keep the original machine output for audit and evaluation.
- Do not treat contextual guesses as visual evidence unless the product explicitly labels them as inferred context.

## 5. Confidence policy

Thresholds must be tuned against a labeled validation set, not selected only by intuition. Use separate thresholds if model/task behaviour differs. Do not compare raw scores across models unless calibrated.

For each class or task, report:
- Precision and recall.
- False-positive and false-negative examples.
- Coverage (fraction automatically suggested or accepted).
- Performance by relevant image conditions and categories.
- Human-review rate.
- Processing time and resource use.

A high-confidence score is not a guarantee of correctness. The UI must not imply otherwise.

## 6. Human feedback and model training

Reviewer decisions can become a curated dataset only through a defined pipeline:
- Record the action and context.
- Distinguish direct corrections from ambiguous cases.
- Review label consistency.
- Deduplicate examples and prevent train/test leakage.
- Keep evaluation data separate from training data.
- Version datasets and training runs.
- Require approval before a new model replaces the production model.
- Maintain a rollback path.

Do not automatically retrain on every click or silently alter production behaviour.

## 7. Semantic search

- Define how text and image embeddings are generated and normalized.
- Version the embedding model and index.
- Rebuild or migrate indexes explicitly when the model changes.
- Test query quality with a fixed benchmark and human relevance judgments.
- Apply authorization filters before returning results.
- Record enough diagnostic metadata to explain index/model mismatch without logging sensitive content.
- If vector storage currently uses JSONB, benchmark its query performance and decide whether a supported vector extension is needed.

## 8. Current local indexing behavior

After an explicitly confirmed ingestion preview, the worker creates visual embeddings for images and sampled video frames. For still images it also runs local Tesseract OCR (English, bounded output) and embeds detected text; for videos it stores and embeds speech transcripts and English translations when available. Search supports visual meaning or exact text, with media-type and added-year filters. The year filter uses the archive record creation date, not a camera-captured date.

The source scanner is operator-triggered and restricted to configured roots; it is not a background crawler. The current pipeline does not infer people’s names, recognize faces, or create descriptive visual tags. OCR may misread text and cannot establish that a printed name identifies a person in the image. OCR/transcript search remains unverified against a representative institutional dataset.

## 9. Face recognition and enrollment

Face matching is sensitive and can produce harmful false matches. Before enabling:
- Obtain institutional/privacy/legal approval.
- Confirm lawful basis, access rules, retention, and notice requirements.
- Review model and pretrained-weight licenses. InsightFace source code being open source does not automatically mean every pretrained model is licensed for unrestricted institutional or commercial use.
- Evaluate false match and false non-match behaviour on a representative, appropriately governed dataset.
- Use a review threshold and a separate confirmed-identity state.
- Never auto-confirm uncertain matches.
- Restrict access to embeddings and enrollment records.
- Audit enrollment, matching, export, and deletion.
- Provide a documented correction and deletion procedure.

If these conditions are not met, keep face enrollment disabled. InsightFace states that its code is MIT licensed but its pretrained model packs are for non-commercial research; it directs users to request a separate license for open-source recognition models. Obtain the applicable written model license before institutional deployment.

## 10. Evaluation gates

Before production use, establish acceptance targets for each task with the product owner and subject-matter reviewers. Do not invent universal targets. Record dataset composition, limitations, thresholds, and failure examples. A model update requires a repeatable evaluation and explicit approval.
