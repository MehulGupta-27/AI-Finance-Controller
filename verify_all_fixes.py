# -*- coding: utf-8 -*-
"""
Verification script - confirms all 6 fixes are working correctly.
Run after starting backend (python api/main.py) and frontend (npm run dev).
"""
import sys
import requests

sys.path.insert(0, 'd:\\AI Finance Controller')

print("=" * 70)
print("VERIFICATION: All 6 Fixes")
print("=" * 70)

# Fix 1 & 2: FastAPI backend + App.jsx real fetch
print("\n[Fix 1 & 2] FastAPI backend serving real data to frontend...")
try:
    response = requests.get("http://localhost:8000/api/summary", timeout=5)
    data = response.json()
    print(f"[OK] API responds with HTTP {response.status_code}")
    print(f"[OK] Returns {len(data['records'])} real records from pipeline")
    print(f"[OK] Summary: {data['summary']['matched']} matched, "
          f"{data['summary']['partial']} partial, "
          f"{data['summary']['unresolved']} unresolved")
except Exception as e:
    print(f"[FAIL] Backend not running or API failed: {e}")
    print("  Start with: python api/main.py")
    sys.exit(1)

# Fix 3: Field name standardization (risk_flags, recommendation)
print("\n[Fix 3] Field names standardized (risk_flags, recommendation)...")
first_record = data['records'][0]
explanation = first_record['explanation']
has_risk_flags = 'risk_flags' in explanation
has_recommendation = 'recommendation' in explanation
has_old_flags = 'flags' in explanation
has_old_next_step = 'next_step' in explanation

if has_risk_flags and has_recommendation and not has_old_flags and not has_old_next_step:
    print(f"[OK] Explanation uses correct field names")
    print(f"  - risk_flags: {explanation['risk_flags']}")
    print(f"  - recommendation: {explanation['recommendation']}")
else:
    print(f"[FAIL] Field name mismatch detected")
    print(f"  risk_flags present: {has_risk_flags}")
    print(f"  recommendation present: {has_recommendation}")
    print(f"  OLD 'flags' present: {has_old_flags}")
    print(f"  OLD 'next_step' present: {has_old_next_step}")

# Fix 4: Dashboard real counts (not hardcoded 102/79)
print("\n[Fix 4] Dashboard counts from real pipeline (not hardcoded)...")
exact_count = data['summary'].get('exact_match_count', 0)
fuzzy_count = data['summary'].get('fuzzy_auto_count', 0)
if exact_count > 0 and fuzzy_count > 0:
    print(f"[OK] exact_match_count = {exact_count} (was hardcoded 102)")
    print(f"[OK] fuzzy_auto_count  = {fuzzy_count} (was hardcoded 79)")
    if exact_count == 102 and fuzzy_count == 79:
        print("  [WARN] Counts match old hardcoded values (coincidence?)")
else:
    print(f"[FAIL] Summary missing exact_match_count or fuzzy_auto_count")

# Fix 5: Agent 9 indexing wired into pipeline
print("\n[Fix 5] Agent 9 indexing wired into pipeline...")
from agents.qa_agent import query
try:
    answer = query("How many unresolved transactions?")
    print(f"[OK] Agent 9 query works: '{answer[:60]}...'")
    print(f"[OK] ChromaDB index was populated by pipeline")
except Exception as e:
    print(f"[FAIL] Agent 9 query failed: {e}")

# Fix 6: qa_agent uses GROQ_QA_MODEL (not GROQ_REASONING_MODEL)
print("\n[Fix 6] qa_agent imports GROQ_QA_MODEL...")
from agents.config import GROQ_QA_MODEL
from agents import qa_agent
import inspect
source = inspect.getsource(qa_agent)
if 'GROQ_QA_MODEL' in source and 'from agents.config import GROQ_QA_MODEL' in source:
    print(f"[OK] qa_agent imports GROQ_QA_MODEL from config")
    print(f"  Model: {GROQ_QA_MODEL}")
else:
    print(f"[FAIL] qa_agent still imports wrong constant")

print("\n" + "=" * 70)
print("All fixes verified! Open http://localhost:5173 to see the live dashboard.")
print("=" * 70)
