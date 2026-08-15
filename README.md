# Pintell

**Read what a tender requires, compare it to what a vendor has, and say whether
they qualify — with the sentence that proves it.**

Pintell mirrors World Bank Group procurement notices for Central Asia and the
CIS, extracts the qualification criteria out of each tender and the documents it
links to, and evaluates a vendor's profile against them. Every criterion it
shows carries a quote copied verbatim from the source document, located on the
page it came from.

---

## The problem

A World Bank tender does not state its requirements in a field. It states them
in prose, across a notice and a Terms of Reference the notice merely links to,
in English or Russian, on a borrower's file server that stops answering once the
tender closes. A vendor deciding whether to bid has to read forty pages to find
out that the minimum turnover disqualifies them on page 31.

Automating that reading is easy to do badly. A language model asked "what does
this tender require?" will answer fluently for a tender it never read, and a
wrong threshold costs a real company a real bid. So the engineering problem here
is not extraction. It is **making extraction checkable**.

## The claim, and how it is enforced

Three rules run through the whole codebase. They are the reason to trust the
output, and they are enforced by structure rather than by prompting.

**The model reads; it never decides.** Extraction returns a structured
requirement — a comparison, a threshold, a unit. `apps/compliance/expressions.py`
then evaluates it with ordinary arithmetic and three-valued logic, in a module
with no Django dependency and no model call in it. A verdict can be reproduced
by hand, and the same input always produces the same answer.

**No claim without a verbatim quote.** Every extracted requirement carries
`evidence_quote`, copied character for character from the source. A verifier
searches the source for that exact string; a quote it cannot find is kept, marked
`NOT_FOUND`, and permanently barred from reaching a verdict. That turns
hallucination from a worry into a measured rate — **93.8% of extracted quotes
verify against their source** on the current corpus, and the 6.2% that do not are
visible rather than silently shipped. The quote is never translated, because the
verifier searches the borrower's document in the borrower's language.

**No fact about procurement is written from memory.** Clause numbers, per-method
requirements, joint-venture share percentages — a model states these confidently
and wrongly. When the code needs one and no source has been read, the question is
logged rather than guessed, the value stays behind a `NEEDS_VERIFICATION`
comment, and the uncertainty is shown in the product. An entire architectural
layer was cancelled under this rule: its evidence would have been a clause
reference rather than a quote, which is the one kind of row the verifier could
never check.

---

## What it does

| | |
|---|---|
| **Mirror** | 25,000+ procurement notices, synced every 30 minutes, classified by direction and audience. Contract awards parsed per company — winner, evaluated, rejected |
| **Harvest** | The Terms of Reference and bidding documents notices link to, mirrored before the links expire. Content-addressed, SSRF-guarded, rate-limited per host |
| **Extract** | Three layers, cheapest first: deterministic rules, then a schema-constrained model over the notice body, then the same over a mirrored document. Each layer is told what the last one already found |
| **Evaluate** | A vendor profile against the extracted criteria. Two fractions, never one: what is *established*, and what settling the unknowns could reach |
| **Show the source** | A mirrored PDF indexed to line geometry, with the stored quote located in it by exact string match. No match means no highlight — never a guessed one |
| **Search and ask** | Hybrid retrieval (dense + lexical, fused by rank) over notices and documents, and a chat that may only cite an index into the passages it was shown. An unsupported claim is dropped and counted |
| **Operate** | A staff console for coverage, cost, extraction runs and per-requirement review, separate from the public site |

Interface in **Uzbek, Russian and English**; extracted criteria are labelled in
all three, while the evidence stays in the document's own language.

## Architecture

```
World Bank APIs ──► sync ──► PostgreSQL ──► REST API ──► React (public)
                     │                          │
                     ├── harvest ──► documents ─┤
                     │                          └──► React (operator console)
                     ├── extract ──► L1 rules
                     │               L2 model over the notice
                     │               L3 model over the document
                     │                  └─► grounding ──► requirements
                     └── index ────► Qdrant ──► search · chat
```

| Layer | Reads | Cost |
|---|---|---|
| **L1** | The notice body, with deterministic rules | free, ~1 ms |
| **L2** | The notice body, model call under a JSON schema | metered |
| **L3** | A mirrored or vendor-supplied tender document | metered, deepest |

**Stack.** Python 3.13 · Django 5.2 · Django REST Framework · Celery + Redis ·
PostgreSQL 17 · Qdrant · React 19 + TypeScript + Vite · Docker Compose ·
GitHub Actions.

**Models, chosen per task rather than by default.** Claude under
`output_config.format` json_schema for extraction and classification — the
schema makes a malformed answer a transport-level impossibility, so the prompt
only has to enforce the one thing a schema cannot. Gemini embeddings for the
semantic index over *published* documents; `services/embedding.py` is the seam
where a local model replaces it the moment vendor text becomes a query, which is
the point at which the data-residency requirement bites.

---

## Quick start

```bash
cp .env.example .env    # set DJANGO_SECRET_KEY and POSTGRES_PASSWORD
docker compose up --build
```

| | |
|---|---|
| Public site | http://localhost:3000 |
| Operator console | http://localhost:3001 |
| API | http://localhost:8000/api/tenders/ |
| Health | http://localhost:8000/api/health/ |

No API key is required to run it. Without one the metered layers record "no API
key configured" and the deterministic layer still produces requirements — the
whole stack degrades instead of failing, which is the same contract the harvester
holds for a dead link.

```bash
# after the first sync
docker compose exec backend python manage.py enrich_tenders --status
docker compose exec backend python manage.py harvest_documents --status
docker compose exec backend python manage.py extract_requirements --status
```

## Tests

```bash
docker compose exec backend python manage.py test
```

**1,192 tests**, run serially against a real PostgreSQL. `.github/workflows/ci-cd.yml`
runs the suite, typechecks and builds both front ends, and builds all three
images on every push — a test-only gate with nothing wired to a server.

Two conventions worth knowing before reading them. Tests carry docstrings naming
the *behaviour* under test rather than the mechanics. And nothing in the suite
calls a model: layers are installed as scripted modules for the duration of a
test, so "the layer is not installed" and "the model refused" are exercised for
real rather than mocked around.

## Layout

```
backend/apps/
  tenders/       mirror, classification, harvesting, contacts, awards
  compliance/    extraction stack, decision engine, scoring, source viewer
  experts/       expert directory and its taxonomy
  rag_indexer/   chunking, embedding, hybrid retrieval, chat
  adminpanel/    operator console API
frontend/        public site (React + Vite)
admin-frontend/  operator console
```

## Documentation

This README is the single source of documentation for the project.

The codebase holds a deliberately high comment standard: comments explain *why*
a decision was made, especially where the obvious choice was rejected and what
the measurement forced instead. `apps/tenders/deadlines.py`,
`apps/tenders/companies.py` and `apps/compliance/l3.py` are the register.

## Honest limits

- **Documents are indexed on demand, not continuously.** New notices are indexed
  by a scheduled job; the mirrored document backlog is a hand-run pass.
- **Two components ship switched off on purpose** — a cross-encoder reranker and
  a fast model tier. Both are implemented; one needs a third party nobody has
  cleared legally and the other measured badly on Uzbek. They are decisions, not
  defaults.
- **Some tenders are for an individual, not a firm**, and a firm profile cannot
  answer requirements about a person. Logged as an open question rather than
  scored wrongly.
