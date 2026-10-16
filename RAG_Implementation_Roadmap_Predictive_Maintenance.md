# RAG Implementation Roadmap for the Agentic Predictive Maintenance Platform

## 1. Objective

Make Retrieval-Augmented Generation (RAG) the core knowledge and reasoning layer of the predictive maintenance platform.

The system should use:
- Equipment telemetry and asset information
- Historical maintenance records and work orders
- Equipment manuals and technical documentation
- Troubleshooting guides and standard operating procedures (SOPs)
- Failure-analysis reports and relevant maintenance notes

The goal is to generate evidence-based maintenance explanations and recommendations with traceable source citations.

Extend the existing Python, FastAPI, LangGraph, PostgreSQL/pgvector, and Groq-based application rather than rebuilding it from scratch.

## 2. Current State and Target State

### Existing foundation

- `backend/services/rag_service.py`: provides a knowledge retrieval entry point and a `retrieve_similar()` function.
- `backend/services/chat_service.py`: connects Groq-based LLM calls with tools for assets, telemetry, workflows, and knowledge search.
- PostgreSQL with pgvector: provides a foundation for storing and searching vector embeddings.
- Existing diagnostic and operational tools: provide access to structured operational data.

### Current limitation

The current RAG service uses a deterministic hash-based vectorizer and a small set of hardcoded maintenance notes. This is a demo mechanism, not a semantic embedding model.

### Target state

Implement:
1. A document ingestion pipeline
2. Real semantic embeddings
3. Structure-aware chunking and metadata
4. Metadata-aware vector retrieval
5. Hybrid search using semantic and keyword retrieval
6. Reranking of candidate passages
7. Grounded generation with citations
8. RAG evaluation, guardrails, and monitoring

## 3. Target RAG Architecture

Separate unstructured maintenance knowledge from structured operational data.

### Unstructured knowledge

Examples:
- Equipment manuals
- Maintenance SOPs
- Troubleshooting documents
- Failure-analysis reports
- Historical maintenance notes

Store document chunks, embeddings, and source metadata in PostgreSQL with pgvector.

### Structured operational data

Examples:
- Current and historical sensor readings
- Asset thresholds and equipment status
- Work orders
- Workflow events

Retrieve these from the existing operational database and analytical tools. Do not treat the LLM as the authoritative source for sensor values or equipment thresholds.

### End-to-end flow

1. **Operator question or investigation trigger**
   - Example: “Why is Pump P-101 showing excessive vibration?”
2. **Query understanding and routing**
   - Identify the asset, symptom, plant, time window, and evidence needed.
3. **Operational data retrieval**
   - Retrieve relevant telemetry, thresholds, asset history, and work orders.
4. **Knowledge retrieval**
   - Search maintenance manuals, SOPs, troubleshooting guides, and failure reports.
5. **Retrieval refinement**
   - Apply metadata filters, combine vector and keyword results, rerank candidates, and select the most useful passages.
6. **Context assembly**
   - Combine verified operational facts with retrieved document passages and source metadata.
7. **LLM generation**
   - Use the Groq LLM through the existing diagnostic workflow to produce a grounded response.
8. **Validation and guardrails**
   - Check citations, evidence support, missing information, and safety constraints.
9. **Response**
   - Return possible causes, supporting evidence, recommended inspections, citations, and uncertainty.
10. **Human review**
    - Propose work orders or follow-up actions for operator review, preserving existing approval and audit controls.

## 4. Implementation Phases

### Phase 1: Build a Document Ingestion Pipeline

**Objective:** Replace hardcoded knowledge notes with real maintenance documents.

#### Tasks
- Add document upload and ingestion APIs.
- Initially support PDF, DOCX, TXT, and Markdown.
- Extract text and normalize whitespace.
- Preserve headings and tables where possible.
- Capture document identity, manufacturer, equipment type, revision, source, and page information.
- Split extracted text into chunks.
- Store chunks and metadata in PostgreSQL.
- Track ingestion status and failures.

#### Suggested modules
- `backend/services/document_loader.py`
- `backend/services/chunking_service.py`
- `backend/services/ingestion_service.py`
- `backend/routers/knowledge.py`

#### Acceptance criteria
- An authorized user can ingest a technical document.
- The document and its chunks are stored with source metadata.
- Failed ingestion is reported clearly and can be retried.
- The same document is not unintentionally duplicated.

#### Implementation status (2026-10-09)
- Implemented: role-gated multipart ingestion for PDF, DOCX, TXT, and Markdown; extraction, heading/page metadata, overlapping chunks, SHA-256 deduplication/retry, ingestion status, and plant/role-filtered document listing and RAG retrieval.
- Migration `20261009_0009` adds document records and nullable chunk provenance while preserving legacy seeded notes.
- Upload limit defaults to 10 MB. Original binary files are not retained; scanned PDF OCR and reliable DOCX page mapping are not supported in this phase.
- Role ACLs are enforced before retrieval. Plant ID currently narrows query results but is not a user entitlement; the identity provider has no plant-membership claim/mapping configured.
- Unit tests cover parsing/chunking. The PostgreSQL migration, upload, deduplication, retry, source ACL, citation audit persistence, and evaluation-fixture integration tests passed against a disposable TimescaleDB database (24 integration tests total).

### Phase 2: Implement Real Semantic Embeddings

**Objective:** Retrieve information based on meaning rather than only lexical overlap.

#### Tasks
- Select an embedding model suitable for the platform's language and technical vocabulary.
- Replace the demo `_hash_embedding()` function in `rag_service.py`.
- Generate embeddings for chunks during ingestion.
- Embed queries using the same model.
- Validate embedding dimensions against the pgvector column.
- Record the embedding model name and version.
- Plan for re-embedding if the model changes.

#### Initial model candidate
A lightweight starting candidate is `BAAI/bge-small-en-v1.5` from Sentence Transformers. Treat this as a candidate to evaluate against real maintenance questions, not as a guaranteed best model.

#### Acceptance criteria
- The same embedding model and preprocessing are used for documents and queries.
- Similar maintenance questions retrieve relevant passages even when their wording differs.
- Embedding dimensions and database schema are consistent.
- Embedding generation failures are logged and handled.

### Phase 3: Improve Chunking and Metadata

**Objective:** Preserve the technical context needed to interpret maintenance passages correctly.

#### Initial chunking experiment
Start by testing:
- Chunk size: approximately 400–700 tokens
- Overlap: approximately 50–100 tokens

These are starting points for evaluation, not fixed production settings.

#### Tasks
- Use structure-aware chunking that respects headings and procedural steps.
- Avoid splitting safety instructions, troubleshooting steps, or table rows in misleading ways.
- Preserve document and section titles.
- Attach document ID, chunk ID, page number, section title, asset type, manufacturer, document type, and revision.
- Add metadata filters for plant, equipment type, manufacturer, document type, and revision where appropriate.
- Keep chunk text traceable to its original source.

#### Acceptance criteria
- Retrieved chunks retain enough context to be interpreted correctly.
- Source document, page, and section can be identified.
- Metadata filters return only eligible documents.
- Chunking changes can be evaluated against a fixed test set.

### Phase 4: Build Hybrid Retrieval and Reranking

**Objective:** Improve retrieval for both natural-language symptoms and exact technical identifiers.

#### Retrieval pipeline
1. Apply applicable access and metadata filters.
2. Retrieve candidates using vector similarity search.
3. Retrieve candidates using PostgreSQL full-text search or a BM25-based keyword search.
4. Merge and deduplicate results, using Reciprocal Rank Fusion (RRF) as an initial option.
5. Rerank the candidate passages using a cross-encoder.
6. Select the best passages for context construction.

#### Initial configuration to test
- Retrieve a candidate pool from both semantic and keyword search.
- Rerank the combined candidates.
- Send approximately 3–5 high-quality passages to the LLM initially.
- Tune candidate count and final context size through evaluation.

#### Acceptance criteria
- Exact alarm codes and model numbers can be found.
- Natural-language symptom questions retrieve semantically relevant material.
- Duplicate passages are removed or consolidated.
- Reranking improves or maintains retrieval metrics on the evaluation set.

### Phase 5: Integrate RAG with the Diagnostic Agent

**Objective:** Make retrieved knowledge part of the diagnostic workflow, not just a separate chatbot feature.

#### Tasks
- Connect retrieval to the existing diagnostic agent and LangGraph workflow.
- Retrieve maintenance knowledge based on asset identity, symptoms, and relevant operating context.
- Query structured telemetry and asset data through existing tools.
- Distinguish observed facts from possible explanations.
- Include source citations and explicit evidence gaps.
- Preserve human approval, safety checks, and audit trails.
- Avoid executing physical or operational actions directly from unverified LLM output.

#### Recommended response structure
Each diagnostic response should include:
1. **Observed facts:** Actual telemetry, timestamps, thresholds, and asset information retrieved from trusted operational sources.
2. **Possible causes:** Hypotheses supported by evidence, clearly labeled as possibilities.
3. **Supporting evidence:** Relevant maintenance passages, work orders, and telemetry.
4. **Recommended checks:** Inspection steps supported by the source material and site procedures.
5. **Citations:** Document title, revision, page or section, and a stable source reference.
6. **Uncertainty and missing data:** Information needed to confirm or reject a hypothesis.
7. **Next action:** A reviewable recommendation or work-order proposal, subject to approval.

#### Acceptance criteria
- Answers use current operational data when the question requires it.
- Technical recommendations include traceable evidence.
- The system does not claim a component has failed based on a symptom alone.
- Missing or conflicting evidence is communicated clearly.
- Existing approval and audit mechanisms remain in place.

### Phase 6: Add Evaluation, Guardrails, and Monitoring

**Objective:** Measure RAG quality and make failures observable.

#### Evaluation dataset
Create an initial set of approximately 50–100 representative maintenance questions, expected source documents, and reference answers where available. Start smaller if needed and expand as failure cases are identified.

#### Metrics

| Metric | Purpose |
|---|---|
| Recall@K | Measures whether relevant evidence appears in the top K retrieved passages. |
| MRR | Measures how early the first relevant result appears. |
| nDCG | Measures the quality of the ranked retrieval results. |
| Faithfulness | Checks whether generated claims are supported by the supplied evidence. |
| Citation accuracy | Checks whether cited passages support the associated claims. |
| Answer correctness | Measures whether the response correctly addresses the question. |
| Latency | Tracks end-to-end and component-level response times. |
| Cost | Tracks embedding and LLM usage costs. |
| No-answer rate | Tracks how often the system cannot find adequate evidence. |

#### Guardrails
- Treat retrieved documents as untrusted data, not as instructions to the LLM.
- Detect or resist prompt injection in user queries and uploaded documents.
- Enforce access controls before retrieving restricted documents.
- Validate citations against actual retrieved chunks.
- Require abstention or a qualified response when evidence is insufficient.
- Log failures without unnecessarily storing secrets or sensitive data.
- Preserve human approval for maintenance actions.

#### Acceptance criteria
- Evaluation results can be reproduced across code changes.
- Retrieval and generation failures can be distinguished.
- Latency, cost, and no-answer rate are measurable.
- Prompt injection and unsupported-answer tests are included in regression testing.

## 5. Recommended Repository Structure

Extend the existing application incrementally. This is a proposed structure; keep existing modules that already serve these responsibilities and avoid duplicating them.

```text
backend/
├── agents/
│   ├── diagnostic_agent.py
│   ├── workflow_graph.py
│   └── state.py
├── routers/
│   ├── chat.py
│   └── knowledge.py
├── services/
│   ├── rag_service.py
│   ├── document_loader.py
│   ├── chunking_service.py
│   ├── embedding_service.py
│   ├── ingestion_service.py
│   ├── retrieval_service.py
│   ├── reranking_service.py
│   ├── context_builder.py
│   ├── citation_service.py
│   └── chat_service.py
├── schemas/
│   └── knowledge.py
├── tests/
│   ├── test_ingestion.py
│   ├── test_chunking.py
│   ├── test_retrieval.py
│   ├── test_rag_evaluation.py
│   └── test_rag_grounding.py
└── ...
```

### Module responsibilities

| Module | Responsibility |
|---|---|
| `document_loader.py` | Extract text and document structure from supported formats. |
| `chunking_service.py` | Split documents into context-preserving chunks. |
| `embedding_service.py` | Generate and version embeddings for queries and chunks. |
| `ingestion_service.py` | Coordinate parsing, chunking, embedding, storage, and ingestion status. |
| `retrieval_service.py` | Execute vector, keyword, and metadata-filtered retrieval. |
| `reranking_service.py` | Rerank candidate passages by query relevance. |
| `context_builder.py` | Assemble a bounded, source-aware context for the LLM. |
| `citation_service.py` | Validate and format source references. |
| `rag_service.py` | Provide a stable interface to the rest of the application. |
| `knowledge.py` router | Expose authenticated knowledge ingestion and management endpoints. |

## 6. Recommended Database Design

Continue with PostgreSQL and pgvector for the initial implementation. A separate vector database is not required for the first iteration.

### `knowledge_documents`

Store document-level metadata:
- `id`
- `filename`
- `document_type`
- `manufacturer`
- `asset_type`
- `revision`
- `source_uri` or storage reference
- `checksum`
- `ingestion_status`
- `created_at`
- `updated_at`

### `knowledge_chunks`

Store searchable passages:
- `id`
- `document_id`
- `content`
- `embedding`
- `page_number`
- `section_title`
- `asset_type`
- `manufacturer`
- `document_type`
- `revision`
- `metadata`
- `created_at`

Use an embedding column whose dimensions match the selected embedding model. Plan schema and index migrations carefully if the embedding model changes.

### `rag_query_logs`

Store operational and evaluation information where appropriate:
- Query or a privacy-safe query reference
- Retrieval configuration
- Retrieved chunk IDs
- Latency measurements
- Outcome and error category
- Model and prompt versions

Apply suitable retention and privacy rules.

### `rag_feedback`

Store feedback useful for improvement:
- Query reference
- Answer reference
- Operator rating or feedback
- Correct source documents, where known
- Review status
- Created timestamp

Keep live sensor telemetry and operational records separate from document chunks and embeddings. Connect them through stable asset IDs and the application's existing data-access layer.

## 7. Example End-to-End Use Case

### User question
“Why is Pump P-101 showing high vibration, and what should I inspect first?”

### Workflow
1. **Parse the question:** Identify Pump P-101, high vibration, and the requested inspection guidance.
2. **Retrieve operational facts:** Query current vibration, recent trends, configured limits, and relevant asset history.
3. **Retrieve technical knowledge:** Search pump manuals, bearing troubleshooting sections, lubrication SOPs, and similar work orders.
4. **Rerank evidence:** Prioritize passages relevant to the pump model and observed symptoms.
5. **Build context:** Combine verified telemetry and source passages without mixing facts and hypotheses.
6. **Generate a response:** Explain possible causes and evidence-based checks.
7. **Validate the answer:** Check citations, evidence support, and safety constraints.
8. **Present the result:** Return the explanation and references, along with uncertainty and missing data.
9. **Request human review:** Propose a work order or follow-up action without bypassing approval controls.

### Expected answer characteristics

The system should not conclude that the bearing has failed solely because vibration is elevated. It should explain whether bearing wear is one possible cause, identify other plausible causes supported by available evidence, and specify which measurements or inspections would help distinguish between them.

The final answer should cite the actual source documents and operational data used. It must not invent document names, page numbers, measurements, or citations.

## 8. Delivery Schedule

This is an initial planning estimate, assuming the current application and database are operational.

| Period | Focus | Deliverable |
|---|---|---|
| Week 1 | Ingestion, chunking, semantic embeddings | Real documents can be ingested, indexed, and retrieved. |
| Week 2 | Hybrid search, reranking, citations | More relevant passages are returned and used in grounded answers. |
| Week 3 | Agent integration, evaluation, guardrails | End-to-end diagnostic demonstration with measurable quality and source references. |

The schedule may need to expand depending on document complexity, ingestion volume, infrastructure, and evaluation findings.

## 9. Immediate Implementation Checklist

### Foundation
- [ ] Inspect the existing RAG service and its database schema.
- [ ] Replace the demo hash-based embedding implementation.
- [ ] Add a real embedding model and verify vector dimensions.
- [ ] Implement document loading for PDF, DOCX, TXT, and Markdown.
- [ ] Add structure-aware chunking and source metadata.
- [ ] Store document chunks and embeddings in PostgreSQL/pgvector.

### Retrieval quality
- [ ] Implement vector similarity search.
- [ ] Add PostgreSQL full-text or BM25 keyword retrieval.
- [ ] Merge and deduplicate candidates using RRF or another evaluated method.
- [ ] Add metadata filtering and access controls.
- [ ] Add cross-encoder reranking.
- [ ] Tune final context size using the evaluation set.

### Agent integration
- [ ] Connect RAG to the existing diagnostic workflow.
- [ ] Retrieve structured telemetry separately from unstructured knowledge.
- [ ] Add source-grounded response generation.
- [ ] Validate citations and report evidence gaps.
- [ ] Preserve human approval and audit controls.

### Reliability
- [ ] Create a representative maintenance-question test set.
- [ ] Measure Recall@K, MRR or nDCG, faithfulness, citation accuracy, and answer correctness.
- [ ] Add prompt-injection and insufficient-evidence tests.
- [ ] Monitor latency, costs, failures, and no-answer rates.
- [ ] Add regression tests for changes to prompts, chunking, and embeddings.

## 10. Repository and Secret Hygiene

Before sharing or deploying the repository:
- Remove `.env` files containing secrets from distributable source.
- Keep `.venv` and local runtime data out of version control.
- Add relevant local files and directories to `.gitignore`.
- Rotate any credentials that may have been exposed.
- Use environment variables or a secrets manager for production credentials.
- Avoid storing unnecessary sensitive information in RAG logs.

## 11. Recommended Implementation Order

Prioritize the following sequence:

1. **Reliable ingestion**
2. **Semantic embeddings**
3. **Structure-aware chunking and metadata**
4. **Hybrid retrieval**
5. **Reranking**
6. **Grounded generation and citations**
7. **Evaluation and guardrails**
8. **Production monitoring and optimization**

Do not begin with multi-query retrieval, agentic retrieval loops, or parent-document retrieval. Add those advanced techniques after the baseline pipeline has measurable retrieval and answer quality.

The first milestone is a working flow in which a real maintenance document is ingested, a relevant passage is retrieved for a user question, and the diagnostic agent produces an answer with a verifiable citation. Build and evaluate that flow before expanding the system.

## Trust work update (2026-10-13)

- Added a four-case labeled retrieval fixture and Recall@K/MRR scorer. `backend/scripts/evaluate_rag.py` runs it only against a database whose name contains `test` or `eval`; it reports observed hash-retrieval results without asserting that the demo ranking is production-quality.
- Diagnostic retrieval now uses asset type, alert type, and measurements explicitly compared with configured limits; it does not serialize the raw telemetry dictionary or operator message into the retrieval query.
- Retrieval outcomes are distinguished as `available`, `no_matches`, or `unavailable`. Failures are logged without query/chunk text and counted by outcome in `rag_retrieval_requests_total`.
- Server-generated citation references are validated against retrieved chunk IDs and persist chunk/document IDs, revision, page, and section in work-order JSON plus the hash-chained `assessment_completed` event. The approval modal opens a passage through a role/plant-filtered API endpoint.
- Remaining for this trust slice: claim-level citation entailment, real semantic embeddings, hybrid search/reranking, distributed rate limiting, and user-to-plant identity entitlements. PostgreSQL integration tests passed against a disposable database; the local application database is migrated through `20261009_0009`.

## Semantic retrieval update (2026-10-16)

- Replaced runtime hash vectors with FastEmbed using `BAAI/bge-small-en-v1.5` by default; document and query vectors are normalized and checked against the existing 384-dimensional pgvector column.
- Added `embedding_model` provenance and an Alembic migration. Existing chunks remain available to keyword search until reindexed with `python -m scripts.reindex_knowledge_embeddings --confirm`.
- Retrieval now combines model-matched vector candidates and PostgreSQL full-text candidates using reciprocal-rank fusion, preserving the existing document role and optional plant filters.
- The evaluation script accepts a custom labeled query set through `--cases`; metrics still measure retrieval only and are not a production quality certification.
- Focused RAG unit tests and full backend unit discovery passed. PostgreSQL integration tests were skipped because `TEST_DATABASE_URL` was not configured in the local environment.
- Remaining gaps include cross-encoder reranking, claim-level citation entailment, and identity-backed plant entitlements.

## RAG completion update (2026-10-16)

- Document uploads now retain original bytes, return a queued processing record, and are parsed/indexed by a leased background worker with bounded retries. Image-only PDF pages use configurable Tesseract OCR; DOCX page attribution remains unavailable.
- Knowledge ACLs now combine roles with optional verified OIDC subject/group claims and plant entitlements. `OIDC_GROUPS_CLAIM` defaults to `groups`.
- Human-confirmed close-outs are indexed as plant-scoped knowledge only when not marked simulated and without a `SIM-` CMMS reference. `scripts/index_closed_work_orders.py --confirm` supports idempotent historical backfill.
- The FastEmbed cross-encoder is optional via `RAG_RERANKER_MODEL_NAME`. Evaluation reports Recall@K, MRR, and nDCG, and `--compare-reranker` prints paired deltas. Leave it disabled until reviewed technician-query data demonstrates a gain.
- Remaining: collect/approve real technician queries and relevance labels; replace/adjudicate the advisory LLM citation check with a validated entailment method if policy requires stronger guarantees; validate ACL claim mappings with the production identity provider. Do not use synthetic questions or labels as substitutes for field evaluation.
