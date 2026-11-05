"""The evaluation's fingerprint: a hash of everything that defines what was measured.

Components: the dataset, the system prompt, the tool schemas the model sees, the change policy, the grader and
runner code, the reference policy and its mutants, the fixture data, and the code every trial runs through: the
runtime, the tools, the analyzer, the policy engine, the sandbox and the executor. A report whose fingerprint
differs from the code's is stale; CI and the published-report test both refuse it.
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
# The code paths of a trial, by component (paths relative to the package).
RUN_SOURCES: dict[str, tuple[str, ...]] = {
    "runtime": ("agent/runtime.py", "agent/state.py", "agent/resilience.py", "agent/faults.py"),
    "tool_code": ("tools/catalog.py", "tools/registry.py"),
    "analyzer": ("domain/analysis.py", "domain/rules.py"),
    "policy_engine": ("policy/engine.py",),
    "sandbox": ("execution/sandbox.py",),
    "executor": ("execution/executor.py", "execution/tokens.py"),
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sources(paths: tuple[str, ...]) -> str:
    return _sha(b"".join(path.encode() + b"\0" + (PACKAGE / path).read_bytes() for path in paths))


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
        **{name: _sources(paths) for name, paths in RUN_SOURCES.items()},
    }


def fingerprint() -> dict[str, str]:
    parts = components()
    combined = _sha("\n".join(f"{k}={v}" for k, v in sorted(parts.items())).encode())
    return {**parts, "combined": combined}
