"""
agents/core - Core reconciliation agents

Contains the 8 main agents that perform the reconciliation pipeline:
1. ingestion_agent - Load and standardize CSV data
2. exact_match_agent - Find perfect order ID matches
3. fuzzy_match_agent - Find approximate matches by amount/date
4. llm_reasoning_agent - AI-powered matching for complex cases
5. verifier_agent - Cross-validate and verify agent decisions
6. classifier_agent - Classify records and generate explanations
7. reporting_agent - Generate reports and cash flow forecasts
8. qa_agent - Natural language Q&A over reconciled data
"""
