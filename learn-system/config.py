from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "learn.db"
SECRETS_DIR = Path.home() / "CCProject" / ".secrets"
