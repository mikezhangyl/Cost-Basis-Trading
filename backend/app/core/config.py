from pathlib import Path

from dotenv import load_dotenv


def load_environment() -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    repo_dir = backend_dir.parent
    for candidate in (repo_dir / ".env.local", backend_dir / ".env.local", repo_dir / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
