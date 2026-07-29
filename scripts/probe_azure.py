"""Quick probe to verify Azure OpenAI credentials and deployments work.

Usage:
    python scripts/probe_azure.py
"""

from __future__ import annotations

import asyncio
import sys
import os

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.config import get_settings
from app.llm.azure import chat_completion, get_embedding


async def main() -> None:
    settings = get_settings()
    print(f"Endpoint        : {settings.azure_openai_endpoint}")
    print(f"Chat API version: {settings.azure_openai_api_version}")
    print(f"Chat deployment : {settings.azure_chat_deployment}")
    print(f"Embed API version: {settings.azure_embedding_api_version}")
    print(f"Embed deployment: {settings.azure_embedding_deployment}")
    print(f"Embed dimensions: {settings.embedding_dimensions}")
    print()

    # Test chat
    print("--- Chat completion ---")
    resp = await chat_completion(
        messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
        settings=settings,
    )
    print(f"Response: {resp.choices[0].message.content}")
    print()

    # Test embedding
    print("--- Embedding ---")
    vec = await get_embedding("test embedding", settings)
    print(f"Dimension: {len(vec)}")
    print(f"First 5:   {vec[:5]}")
    print()

    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
