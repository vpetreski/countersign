"""What a source adapter produces: canonical records plus everything it refused or repaired."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from countersign.domain.resources import CostLine, CpuPoint, DataWarning, QuarantinedRecord, Resource


@dataclass
class SourceBatch:
    source: str
    resources: list[Resource] = field(default_factory=list)
    cpu: dict[str, list[CpuPoint]] = field(default_factory=dict)  # resource id -> ascending daily points
    costs: list[CostLine] = field(default_factory=list)
    quarantined: list[QuarantinedRecord] = field(default_factory=list)
    warnings: list[DataWarning] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256
    records_read: int = 0

    def read(self, root: Path, path: Path) -> bytes:
        data = path.read_bytes()
        self.files[str(path.relative_to(root))] = hashlib.sha256(data).hexdigest()
        return data

    def quarantine(self, source_ref: str, reason: str, raw: object) -> None:
        self.quarantined.append(
            QuarantinedRecord(source=self.source, source_ref=source_ref, reason=reason, raw=str(raw)[:2000])
        )

    def warn(self, source_ref: str, rule: str, detail: str) -> None:
        self.warnings.append(DataWarning(source=self.source, source_ref=source_ref, rule=rule, detail=detail))

    @property
    def checksum(self) -> str:
        digest = hashlib.sha256()
        for name in sorted(self.files):
            digest.update(f"{name}:{self.files[name]}\n".encode())
        return digest.hexdigest()
