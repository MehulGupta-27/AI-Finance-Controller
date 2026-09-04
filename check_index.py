#!/usr/bin/env python
"""Check what's indexed in ChromaDB."""
from agents.qa_agent import _get_collection

collection = _get_collection()
print(f'Collection count: {collection.count()}')

# Get sample records
results = collection.get(limit=30, include=['documents', 'metadatas'])
print(f'\nLooking for gym-related records:')
for i, doc in enumerate(results['documents']):
    meta = results['metadatas'][i]
    notes = meta.get('notes', '')
    if 'gym' in doc.lower() or 'gym' in notes.lower() or 'membership' in notes.lower():
        print(f'\n{i+1}. Document text: {doc}')
        print(f'   Notes: {notes}')
        print(f'   Status: {meta["status"]}')
