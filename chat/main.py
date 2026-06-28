#!/usr/bin/env python3
"""Entry point for the Rote chatbot.

  python chat/main.py
  python -m chat          (from repo root)
  ROTE_SCORE_THRESHOLD=0.9 python chat/main.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from chat.agent import chat_loop

if __name__ == "__main__":
    chat_loop()
