# RAG & Deep Research Tech Stack Evaluation

**Date:** 2026-03-01
**Scope:** Stage 4+ — Documents, RAG pipeline, agentic research, deep synthesis
**Constraints:** Air-gapped single-node deployment, NVIDIA RTX 24GB VRAM (shared with LLM), no cloud APIs

---

## Table of Contents

1. [Executive Summary & Recommended Stack](#1-executive-summary--recommended-stack)
2. [Architecture Vision: The Deep Research Assistant](#2-architecture-vision-the-deep-research-assistant)
3. [Vector Database](#3-vector-database)
4. [Embedding Models](#4-embedding-models)
5. [Document Parsing & Understanding](#5-document-parsing--understanding)
6. [Chunking Strategies](#6-chunking-strategies)
7. [Retrieval Architecture](#7-retrieval-architecture)
8. [Re-Ranking](#8-re-ranking)
9. [Orchestration Frameworks](#9-orchestration-frameworks)
10. [Agentic RAG & Deep Research Patterns](#10-agentic-rag--deep-research-patterns)
11. [Knowledge Extraction & Graph](#11-knowledge-extraction--graph)
12. [Citation & Provenance](#12-citation--provenance)
13. [VRAM Budget](#13-vram-budget)
14. [Implementation Roadmap](#14-implementation-roadmap)

---

## 1. Executive Summary & Recommended Stack

| Layer | Decision | Why |
|-------|----------|-----|
| **Vector DB** | **pgvector** (PostgreSQL) | Already deployed, zero new services, first-class SQLAlchemy, best perf at our scale |
| **Embedding model** | **Qwen3-Embedding-0.6B** (primary), **bge-m3** (fallback) | Best quality/VRAM ratio, 8K context, Apache 2.0, vLLM-native |
| **Document parsing** | **Docling** (primary), **PyMuPDF4LLM** (fast path) | Best structure preservation, MIT, built-in hierarchical chunking |
| **Chunking** | **Structure-aware** + semantic fallback + parent-child | Legal/medical docs are inherently hierarchical — respect that structure |
| **Retrieval** | **Hybrid search** (BM25 + dense vectors) + **re-ranking** | Essential for legal terminology precision; hybrid beats pure vector by 10-25% |
| **Re-ranker** | **BGE-reranker-v2-m3** (GPU) or **FlashRank** (CPU) | +5-15% NDCG improvement, fits VRAM budget |
| **Orchestration** | **LlamaIndex** (retrieval core) + **custom FastAPI** (agent loop) | Best pgvector integration, no framework lock-in on orchestration |
| **Agent pattern** | **Plan-and-Execute** + iterative retrieval + reflection loops | Matches deep research use case without multi-GPU requirement |
| **Knowledge extraction** | **GLiNER** (zero-shot NER) + **LLM batch extraction** | Configurable entity types, no training data needed, air-gapped |
| **Advanced (later)** | **Contextual retrieval** → **RAPTOR** → **LightRAG/GraphRAG** | Layered complexity — each builds on the previous |

### VRAM Budget (24GB GPU)

```
Qwen 2.5 32B AWQ (LLM):           ~20.0 GB
Qwen3-Embedding-0.6B:              ~1.2 GB
BGE-reranker-v2-m3 (INT8):         ~0.7 GB
OS / CUDA overhead:                 ~1.5 GB
────────────────────────────────────────────
Total:                             ~23.4 GB  ✓ fits
```

---

## 2. Architecture Vision: The Deep Research Assistant

The user's scenario: **a researcher developing synthesis through deep thought and exploration, using data sources as a knowledge base.** This is not simple Q&A — it's multi-step investigation, cross-document reasoning, and grounded synthesis.

### The Research Flow

```
Researcher asks a complex question
        │
        ▼
┌─────────────────────────────┐
│  1. PLAN                    │  LLM decomposes into sub-questions,
│     Research Plan Generation│  shows plan to user for approval
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  2. INVESTIGATE             │  For each sub-question:
│     Iterative Retrieval     │  - Hybrid search (BM25 + vector)
│                             │  - Re-rank results
│                             │  - Read & extract relevant passages
│                             │  - Follow cross-references
│                             │  - Identify gaps → refine queries
└──────────┬──────────────────┘
           │ (2-5 rounds per sub-question)
           ▼
┌─────────────────────────────┐
│  3. REFLECT                 │  Self-evaluate findings:
│     Completeness Check      │  - Are all sub-questions answered?
│                             │  - Any contradictions across sources?
│                             │  - Missing perspectives?
│                             │  → If gaps found, loop back to step 2
└──────────┬──────────────────┘
           │
           ▼
┌─────────────────────────────┐
│  4. SYNTHESIZE              │  Combine all findings into a
│     Grounded Report         │  structured report with inline
│                             │  citations to specific documents,
│                             │  pages, and passages
└─────────────────────────────┘
```

### Estimated Latency

| Step | Time | Notes |
|------|------|-------|
| Plan generation | 2-5s | Single LLM call |
| Per sub-question retrieval | 100-300ms | Hybrid search + re-rank |
| Per sub-question generation | 2-5s | LLM reads + synthesizes |
| Reflection | 2-3s | LLM self-evaluation |
| Final synthesis | 5-15s | Longer generation |
| **Total (5 sub-questions, 2 rounds)** | **30-90s** | Acceptable for deep research |

---

## 3. Vector Database

### Decision: pgvector (PostgreSQL extension)

**pgvector is already deployed** — the `pgvector/pgvector:pg16` Docker image is running, the extension is initialized via `docker/postgres/init-pgvector.sql`, `asyncpg` is a dependency in `pyproject.toml`, and SQLAlchemy 2.0 async is the ORM.

### Why Not Alternatives

| Option | Verdict | Key Reason |
|--------|---------|------------|
| **pgvector** | **Winner** | Already deployed, zero new services, full SQL + ACID, first-class SQLAlchemy via `pgvector-python` |
| ChromaDB | Rejected | Adds a second data store, no SQLAlchemy integration, declining market share (14.4% → 8.9%), stability issues reported |
| Qdrant | Rejected | Adds a third service to the Cube, HTTP overhead makes it slower than pgvector at our scale (~52ms vs ~2.5ms), strengths (billions of vectors, GPU indexing) are overkill |
| Milvus Lite | Disqualified | Vendor explicitly states "not for production" — production requires etcd + MinIO |
| LanceDB | Rejected | No HNSW (uses IVF-PQ only), younger ecosystem, no SQLAlchemy integration |
| sqlite-vec | Disqualified | Brute-force only (no ANN indexes), project is migrating away from SQLite |

### Performance at Expected Scale

For 10K documents → ~100K-1M chunks with 1024-dim embeddings:

- **Unfiltered query**: ~2.5ms (HNSW)
- **With metadata filters**: ~5.7ms (v0.8.0 iterative scan)
- **Memory**: ~6-9 GB for 1M × 1024-dim vectors (shared with PostgreSQL)
- **halfvec support**: 16-bit storage for ~50% reduction

### What the Schema Looks Like

```python
from pgvector.sqlalchemy import Vector

class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"))
    parent_chunk_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # parent-child chunking
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector(1024))
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # "Article 7 > Section 7.2"
    page_numbers: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    chunk_type: Mapped[str] = mapped_column(String(50), default="text")    # text, table, list
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

Similarity search is a standard SQLAlchemy query:

```python
stmt = (
    select(DocumentChunk)
    .where(DocumentChunk.document_id.in_(accessible_doc_ids))
    .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
    .limit(10)
)
```

---

## 4. Embedding Models

### Decision: Qwen3-Embedding-0.6B (primary)

| Property | Qwen3-Embedding-0.6B | bge-m3 (fallback) |
|----------|----------------------|-------------------|
| Parameters | 0.6B | 568M |
| Dimensions | 32–1024 (flexible) | 1024 |
| Max tokens | 8,192 | 8,192 |
| VRAM (FP16) | ~1.2 GB | ~1.1 GB |
| MTEB average | 64.3 | ~63-64 |
| License | Apache 2.0 | MIT |
| vLLM support | Yes (≥0.8.5, `task="embed"`) | Yes (requires `--hf-overrides`) |
| Special features | Instruction-aware, flexible dims | Dense + sparse + ColBERT multi-vector |

### Why Qwen3-Embedding-0.6B

- **Best quality-to-size ratio** — competitive with 7B models at 1/12th the parameters
- **Instruction-aware**: Prepend task descriptions like `"Retrieve relevant legal clauses for:"` for 1-5% retrieval improvement
- **8K context**: Handles multi-page contract sections without chunking
- **Flexible dimensions**: Use 512 (fast/small), 768 (balanced), or 1024 (max quality)
- **Qwen ecosystem alignment**: Same family as the LLM
- **Apache 2.0**: No commercial restrictions

### Models Eliminated

| Model | Reason |
|-------|--------|
| bge-en-icl (7B) | ~14GB VRAM — cannot colocate with LLM |
| gte-Qwen2-7B (7.6B) | ~16-32GB VRAM — impossible |
| e5-mistral-7b (7B) | ~14-20GB VRAM — impossible |
| jina-embeddings-v3 | CC-BY-NC-4.0 — non-commercial license |
| all-MiniLM-L6-v2 | 256-token limit, MTEB ~56 — too low quality |

### Deployment Architecture

Run the embedding model as a **separate vLLM instance** (not in the same instance as the LLM):

```bash
# LLM instance
vllm serve qwen2.5-32b-awq --gpu-memory-utilization 0.85 --port 8001

# Embedding instance
vllm serve Qwen/Qwen3-Embedding-0.6B --task embed --gpu-memory-utilization 0.10 --port 8002
```

**Why separate**: Different scaling characteristics (batch ingestion vs. low-latency query), independent lifecycle (vLLM restart for model swap doesn't kill embeddings), avoids KV-cache waste.

**CPU fallback**: If VRAM is too tight, run via `sentence-transformers` on CPU. Query-time latency is ~10-50ms (acceptable). Batch ingestion is ~5-10 min for 10K documents.

### Domain Fine-Tuning Note

General MTEB scores don't reliably predict legal/medical retrieval quality. Start with the base model, then fine-tune on a sample of the actual document corpus using the FlagEmbedding training framework. Fine-tuning 0.6B is feasible on the same GPU when the LLM is idle.

---

## 5. Document Parsing & Understanding

### Decision: Docling (primary) + PyMuPDF4LLM (fast path)

### Format-Specific Parser Selection

```
DOCX  → python-docx  (MIT, excellent structure, ~1MB)
MD    → stdlib + regex
TXT   → stdlib
PDF   → pdfplumber (default) → Docling (complex/scanned) → Unstructured (fallback)
```

### Parser Comparison

| Library | Formats | Table Extraction | Layout Accuracy | License | Air-Gap Size | Speed |
|---------|---------|-----------------|----------------|---------|-------------|-------|
| **Docling** | PDF, DOCX, PPTX, HTML, images | **Best** (TableFormer ML) | **Best** (DocLayNet models) | MIT | ~1.5GB + PyTorch | ~100-200ms/page |
| **PyMuPDF4LLM** | PDF, XPS, EPUB | Basic (heuristic) | Basic | AGPL/Commercial | ~30MB | ~10ms/page |
| **pdfplumber** | PDF | Good (geometric) | Good | MIT | ~10MB | ~50ms/page |
| **Unstructured** | Everything | Moderate-Good | Good (hi_res) | Apache 2.0 | 50MB-5GB | ~300ms-1s/page |
| **python-docx** | DOCX only | Excellent (native XML) | Good (structural) | MIT | ~1MB | Instant |
| **Marker** | PDF only | Good | Good-Excellent | **GPL v3** | 3-5GB | ~200-500ms/page |

### Why Docling

- **Best structure preservation**: Headings, sections, tables, figures, reading order — all extracted into a rich `DoclingDocument` object
- **Built-in hierarchical chunker**: Respects document boundaries (won't split mid-table or mid-section)
- **TableFormer model**: Best-in-class table extraction for complex tables (merged cells, multi-level headers, borderless)
- **MIT license**: No commercial restrictions
- **Active IBM development**: Frequent releases through 2025-2026
- **LlamaIndex/LangChain native integration**

### PyMuPDF4LLM as Fast Path

For simple, well-structured digital PDFs: 100+ pages/second, zero ML models, minimal dependencies. Use as the default when high-fidelity structure parsing isn't needed.

### Multimodal Document Understanding (Future)

For scanned documents, charts, handwritten notes — deploy **Qwen2.5-VL-7B-AWQ** as a document understanding specialist:

- ~6GB VRAM (4-bit quantized) — runs on a second GPU or shares when LLM is idle
- Handles: OCR, table extraction from images, chart reading, diagram understanding
- Use during ingestion as a fallback when Docling's heuristic pipeline can't handle a page

### Visual Document Retrieval: ColPali/ColQwen (Future)

A paradigm shift — embed page images directly, search visually, no OCR/parsing needed:

- **ColQwen2**: Based on Qwen2-VL, multi-vector image embeddings
- ~6GB VRAM, retrieval quality matches/exceeds text-based approaches on visually-rich docs
- Complementary to text-based retrieval, not a replacement
- Evaluate for Stage 5+ when the text pipeline is proven

---

## 6. Chunking Strategies

### Decision: Structure-aware chunking (primary) with hybrid fallbacks

### Strategy Comparison

| Strategy | Retrieval Quality | Legal Docs | Medical Docs | Complexity |
|----------|------------------|-----------|-------------|-----------|
| Fixed-size (512 tokens) | Low | Poor | Poor | Trivial |
| Recursive character | Moderate | Moderate | Moderate | Low |
| **Structure-aware** | **Highest** | **Excellent** | **Excellent** | Medium-High |
| Semantic (embedding boundaries) | High | Good | Good | Medium-High |
| Sentence-window | High | Good (factoid) | Good | Medium |
| Late chunking (Jina) | Very High | Theoretical | Theoretical | High |

### Recommended Hybrid Architecture

**1. Primary: Document-structure-aware chunking**
- Parse documents with Docling to extract heading hierarchy
- Chunk at section boundaries
- Prepend heading path as metadata: `"Article 7: Indemnification > Section 7.2: Limitations"`
- This handles the 80% case for legal and medical documents

**2. Fallback within large sections: Semantic chunking**
- When a structural section exceeds target size (>512 tokens), apply semantic splitting
- Uses embedding similarity to find natural topic breaks
- Avoids arbitrary splitting of long legal clauses

**3. Parent-child chunking**
- **Child chunks** (200-400 tokens): Indexed for retrieval (precise embeddings)
- **Parent chunks** (1000-1500 tokens): Returned for generation (richer context)
- Essential for legal documents where a clause needs surrounding paragraphs for interpretation
- Implemented via `parent_chunk_id` foreign key in the chunks table

**4. Last resort: Recursive character splitting**
- For documents where structure extraction fails (poor OCR, unformatted plain text)

### Why Structure-Aware Wins for Legal/Medical

Legal documents are **inherently hierarchical** (Article > Section > Subsection > Clause). Medical records have **structured fields** (Chief Complaint, History, Assessment, Plan). Respecting these boundaries is the single highest-leverage improvement for retrieval quality. A query about "termination provisions" retrieves the complete termination section with heading context — not an arbitrary 512-token window that starts mid-clause.

---

## 7. Retrieval Architecture

### Decision: Advanced RAG with Hybrid Search + Re-Ranking

```
User Query
    │
    ├──→ [Optional: Query Rewriting via LLM]
    │
    ├──→ BM25 Search (PostgreSQL tsvector / FTS5) ──→ Top 30
    │                                                       │
    ├──→ Dense Vector Search (pgvector HNSW) ──→ Top 30     │
    │                                                       │
    └──→ Reciprocal Rank Fusion ←───────────────────────────┘
              │
              ▼
         Top 20 fused results
              │
              ▼
         Re-ranking (BGE-reranker-v2-m3 or FlashRank)
              │
              ▼
         Top 5 re-ranked results
              │
              ▼
         Parent chunk expansion + deduplication
              │
              ▼
         LLM Generation (Qwen 2.5 32B) with citations
```

### Why Hybrid Search

Pure vector search misses exact legal terminology. A user searches for "termination clause" but the document says "exit provision" — vector search catches this. But when the user searches for "Section 4.2(b)" — only keyword search finds it. **Hybrid search (BM25 + vector) improves recall by 10-25%** over pure vector search on domain-specific corpora.

### Implementation: PostgreSQL tsvector + pgvector

Both live in the same PostgreSQL instance:

```sql
-- BM25-style full-text search
ALTER TABLE document_chunks ADD COLUMN content_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX idx_chunks_fts ON document_chunks USING GIN (content_tsv);

-- Vector similarity search (already via pgvector)
CREATE INDEX idx_chunks_embedding ON document_chunks
    USING hnsw (embedding vector_cosine_ops);
```

Fusion via Reciprocal Rank Fusion (RRF):

```python
def reciprocal_rank_fusion(results_lists: list[list], k: int = 60) -> list:
    scores = defaultdict(float)
    for results in results_lists:
        for rank, doc in enumerate(results):
            scores[doc.id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

### Contextual Retrieval (Anthropic Technique)

At indexing time, prepend a context summary to each chunk before embedding:

```
Original chunk: "The liability shall not exceed $500,000."
Contextualized: "This is from Section 7.2 (Limitation of Liability) of the
Smith-Jones Services Agreement dated March 2024. The liability shall not exceed $500,000."
```

Anthropic reported **49% reduction in retrieval failures** when combined with hybrid search. The context is generated by the local LLM as a batch job during ingestion.

---

## 8. Re-Ranking

### Decision: BGE-reranker-v2-m3 (primary), FlashRank (zero-GPU fallback)

### Comparison

| Model | VRAM | Latency (20 docs) | Quality Gain | Air-Gapped |
|-------|------|--------------------|-------------|------------|
| **BGE-reranker-v2-m3 (INT8)** | ~700MB | 50-150ms GPU | **+5-15% NDCG** | Yes |
| BGE-reranker-base (INT8) | ~350MB | 30-100ms GPU | +5-12% NDCG | Yes |
| **FlashRank** | **0 (CPU-only)** | 50-200ms CPU | +3-8% NDCG | Yes |
| cross-encoder/MiniLM-L-6 | ~90MB | 15-30ms GPU | +3-10% NDCG | Yes |
| RankLLM (via Qwen 32B) | 0 extra | 3-15s | +10-20% NDCG | Yes |
| ColBERTv2 | ~450MB + index | 10-50ms | +8-15% NDCG | Yes |
| Cohere rerank-v3 | N/A | N/A | Benchmark leader | **No (cloud)** |

### Why BGE-reranker-v2-m3

- At ~700MB (INT8), fits comfortably in the VRAM budget alongside LLM + embeddings
- Achieves ~90-95% of Cohere rerank-v3's quality (the industry benchmark) — locally
- 50-150ms latency for 20 passages is acceptable for interactive use
- Can run on CPU as fallback (~500-1500ms, still acceptable)

### FlashRank as Zero-GPU Alternative

If VRAM is too tight: FlashRank runs entirely on CPU, ships as a single pip install with bundled ONNX models (~4-25MB), no PyTorch dependency. Quality is lower but still meaningfully better than no re-ranking.

---

## 9. Orchestration Frameworks

### Decision: LlamaIndex (retrieval core) + Custom FastAPI (agent loop)

### Framework Comparison

| Criterion | LangGraph | LlamaIndex | Haystack | DSPy | Custom FastAPI |
|-----------|-----------|------------|----------|------|---------------|
| **Local LLM** | Good | Good | Good | Good | Native |
| **Air-gapped** | Caution (telemetry) | Good | **Strongest** | Good | Perfect |
| **Async support** | Improving | Partial | **Strong** | Workable | Native |
| **pgvector** | Yes | **Excellent** | Yes | Via extras | Direct |
| **Framework overhead** | ~14ms | ~6ms | ~5.9ms | ~3.5ms | Zero |
| **Token efficiency** | ~2.0k/query | ~1.6k/query | ~1.6k/query | ~2.0k/query | You control |
| **Deep research** | **Strong** | Medium | Medium | Pipeline-only | Full flexibility |
| **Production maturity** | Mature | Mature | **Most mature** | Growing | You own it |
| **License** | MIT | MIT | Apache 2.0 | MIT | N/A |

### Eliminated Frameworks

| Framework | Why Eliminated |
|-----------|---------------|
| **Semantic Kernel** | Azure-centric, Python SDK is second-class, strategic uncertainty (merging into Microsoft Agent Framework) |
| **AutoGen** | Fork fragmentation (microsoft/autogen vs. ag2ai/ag2), same merger uncertainty |
| **CrewAI** | No async support, no pgvector integration, friction in air-gapped environments |
| **smolagents** | Too minimal for production — no async, no pgvector, no structured orchestration |

### Why LlamaIndex + Custom FastAPI

**1. LlamaIndex for retrieval**: Its `PGVectorStore` uses SQLAlchemy async + asyncpg — exactly Vault's existing DB stack. `SubQuestionQueryEngine` handles multi-step decomposition. Best-in-class data ingestion pipeline.

**2. Custom FastAPI for orchestration**: Vault already has async task management (quarantine orchestrator), state machines (training jobs), and streaming (SSE via httpx). Building an iterative research loop on these patterns is natural.

**3. No framework lock-in**: LlamaIndex handles retrieval; the agent loop is yours. If LlamaIndex ever becomes a liability, the retrieval layer is replaceable without rewriting orchestration.

**4. DSPy as optional optimizer**: Layer in later to automatically optimize prompts for sub-question generation, retrieval queries, and answer synthesis.

### Architecture Integration

```
Custom FastAPI Agent Loop (async)
  │
  ├── Research Planner (custom)
  │     └── Decomposes query into sub-questions
  │
  ├── For each sub-question:
  │     ├── LlamaIndex QueryEngine
  │     │     ├── pgvector dense search
  │     │     ├── PostgreSQL FTS keyword search
  │     │     ├── Reciprocal Rank Fusion
  │     │     └── BGE-reranker re-ranking
  │     │
  │     ├── VLLMBackend (existing) for generation
  │     └── Reflection step (custom)
  │           └── Decides if more retrieval needed
  │
  ├── Synthesis (custom)
  │     └── Combines findings into final answer with citations
  │
  └── State persisted in PostgreSQL (existing SQLAlchemy models)
```

---

## 10. Agentic RAG & Deep Research Patterns

### Pattern Comparison

| Pattern | Synthesis Quality | Latency | Complexity | Local Feasibility |
|---------|------------------|---------|-----------|------------------|
| Naive RAG (retrieve → generate) | Low | 3-5s | Low | Easy |
| **Advanced RAG** (hybrid + re-rank) | Good | 5-8s | Medium | Easy |
| **Plan-and-Execute** | Very High | 30-90s | Medium | **Recommended** |
| ReAct (Reasoning + Acting) | Good (short chains) | 10-30s | Low-Medium | Easy |
| **Reflection / Self-Critique** | Very High | 2-3x base | Low | **Recommended** |
| Multi-Query RAG | Good | 3-5x retrieval | Low | Easy |
| HyDE | Moderate | +2-5s | Low | Optional |
| **Contextual Retrieval** | High | None (indexing) | Low | **Recommended** |
| **RAPTOR** (hierarchical summaries) | Very High | None (indexing) | Medium-High | Phase 2 |
| **GraphRAG** (knowledge graph) | **Best for synthesis** | 2-5 min | High | Phase 3 |
| Multi-Agent collaboration | Marginal gain | Sequential | High | **Skip** (single GPU) |
| Self-RAG (fine-tuned tokens) | High | Minimal | High | Skip (models too small) |

### Recommended: Layered Implementation

**Layer 1 — Foundation (implement first):**

1. **Hybrid retrieval**: BM25 + dense vectors in PostgreSQL
2. **Contextual retrieval**: LLM prepends document context to chunks at indexing time (Anthropic technique, 49% retrieval failure reduction)
3. **Cross-encoder re-ranking**: BGE-reranker-v2-m3 after initial retrieval

**Layer 2 — Agentic Reasoning (implement second):**

4. **Plan-and-Execute agent**: For complex queries, generate a research plan with sub-questions, execute each with retrieval, synthesize. Show the plan to the user for approval (like Gemini Deep Research's UX)
5. **Iterative retrieval with reflection**: After initial synthesis, evaluate completeness/accuracy, generate follow-up queries for gaps. Limit to 3-5 iterations.
6. **Tool-calling interface**: Expose retrieval as tools (`search_documents`, `keyword_search`, `read_document`, `list_documents`) so the model flexibly combines strategies

**Layer 3 — Advanced (implement when needed):**

7. **RAPTOR hierarchical indexing**: Build a summarization tree over the corpus for thematic queries ("what are the patterns across all contracts?"). Expensive to build but transformative for synthesis
8. **LightRAG → GraphRAG**: Entity extraction + relationship graph for cross-document connection finding. Start with LightRAG (simpler, incremental indexing); upgrade to full GraphRAG if the corpus is highly interconnected

### What to Skip

- **Multi-agent collaboration**: Single-GPU sequential inference negates parallelism benefits. Use a single agent with role-switching prompts instead
- **Self-RAG fine-tuning**: Available models are 7B/13B — too small for complex synthesis. Approximate the behavior with structured prompting on Qwen 32B
- **Full STORM**: Compute-intensive multi-perspective simulation. Marginal gain over plan-and-execute + reflection for local deployment
- **Neo4j**: Too much operational overhead for a single-node deployment. LightRAG/GraphRAG's built-in graph storage is sufficient

### Deep Research Patterns from Industry

| System | Key Pattern | Applicable Insight |
|--------|------------|-------------------|
| **OpenAI Deep Research** | Dynamic retrieval rounds (50-100+ actions), chain-of-thought for "what to search next" | The iterative "decide → search → evaluate → decide again" loop is the core pattern |
| **Gemini Deep Research** | User-editable research plan before execution; two-model approach (heavy planner, light executor) | Show the plan for approval; use 32B for planning, potentially 8B for individual retrieval steps |
| **Perplexity Pro Search** | 2-3 rounds of query expansion + parallel retrieval + citation-grounded synthesis | Most practical pattern for resource-constrained local deployment |
| **STORM (Stanford)** | Multi-perspective questioning (plaintiff view, defendant view, regulatory view) | Valuable prompt engineering technique — don't need the full framework |
| **GPT-Researcher** | Sub-question agents running in parallel + final report synthesis | Good architecture pattern; implement with async tasks, not a separate framework |

---

## 11. Knowledge Extraction & Graph

### NER: GLiNER (Zero-Shot, Configurable)

**GLiNER** is the standout choice for Vault. Zero-shot NER where you define entity types at runtime:

```python
labels = ["judge", "plaintiff", "defendant", "statute",
          "medical_condition", "medication", "dosage",
          "contract_party", "effective_date", "liability_cap"]
```

- ~400MB, runs on CPU in milliseconds
- No training data needed — entity types are configurable per document collection
- Air-gapped, MIT license

**Supplementary NER:**
- **spaCy** (`en_core_web_trf`): Baseline entities (PERSON, ORG, DATE, MONEY)
- **scispaCy**: Biomedical NER (diseases, chemicals, genes) for medical collections
- **Regex patterns**: Legal citations (`42 U.S.C. § 1983`, `Smith v. Jones, 123 F.3d 456`)

### Relationship Extraction: LLM Batch Processing

Use Qwen 2.5 32B during ingestion to extract structured relationships:

```
Prompt: "From this legal text, extract relationships as (entity1, relation, entity2).
Focus on: signatories, clause cross-references, party obligations, effective dates."
```

Store as knowledge graph triples alongside vector embeddings. Run as background batch jobs.

### Timeline Extraction

High value for legal research — extract date-event pairs:
- **dateparser** for explicit dates
- **LLM** for relative/contextual dates ("within 30 days of filing", "the following Tuesday")
- Store in structured timeline table (date, event, parties, document_id, chunk_id)

### Knowledge Graph (Phase 3)

**LightRAG** as the pragmatic starting point:
- Entity + relationship extraction from chunks
- Lightweight graph storage (no Neo4j needed)
- Dual-mode retrieval: vector search + graph traversal
- **Incremental indexing** — add documents without re-indexing the whole corpus

**GraphRAG** (Microsoft) for advanced cross-document synthesis:
- Community detection + hierarchical summaries
- Enables answering "What are the common themes across these 200 contracts?"
- But: indexing cost is **~50-200 hours of LLM compute** for 10K documents on single GPU
- Defer to Stage 5-6 if the use case demands it

---

## 12. Citation & Provenance

### Chunk-Level Provenance (Required)

Every chunk carries metadata:

```json
{
  "chunk_id": "doc-abc-chunk-42",
  "document_id": "doc-abc",
  "document_title": "Smith v. Jones - Motion for Summary Judgment",
  "page_numbers": [12, 13],
  "section_path": "III. Statement of Facts > B. Timeline of Events",
  "paragraph_indices": [34, 35, 36],
  "chunk_type": "narrative_text",
  "entities": ["John Smith", "ABC Corp", "Section 4.2(b)"]
}
```

### Citation in Generation

Instruct the LLM to cite sources using chunk references:

```
Answer using ONLY the provided sources. For every claim, cite the source
using [Doc: title, Page: X] format. If you cannot find evidence, say so.
```

### Response Schema Extension

Extend the chat completion response with structured citations:

```json
{
  "choices": [{
    "message": {
      "content": "The liability cap was originally $500K [1] but was amended to $1M [2]...",
      "citations": [
        {
          "marker": "[1]",
          "chunk_id": "doc-abc-chunk-42",
          "document_title": "Smith-Jones Services Agreement",
          "page": 12,
          "section": "Section 7.2: Limitation of Liability",
          "evidence_span": "The liability shall not exceed $500,000..."
        }
      ]
    }
  }]
}
```

### Verification (Optional Enhancement)

Post-generation verification using NLI:
- **MiniLM-based NLI model** (~80MB, CPU): Takes (cited_chunk, llm_claim) → entailment/contradiction/neutral
- Flags unsupported claims before sending response to user

---

## 13. VRAM Budget

### Primary Configuration (24GB GPU)

```
Component                          VRAM        Notes
─────────────────────────────────────────────────────
Qwen 2.5 32B AWQ (LLM)           ~20.0 GB    Primary inference, port 8001
Qwen3-Embedding-0.6B              ~1.2 GB    Embedding service, port 8002
BGE-reranker-v2-m3 (INT8)         ~0.7 GB    Via sentence-transformers
OS / CUDA overhead                 ~1.5 GB
─────────────────────────────────────────────────────
Total                             ~23.4 GB    ✓ fits in 24GB
```

### If VRAM Is Tight

Replace BGE-reranker with FlashRank (CPU-only, 0 VRAM):
```
Total without GPU re-ranker:      ~22.7 GB    More headroom
```

Or run embedding model on CPU via sentence-transformers:
```
Total with CPU embedding:         ~22.2 GB    Maximum headroom
Query latency: ~10-50ms on CPU (acceptable)
```

### Dual-GPU Configuration (If Available)

```
GPU 0: Qwen 2.5 32B AWQ (~20 GB) + overhead
GPU 1: Qwen3-Embedding-0.6B (~1.2 GB) + BGE-reranker (~0.7 GB)
        + Qwen2.5-VL-7B-AWQ (~6 GB, for document understanding)
        + ColQwen2 (~6 GB, optional visual retrieval)
```

### Indexing Budget (Background Processing)

All indexing runs as batch jobs when the LLM has spare capacity:
- Contextual retrieval (context generation): ~1-2s per chunk → 10K chunks in ~3-5 hours
- Embedding generation: ~50ms per chunk (GPU) → 10K chunks in ~8 minutes
- RAPTOR tree building: ~2-3x contextual retrieval cost
- GraphRAG entity extraction: ~3-5s per chunk → 10K chunks in ~8-14 hours

---

## 14. Implementation Roadmap

### Phase 1: Foundation (Stage 4a)

**Goal**: Basic RAG — upload documents, search, get cited answers.

| Component | What to Build |
|-----------|--------------|
| Document ingestion | Quarantine → Docling parsing → structure-aware chunking |
| Embedding service | Qwen3-Embedding-0.6B via vLLM on port 8002 |
| Vector store | pgvector schema, HNSW index, cosine similarity queries |
| Hybrid search | PostgreSQL tsvector (BM25) + pgvector (dense) + RRF fusion |
| Re-ranking | BGE-reranker-v2-m3 or FlashRank |
| Basic RAG endpoint | `POST /vault/documents/search` → hybrid retrieve → generate with citations |
| Document CRUD | 8 endpoints from vault-api-spec.md Section 4 |

### Phase 2: Contextual + Agentic (Stage 4b)

**Goal**: Deep research with iterative retrieval and synthesis.

| Component | What to Build |
|-----------|--------------|
| Contextual retrieval | LLM batch job to prepend context summaries at indexing time |
| Parent-child chunks | Dual-level indexing (small for retrieval, large for generation) |
| Plan-and-Execute agent | Research planner → sub-question decomposition → iterative retrieval |
| Reflection loops | Self-evaluation after synthesis, follow-up queries for gaps |
| Tool-calling interface | `search_documents`, `keyword_search`, `read_document`, `list_documents` |
| Research session API | Persist research state, allow user to guide the investigation |

### Phase 3: Knowledge Graph + Advanced (Stage 5+)

**Goal**: Cross-document reasoning and thematic synthesis.

| Component | What to Build |
|-----------|--------------|
| NER pipeline | GLiNER + scispaCy + regex for entity extraction during ingestion |
| Knowledge graph | LightRAG for entity-relationship storage and graph-augmented retrieval |
| RAPTOR indexing | Hierarchical summarization tree for thematic queries |
| Timeline extraction | Structured date-event storage for legal timeline queries |
| Visual retrieval | ColQwen2 for page-level visual search (complement to text) |
| GraphRAG | Full Microsoft GraphRAG if cross-document synthesis demand warrants it |

### Service Architecture on the Cube

```
┌─────────────────────────────────────────────────┐
│  Caddy (:443)                                   │
│  ├── /v1/*, /vault/*, /ws/* → vault-backend     │
│  └── /*                     → vault-frontend     │
├─────────────────────────────────────────────────┤
│  vault-backend (:8000)     — FastAPI, main API   │
│  vault-embeddings (:8002)  — Qwen3-Embed-0.6B   │
│  vault-vllm (:8001)        — Qwen 2.5 32B AWQ   │
│  postgresql (:5432)        — pgvector + FTS      │
│  vault-frontend (:3001)    — Next.js             │
└─────────────────────────────────────────────────┘
```

All services run as systemd units, managed by Ansible, on the single node.
