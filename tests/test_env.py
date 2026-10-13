"""The course rule: .env is read from the repo folder first, then from the folder above it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE = "from backend import config, llm; import os; print(os.getenv('PORTKEY_API_KEY'), config.DATA_MODE)"


def _copy_repo(dst: Path) -> Path:
    repo = dst / "repo"
    shutil.copytree(REPO / "backend", repo / "backend", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / "data").mkdir()
    return repo


def _probe(repo: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("PORTKEY_API_KEY", "OPENAI_API_KEY", "DATA_MODE", "BTWS_DB_PATH")}
    return subprocess.run([sys.executable, "-c", PROBE], cwd=repo, env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


def test_env_in_parent_folder():
    with tempfile.TemporaryDirectory() as d:
        repo = _copy_repo(Path(d))
        (Path(d) / ".env").write_text("PORTKEY_API_KEY=parent-key\nDATA_MODE=sample\n")
        assert _probe(repo) == "parent-key sample"


def test_repo_env_wins_over_parent():
    with tempfile.TemporaryDirectory() as d:
        repo = _copy_repo(Path(d))
        (Path(d) / ".env").write_text("PORTKEY_API_KEY=parent-key\n")
        (repo / ".env").write_text("PORTKEY_API_KEY=repo-key\nDATA_MODE=live\n")
        assert _probe(repo) == "repo-key live"
