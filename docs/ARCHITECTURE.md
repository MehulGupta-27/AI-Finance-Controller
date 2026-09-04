# AI Finance Controller - Architecture

## Overview

AI Finance Controller is an **automated payment reconciliation system** that matches online payment gateway records (Razorpay) with bank deposits and internal ledger entries. It uses a multi-agent AI pipeline to automatically verify transactions and flag issues for human review.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         DATA SOURCES                            │
├─────────────────────────────────────────────────────────────────┤
│  1. Razorpay Export (CSV)    - Online payment gateway records   │
│  2. Bank Statement (CSV)     - Actual bank deposits             │
│  3. Internal Ledger (CSV)    - Your accounting system records   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    DATA INGESTION (Agent 1)                     │
├─────────────────────────────────────────────────────────────────┤
│  • Loads CSV files                                              │
│  • Validates data quality                                       │
│  • Standardizes formats (dates, amounts, text)                  │
│  • Creates canonical records                                    │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    MATCHING PIPELINE                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  • Finds perfect matches (order ID in bank narration)     │  │
│  │  • Highest confidence (95-98%)                            │  │
│  │  Result: MATCHED                                          │  │
│  └──────────────────────────────────────────────────────────┘   │
│                              ↓                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Agent 3: FUZZY MATCH                                     │  │
│  │  • Matches on amount + date proximity                     │  │
│  │  • Handles minor variations                               │  │
│  │  • Confidence: 85-94%                                     │  │
│  │  Result: MATCHED (if confidence ≥ threshold)              │  │
│  └──────────────────────────────────────────────────────────┘   │
│                              ↓                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Agent 4: AI REASONING MATCH                              │  │
│  │  • Uses LLM (Groq/llama-3.3-70b) for complex cases       │   │
│  │  • Understands merchant aliases, delayed settlements      │  │
│  │  • Provides detailed reasoning                            │  │
│  │  • Confidence: 75-90%                                     │  │
│  │  Result: MATCHED or UNRESOLVED                            │  │
│  └──────────────────────────────────────────────────────────┘   │
│                              ↓                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Agent 5: SKIP LOGIC                                      │  │
│  │  • Identifies valid non-matches:                          │  │
│  │    - Payment failed (refunded to customer)                │  │
│  │    - Awaiting settlement (bank hasn't deposited yet)      │  │
│  │  Result: PARTIAL_VALID                                    │  │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  VERIFICATION (Agent 6)                          │
├─────────────────────────────────────────────────────────────────┤
│  • Cross-validates all agent results                             │
│  • Resolves disagreements                                        │
│  • Assigns final status and confidence                           │
│  • Generates plain English explanations                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  ROUTING (Agent 7)                               │
├─────────────────────────────────────────────────────────────────┤
│  • Routes to: AUTO_APPROVED, IN_PROGRESS, or NEEDS_REVIEW       │
│  • Builds detailed explanations with:                            │
│    - Headline (status + confidence)                              │
│    - Checklist (what passed/failed)                              │
│    - Recommendation (what to do next)                            │
│    - Risk flags (if any)                                         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  REPORTING (Agent 8)                             │
├─────────────────────────────────────────────────────────────────┤
│  • Generates JSON report with all results                        │
│  • Saves to outputs/reports/                                     │
│  • Includes summary statistics                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  Q&A INDEXING (Agent 9)                          │
├─────────────────────────────────────────────────────────────────┤
│  • Indexes all transaction data in ChromaDB (vector database)    │
│  • Enables natural language queries like:                        │
│    "Show me all high-value unmatched payments"                   │
│    "What happened with order #12345?"                            │
│  • Uses semantic search for intelligent answers                  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      BACKEND API                                 │
├─────────────────────────────────────────────────────────────────┤
│  FastAPI Server (port 8000)                                      │
│  • GET /api/summary - Returns reconciliation results            │
│  • POST /api/qa - Answers questions about transactions           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    FRONTEND UI                                   │
├─────────────────────────────────────────────────────────────────┤
│  React + Vite (port 5173)                                        │
│  • Dashboard - Summary stats and counts                          │
│  • Review Queue - List of all transactions with filters          │
│  • Record Detail - Detailed view of individual transactions      │
│  • Q&A Chat - Ask questions in natural language                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
AI Finance Controller/
│
├── agents/                    # Core reconciliation logic
│   ├── pipeline.py           # Main orchestrator - runs all agents in sequence
│   ├── ingestion_agent.py    # Agent 1: Loads and standardizes CSV data
│   ├── exact_match_agent.py  # Agent 2: Finds perfect matches
│   ├── fuzzy_match_agent.py  # Agent 3: Finds approximate matches
│   ├── llm_reasoning_agent.py # Agent 4: AI-powered complex matching
│   ├── verifier_agent.py     # Agent 6: Cross-validates results
│   ├── router.py             # Agent 7: Routes and explains decisions
│   ├── reporting_agent.py    # Agent 8: Generates reports
│   ├── qa_agent.py           # Agent 9: Q&A system with vector search
│   ├── data_loader.py        # CSV parsing utilities
│   ├── llm_provider.py       # LLM API interface (Groq)
│   ├── config.py             # Configuration (models, thresholds, API keys)
│   ├── as_of_date.py         # Date simulation for testing
│   └── audit_logger.py       # Tracks all decisions for compliance
│
├── api/                       # Backend API server
│   ├── main.py               # FastAPI application with /api/summary and /api/qa
│   └── __init__.py
│
├── frontend/                  # React UI
│   ├── src/
│   │   ├── App.jsx           # Main app component
│   │   ├── components/
│   │   │   ├── Dashboard.jsx      # Summary stats view
│   │   │   ├── ReviewQueue.jsx    # Transaction list with filters
│   │   │   ├── RecordDetail.jsx   # Detailed transaction view
│   │   │   └── QAChat.jsx         # Natural language Q&A interface
│   │   └── main.jsx          # Entry point
│   ├── package.json          # Dependencies (React, Vite)
│   └── vite.config.js        # Vite configuration
│
├── data/                      # Data files
│   ├── raw_100/              # Sample dataset (100 records)
│   │   ├── razorpay_export.csv
│   │   ├── bank_statement.csv
│   │   └── internal_ledger.csv
│   ├── ground_truth/         # Test data with known correct answers
│   │   ├── ground_truth.json
│   │   └── ground_truth_110.json
│   └── generator/            # Scripts to generate test data
│       └── generate_dataset.py
│
├── db/                        # Local databases
│   ├── audit_log.db          # SQLite: tracks all agent decisions
│   └── llm_cache.db          # SQLite: caches LLM responses
│
├── chroma_db/                 # Vector database for Q&A
│   └── (ChromaDB files)      # Stores embeddings of transaction data
│
├── outputs/                   # Generated reports
│   └── reports/              # JSON reports from pipeline runs
│
├── tests/                     # Test suite
│   ├── test_agent5_skip_condition.py    # Tests skip logic
│   ├── test_agent_disagreement.py       # Tests conflict resolution
│   ├── test_as_of_date.py              # Tests date simulation
│   ├── test_record_count_invariant.py  # Tests data integrity
│   └── test_three_state_output.py      # Tests status classification
│
├── docs/                      # Documentation
│   ├── ARCHITECTURE.md       # This file - system overview
│   ├── HOW_IT_WORKS.md       # Detailed agent explanations
│   ├── DATA_GUIDE.md         # Data format and field descriptions
│   ├── BUILD_SPEC.md         # Original requirements
│   ├── PROGRESS_AUDIT.md     # Development history
│   ├── PLAIN_ENGLISH_GUIDE.md # User-facing explanation guide
│   └── IMPROVEMENTS_SUMMARY.md # Recent enhancements
│
├── .env                       # API keys (Groq API key)
├── .gitignore                # Git ignore rules
├── requirements.txt          # Python dependencies
├── README.md                 # Project overview
└── verify_all_fixes.py       # Integration test script
```

---

## Data Flow

### 1. Input (CSV Files)

**Razorpay Export:**
- Payment gateway records
- Fields: order_id, customer, amount, status, captured_at

**Bank Statement:**
- Actual deposits received
- Fields: date, description (narration), debit, credit

**Internal Ledger:**
- Your accounting system records
- Fields: date, customer, notes, debit, credit

### 2. Processing (Pipeline)

Each CSV record flows through 9 agents:

```
Razorpay Record → Ingestion → Exact Match → Fuzzy Match → LLM Reasoning 
                                                              ↓
                                                         Skip Logic
                                                              ↓
                                        Verification → Router → Report → Q&A Index
```

### 3. Output (JSON + UI)

**JSON Report:**
```json
{
  "summary": {
    "total_records": 110,
    "exact_match_count": 68,
    "fuzzy_auto_count": 14,
    "needs_review_count": 28
  },
  "records": [
    {
      "order_id": "order_12345",
      "customer": "Rajesh Kumar",
      "amount": 2154.58,
      "status": "AUTO_APPROVED",
      "sub_reason": "exact_match_gateway",
      "confidence": 0.98,
      "explanation": {
        "headline": "AUTO-APPROVED — Perfect match found — 98% sure this is correct",
        "checklist": [...],
        "recommendation": "No action needed...",
        "risk_flags": []
      }
    }
  ]
}
```

**UI View:**
- Dashboard shows summary stats
- Review Queue shows all records with filters
- Record Detail shows full explanation
- Q&A Chat answers natural language questions

---

## Agent Details

### Agent 1: Data Ingestion
**Purpose:** Load and standardize data  
**Input:** 3 CSV files  
**Output:** CanonicalRecord objects  
**Logic:** Parse, validate, normalize dates/amounts/text

### Agent 2: Exact Match
**Purpose:** Find perfect matches  
**Input:** Razorpay record  
**Output:** Match or None  
**Logic:** Search for order_id in bank narration using regex

### Agent 3: Fuzzy Match
**Purpose:** Find approximate matches  
**Input:** Unmatched records from Agent 2  
**Output:** Match with confidence score or None  
**Logic:** 
- Match amount exactly (±0.01)
- Match date within ±3 days
- Score based on date proximity

### Agent 4: LLM Reasoning
**Purpose:** AI-powered complex matching  
**Input:** Unmatched records from Agent 3  
**Output:** Match with reasoning or None  
**Logic:**
- Send transaction details to LLM (Groq llama-3.3-70b)
- LLM analyzes merchant aliases, settlement delays, garbled narrations
- Returns confidence + human-readable reasoning

### Agent 5: Skip Logic
**Purpose:** Identify valid non-matches  
**Input:** All records  
**Output:** Skip status (failed/awaiting) or None  
**Logic:**
- Check if payment failed (refunded)
- Check if awaiting settlement (no bank record yet)

### Agent 6: Verifier
**Purpose:** Validate and cross-check  
**Input:** All agent results  
**Output:** Final status + confidence  
**Logic:**
- Check for agent disagreements
- Validate confidence thresholds
- Ensure data integrity

### Agent 7: Router
**Purpose:** Classify and explain  
**Input:** Verification results  
**Output:** Routed status + explanation  
**Logic:**
- Route to AUTO_APPROVED, IN_PROGRESS, or NEEDS_REVIEW
- Build plain English explanation with:
  - Headline (status + confidence)
  - Checklist (what passed/failed)
  - Recommendation (next steps)
  - Risk flags (if any)

### Agent 8: Reporting
**Purpose:** Generate output  
**Input:** All routed results  
**Output:** JSON report file  
**Logic:**
- Aggregate statistics
- Format for API consumption
- Save to disk

### Agent 9: Q&A
**Purpose:** Enable natural language queries  
**Input:** All transaction data  
**Output:** ChromaDB index  
**Logic:**
- Generate embeddings using sentence-transformers
- Store in vector database
- Answer queries using semantic search

---

## Technology Stack

### Backend
- **Python 3.11+** - Core language
- **FastAPI** - REST API framework
- **Groq API** - LLM provider (llama-3.3-70b-versatile)
- **ChromaDB** - Vector database for semantic search
- **SQLite** - Audit logging and LLM caching
- **Pandas** - CSV processing
- **sentence-transformers** - Text embeddings

### Frontend
- **React 18** - UI framework
- **Vite** - Build tool
- **CSS Modules** - Styling
- **Fetch API** - Backend communication

### Testing
- **pytest** - Test framework
- **Ground truth datasets** - Known correct answers

---

## Configuration

### Environment Variables (.env)
```
GROQ_API_KEY=your_groq_api_key_here
```

### Key Parameters (agents/config.py)
```python
GROQ_REASONING_MODEL = "llama-3.3-70b-versatile"
GROQ_QA_MODEL = "llama-3.3-70b-versatile"
FUZZY_AUTO_APPROVE_THRESHOLD = 0.85
EXACT_MATCH_CONFIDENCE = 0.98
OVERDUE_SETTLEMENT_DAYS = 5
```

---

## Running the System

### 1. Run Full Pipeline
```powershell
python agents\pipeline.py
```
- Processes CSV files in data/raw_100/
- Generates report in outputs/reports/
- Indexes data in ChromaDB

### 2. Start Backend API
```powershell
python api\main.py
```
- Runs on http://localhost:8000
- Endpoints:
  - GET /api/summary - Returns reconciliation results
  - POST /api/qa - Answers questions

### 3. Start Frontend
```powershell
cd frontend
npm run dev
```
- Runs on http://localhost:5173
- Dashboard, Review Queue, Q&A interface

### 4. Interactive Q&A
```powershell
python agents\qa_agent.py --interactive
```
- Ask questions directly in terminal

---

## Key Design Decisions

### 1. Multi-Agent Architecture
**Why:** Each agent has a single, clear responsibility. Easy to test, debug, and improve individually.

### 2. Hybrid Matching (Template + LLM)
**Why:** 
- Templates for simple cases (fast, consistent, free)
- LLM for complex cases (detailed, context-aware)
- Best of both worlds

### 3. Three-State Output
**Why:** Clear decision boundaries
- AUTO_APPROVED: High confidence, no review needed
- IN_PROGRESS: Waiting for external event (settlement)
- NEEDS_REVIEW: Low confidence or issues found

### 4. Plain English Explanations
**Why:** Non-technical reviewers need to understand AI decisions
- No technical jargon
- Specific to each transaction
- Actionable recommendations

### 5. Confidence Scores Everywhere
**Why:** Transparency builds trust
- Reviewers see how sure the AI is
- Can prioritize low-confidence cases
- Visible in headlines and UI

### 6. Vector Database for Q&A
**Why:** Semantic search understands intent
- "High-value unmatched" finds relevant records even if terms don't match exactly
- Natural language interface for non-technical users

### 7. Audit Logging
**Why:** Compliance and debugging
- Every agent decision is logged
- Traceable to specific rules and evidence
- Required for financial systems

---

## Security & Compliance

### Data Privacy
- All data processed locally (no cloud storage)
- LLM API calls don't store transaction data (Groq policy)
- SQLite databases encrypted at rest (optional)

### Audit Trail
- Every decision logged in db/audit_log.db
- Includes: timestamp, agent, decision, evidence, confidence
- Immutable log (append-only)

### Access Control
- Frontend fetches data via API (future: add authentication)
- No direct database access from UI

---

## Performance

### Throughput
- **110 records in ~15-30 seconds** (depending on LLM calls)
- Agent 2 (exact): <1ms per record
- Agent 3 (fuzzy): ~5ms per record
- Agent 4 (LLM): 300-800ms per record (only for complex cases)

### Caching
- LLM responses cached in db/llm_cache.db
- Identical queries return instantly
- Reduces API costs by 70-90% on repeated runs

### Scalability
- Current: 100-1000 records/day (single-threaded)
- Future: Parallel agent execution for 10,000+ records/day

---

## Future Enhancements

### Planned
1. **Real-time reconciliation** - Process transactions as they arrive
2. **Multi-tenant support** - Handle multiple businesses
3. **Custom rules engine** - Let users define matching rules via UI
4. **Webhook integrations** - Auto-fetch from Razorpay/bank APIs
5. **Machine learning** - Learn from reviewer corrections

### Under Consideration
1. **Multi-currency support** - Handle USD, EUR, etc.
2. **Partial matching** - Split/combined payments
3. **Automated refunds** - Detect and process refunds automatically
4. **Mobile app** - iOS/Android for on-the-go reviews

---

## Troubleshooting

### Common Issues

**Pipeline fails with "GROQ_API_KEY not found"**
- Solution: Add API key to .env file

**Frontend shows "Failed to fetch"**
- Solution: Ensure backend is running on port 8000

**ChromaDB errors**
- Solution: Delete chroma_db/ folder and re-run pipeline

**Low match rates**
- Solution: Check CSV field mappings in config.py

---

## Contributing

### Adding a New Agent
1. Create agents/new_agent.py
2. Implement main logic function
3. Add to pipeline.py sequence
4. Write tests in tests/test_new_agent.py
5. Update this architecture doc

### Modifying Matching Logic
1. Edit relevant agent file (exact/fuzzy/llm)
2. Update tests to cover new cases
3. Run full test suite: `pytest tests/`
4. Update ground truth if behavior changes

---

## Support

For questions or issues:
1. Check docs/HOW_IT_WORKS.md for detailed explanations
2. Review test files for examples
3. Check audit logs in db/audit_log.db for decision trail

---

**Last Updated:** 2026-09-03  
**Version:** 1.0  
**Status:** Production-ready
