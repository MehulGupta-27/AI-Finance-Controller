# AI Finance Controller

Automated reconciliation system for a Razorpay-based business. Matches payments across three data sources — internal ledger, Razorpay export, and bank statement — and classifies every transaction as MATCHED, PARTIAL, or UNRESOLVED.

## What it does

Every day, three files come in:
- **Internal Ledger** — what the business recorded as sold
- **Razorpay Export** — what the payment gateway captured
- **Bank Statement** — what actually landed in the bank account

These three rarely line up cleanly. Bank narrations are garbled, settlements arrive days late, refunds change amounts, and some payments fail entirely. This system reconciles all of it automatically using a 9-agent pipeline.

## Tech Stack

| Layer | Technology |
|---|---|
| Pipeline / Agents | Python 3.11 |
| LLM Reasoning (Agent 4) | Groq — `openai/gpt-oss-20b` |
| LLM Verification (Agent 5) | Groq — `openai/gpt-oss-120b` |
| Q&A Semantic Search | ChromaDB + sentence-transformers |
| API | FastAPI |
| Frontend | React + Vite |
| Fuzzy Matching | rapidfuzz |
| Optimal Assignment | scipy (Hungarian algorithm) |

## Project Structure

```
agents/
├── core/           9 agent files (ingestion → cash flow)
├── utils/          config, llm provider, data loader, audit logger
└── pipeline.py     full end-to-end runner

api/
└── main.py         FastAPI endpoints

frontend/
└── src/            React dashboard + review queue + Q&A chat

data/
├── raw/            550-record full dataset
└── raw_100/        110-record dev dataset

tests/              6 pytest test files
docs/               ARCHITECTURE.md, DATA_GUIDE.md
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run pipeline (110-record dev set)
python agents/pipeline.py

# Run pipeline (full 550-record set)
python agents/pipeline.py --data data/raw

# Start API server
uvicorn api.main:app --reload

# Start frontend (separate terminal)
cd frontend && npm run dev

# Run tests
pytest tests/
```

## Output Statuses

| Status | Meaning |
|---|---|
| **MATCHED** | All three sources confirmed — transaction fully reconciled |
| **PARTIAL** | Partially confirmed — e.g. payment captured but bank deposit not yet arrived |
| **UNRESOLVED** | Needs human review — conflicting signals or missing data |

## Docs

- `docs/ARCHITECTURE.md` — all 9 agents explained with examples and data flow
- `docs/DATA_GUIDE.md` — CSV files, every field explained, how they link together
