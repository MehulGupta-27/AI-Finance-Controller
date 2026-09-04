#!/usr/bin/env python
"""Debug Q&A responses."""
import requests
import json

# Test 1: Simple greeting
print("=== Test 1: 'hi' ===")
r1 = requests.get('http://localhost:8000/api/qa', params={'q': 'hi'}, timeout=30)
data1 = r1.json()
print(f"Answer: {data1['answer'][:150]}")
print(f"Records returned: {len(data1['records'])}")
if data1['records']:
    print(f"First record status: {data1['records'][0]['status']}")
    print(f"First record notes: {data1['records'][0]['notes'][:50] if data1['records'][0]['notes'] else 'N/A'}")

# Test 2: Gym payments
print("\n=== Test 2: 'show all gym payments' ===")
r2 = requests.get('http://localhost:8000/api/qa', params={'q': 'show all gym payments'}, timeout=30)
data2 = r2.json()
print(f"Answer: {data2['answer'][:200]}")
print(f"Records returned: {len(data2['records'])}")
if data2['records']:
    print("\nFirst 3 records:")
    for i, rec in enumerate(data2['records'][:3], 1):
        print(f"  {i}. Status={rec['status']:12s} Notes={rec['notes'][:40] if rec['notes'] else 'N/A'}")
