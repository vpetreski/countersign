"""The evaluation's fingerprint: a hash of everything that defines what was measured.

Components: the dataset, the system prompt, the tool schemas the model sees, the change policy, the grader and
runner code, the reference policy and its mutants, and the fixture data. A report whose fingerprint differs from
the code's is stale; CI and the verifier both refuse it.
"""

from __future__ import annotations

import hashlib
import json

from countersign.agent.prompts import PROMPT_VERSION, prompt_hash
from countersign.agent.runtime import REGISTRY
from countersign.evals import dataset
from countersign.settings import BACKEND_ROOT

PACKAGE = BACKEND_ROOT / "countersign"
GRADER_SOURCES = ("dataset.py", "graders.py", "mutants.py", "record.py", "runner.py", "stats.py")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def components() -> dict[str, str]:
    tools = json.dumps([d.model_dump() for d in REGISTRY.definitions()], sort_keys=True).encode()
    graders = b"".join((PACKAGE / "evals" / name).read_bytes() for name in GRADER_SOURCES)
    return {
        "dataset": dataset.digest(),
        "prompt": _sha(f"{PROMPT_VERSION}:{prompt_hash()}".encode()),
        "tools": _sha(tools),
        "policy": _sha((PACKAGE / "policy" / "policy.yaml").read_bytes()),
        "graders": _sha(graders),
        "reference_policy": _sha((PACKAGE / "models" / "offline.py").read_bytes()),
        "fixtures": _sha((BACKEND_ROOT / "fixtures" / "manifest.json").read_bytes()),
    }


def fingerprint() -> dict[str, str]:
    parts = components()
    combined = _sha("\n".join(f"{k}={v}" for k, v in sorted(parts.items())).encode())
    return {**parts, "combined": combined}
