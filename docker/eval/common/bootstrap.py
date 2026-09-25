"""Makes `import src.*` work from any eval script regardless of CWD, and loads the
real repo-root `.env`/`.env.local` the same way `docker/tests/conftest.py` does.

Every eval entry-point script must `import common.bootstrap` (after adding
`docker/eval/` to `sys.path`) before importing anything from `src.*`.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

EVAL_ROOT = Path(__file__).resolve().parent.parent  # docker/eval
DOCKER_ROOT = EVAL_ROOT.parent  # docker/
REPO_ROOT = DOCKER_ROOT.parent  # repo root

if str(DOCKER_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCKER_ROOT))

load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(REPO_ROOT / ".env")
