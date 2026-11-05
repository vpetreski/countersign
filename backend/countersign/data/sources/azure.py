"""Azure adapter: Azure Resource Graph rows for VMs, managed disks and public IPs, and Azure Monitor
`Percentage CPU` responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from countersign.data.normalize import (
    OWNER_KEYS,
    TEAM_KEYS,
    environment,
    first_tag,
    normalize_tags,
    parse_timestamp,
)
from countersign.data.sources.batch import SourceBatch
from countersign.domain.resources import CpuPoint, Kind, Provider, Resource, State

SOURCE = "azure"
_POWER = {
    "PowerState/running": State.RUNNING,
    "PowerState/deallocated": State.STOPPED,
    "PowerState/stopped": State.STOPPED,
}
_DISK = {"Unattached": State.UNATTACHED, "Attached": State.ATTACHED, "Reserved": State.RESERVED}


def load(root: Path) -> SourceBatch:
    batch = SourceBatch(SOURCE)
    base = root / "sources" / "azure"
    _vms(batch, root, base / "resource_graph.virtual_machines.json")
    _disks(batch, root, base / "resource_graph.disks.json")
    _public_ips(batch, root, base / "resource_graph.public_ip_addresses.json")
    _metrics(batch, root, base / "monitor.percentage_cpu.json")
    return batch


def _rows(batch: SourceBatch, root: Path, path: Path) -> list[tuple[str, dict[str, Any]]]:
    body = json.loads(batch.read(root, path))
    rel = str(path.relative_to(root))
    rows = body.get("data", [])
    if str(body.get("resultTruncated", "false")).lower() == "true":
        batch.warn(rel, "completeness", "Resource Graph result was truncated")
    if int(body.get("totalRecords", len(rows))) != len(rows):
        batch.warn(rel, "completeness", f"totalRecords {body.get('totalRecords')} != {len(rows)} rows")
    return [(f"{rel}#/data/{i}", row) for i, row in enumerate(rows)]


def _common(row: dict[str, Any]) -> tuple[dict[str, str], tuple[str, ...]]:
    return normalize_tags(row.get("tags") or {})


def _vms(batch: SourceBatch, root: Path, path: Path) -> None:
    for ref, row in _rows(batch, root, path):
        batch.records_read += 1
        props = row.get("properties") or {}
        try:
            size = props["hardwareProfile"]["vmSize"]
            native = props["extended"]["instanceView"]["powerState"]["code"]
            state = _POWER[native]
            created, _ = parse_timestamp(props["timeCreated"])
            rid, name, sub, location = row["id"], row["name"], row["subscriptionId"], row["location"]
        except (KeyError, ValueError, TypeError) as exc:
            batch.quarantine(ref, f"invalid virtual machine row: {exc!r}", row)
            continue
        tags, flags = _common(row)
        batch.resources.append(
            Resource(
                id=rid,
                ref=name,
                provider=Provider.AZURE,
                kind=Kind.VM,
                name=name,
                account=sub,
                region=location,
                environment=environment(tags),
                team=first_tag(tags, TEAM_KEYS),
                owner=first_tag(tags, OWNER_KEYS),
                tags=tags,
                state=state,
                native_state=native.removeprefix("PowerState/"),
                sku=size,
                created_at=created,
                flags=flags,
                source=SOURCE,
                source_ref=ref,
            )
        )


def _disks(batch: SourceBatch, root: Path, path: Path) -> None:
    for ref, row in _rows(batch, root, path):
        batch.records_read += 1
        props = row.get("properties") or {}
        try:
            native = props["diskState"]
            state = _DISK[native]
            size = int(props["diskSizeGB"])
            tier = props["tier"]
            created, _ = parse_timestamp(props["timeCreated"])
        except (KeyError, ValueError, TypeError) as exc:
            batch.quarantine(ref, f"invalid disk row: {exc!r}", row)
            continue
        detached = (
            parse_timestamp(props["LastOwnershipUpdateTime"])[0]
            if props.get("LastOwnershipUpdateTime")
            else None
        )
        managed_by = row.get("managedBy") or None
        tags, flags = _common(row)
        batch.resources.append(
            Resource(
                id=row["id"],
                ref=row["name"],
                provider=Provider.AZURE,
                kind=Kind.DISK,
                name=row["name"],
                account=row["subscriptionId"],
                region=row["location"],
                environment=environment(tags),
                team=first_tag(tags, TEAM_KEYS),
                owner=first_tag(tags, OWNER_KEYS),
                tags=tags,
                state=state,
                native_state=native,
                sku=tier,
                size_gb=size,
                created_at=created,
                detached_at=detached if state is State.UNATTACHED else None,
                attached_to=managed_by.rsplit("/", 1)[-1] if managed_by else None,
                flags=flags,
                source=SOURCE,
                source_ref=ref,
            )
        )


def _public_ips(batch: SourceBatch, root: Path, path: Path) -> None:
    for ref, row in _rows(batch, root, path):
        batch.records_read += 1
        props = row.get("properties") or {}
        if "ipAddress" not in props:
            batch.quarantine(ref, "public IP row without ipAddress", row)
            continue
        associated = bool(props.get("ipConfiguration") or props.get("natGateway"))
        tags, flags = _common(row)
        attached = None
        if props.get("ipConfiguration"):
            nic = props["ipConfiguration"]["id"].split("/networkInterfaces/")[1].split("/")[0]
            attached = nic.removeprefix("nic-")
        batch.resources.append(
            Resource(
                id=row["id"],
                ref=row["name"],
                provider=Provider.AZURE,
                kind=Kind.PUBLIC_IP,
                name=row["name"],
                account=row["subscriptionId"],
                region=row["location"],
                environment=environment(tags),
                team=first_tag(tags, TEAM_KEYS),
                owner=first_tag(tags, OWNER_KEYS),
                tags=tags,
                state=State.ASSOCIATED if associated else State.UNASSOCIATED,
                native_state="associated" if associated else "unassociated",
                sku=(row.get("sku") or {}).get("name", "Standard"),
                created_at=None,  # Resource Graph rows for public IPs carry no creation time
                attached_to=attached,
                flags=flags,
                source=SOURCE,
                source_ref=ref,
            )
        )


def _metrics(batch: SourceBatch, root: Path, path: Path) -> None:
    body = json.loads(batch.read(root, path))
    by_id = {r.id.lower(): r for r in batch.resources if r.kind is Kind.VM}
    for response in body.get("responses", []):
        for metric in response.get("value", []):
            vm_id = metric["id"].split("/providers/Microsoft.Insights/")[0].lower()
            resource = by_id.get(vm_id)
            if resource is None:
                batch.warn(f"monitor:{vm_id}", "reconciliation", "metrics for a VM not in inventory")
                continue
            points = []
            for series in metric.get("timeseries", []):
                for point in series.get("data", []):
                    if "average" not in point:  # Azure Monitor omits values for days without data
                        continue
                    day = parse_timestamp(point["timeStamp"])[0].date()
                    points.append(
                        CpuPoint(
                            day=day,
                            avg=float(point["average"]),
                            max=float(point.get("maximum", point["average"])),
                        )
                    )
            batch.cpu[resource.id] = sorted(points, key=lambda p: p.day)
