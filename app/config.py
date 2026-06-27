"""Rote — shared config. One place for model ids, flags, endpoints. Read secrets from env."""
import os
from dotenv import load_dotenv

load_dotenv()  # load keys from a local .env (gitignored) if present, before anything reads os.environ

# --- Gemini Computer Use (the prize model) ---
CU_MODEL = "gemini-3.5-flash"                                  # built-in computer_use; Interactions API (GA)
LEGACY_CU_MODEL = "gemini-2.5-computer-use-preview-10-2025"    # one-line fallback (generateContent loop)
USE_LEGACY_CU = os.getenv("ROTE_USE_LEGACY_CU", "0") == "1"
# requires: google-genai >= 2.7.0 ; GEMINI_API_KEY in env (genai.Client() reads it)

# --- browser / executor ---
VIEWPORT = (1280, 720)        # LOCK this; coords return normalized 0-999 -> denormalize against it
MAX_TURNS = 18                # circuit breaker: hard cap on steps per task
STUCK_AFTER = 3               # abort if screenshot+url hash is unchanged this many turns

# --- controlled arena (AcmeBilling) ---
APP_URL = os.getenv("ROTE_APP_URL", "http://localhost:8800")

# --- MongoDB Atlas (skill registry) ---
MONGO_URI = os.getenv("ROTE_MONGO_URI", "")
DB_NAME = "rote"
SKILLS_COLLECTION = "skills"
TRACES_COLLECTION = "traces"

# --- MiniMax (the "other agent" in the MCP cross-agent beat, P1) ---
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")


def denorm(x_norm: int, y_norm: int, w: int = VIEWPORT[0], h: int = VIEWPORT[1]) -> tuple[int, int]:
    """Normalized 0-999 -> actual pixels."""
    return round(x_norm / 1000 * w), round(y_norm / 1000 * h)
