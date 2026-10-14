"""
Standalone script to re-initialise Qdrant collections.
Run inside the mcp-server pod:
  kubectl exec -it deployment/mcp-server -n org-mind -- python /app/init_collections.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from main import _init_qdrant

if __name__ == "__main__":
    print("Re-initialising Qdrant collections...")
    asyncio.run(_init_qdrant())
    print("Done.")
