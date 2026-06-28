"""Manual Gemini embedding smoke test.

Run explicitly with ``python database/test_embedding.py``. Keeping the API
call behind ``main`` prevents test discovery from making a network request.
"""
from pathlib import Path

from dotenv import load_dotenv
from google import genai


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    client = genai.Client()
    result = client.models.embed_content(
        model="gemini-embedding-2",
        contents="What is the meaning of life?",
    )
    print(result.embeddings)
    print(f"Dimensions: {len(result.embeddings[0].values)}")


if __name__ == "__main__":
    main()
