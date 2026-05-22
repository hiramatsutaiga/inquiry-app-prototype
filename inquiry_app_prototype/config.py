# config.py
import os

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "models/gemini-2.5-flash")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Vision (サービスアカウントJSON)
SERVICE_ACCOUNT_KEY_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account_key.json")

# その他
PROFILE_FILE = os.getenv("PROFILE_FILE", "profile.json")
