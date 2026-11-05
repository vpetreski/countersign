"""AWS adapter: boto3-shaped DescribeInstances / DescribeVolumes / DescribeAddresses and CloudWatch
GetMetricData responses, one directory per account."""

from __future__ import annotations

import datetime as dt
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

SOURCE = "aws"
_INSTANCE_STATES = {"running": State.RUNNING, "stopped": State.STOPPED}


def load(root: Path) -> SourceBatch:
    batch = SourceBatch(SOURCE)
    base = root / "sources" / "aws"
    for account_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        account = account_dir.name
        _instances(batch, root, account_dir, account)
        _volumes(batch, root, account_dir, account)
        _addresses(batch, root, account_dir, account)
        _metrics(batch, root, account_dir, account)
    return batch


def _pages(batch: SourceBatch, root: Path, account_dir: Path, stem: str) -> list[tuple[str, dict[str, Any]]]:
    """Read page1, page2, ... following NextToken the way a paginator would."""
    pages = []
    n = 1
    while True:
        path = account_dir / f"{stem}.page{n}.json"
        if not path.exists():
            break
        body = json.loads(batch.read(root, path))
        pages.append((str(path.relative_to(root)), body))
        if not body.get("NextToken"):
            break
        n += 1
    return pages


def _tags(items: list[dict[str, str]] | None) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in items or []}


def _arn(account: str, region: str, kind: str, ref: str) -> str:
    return f"arn:aws:ec2:{region}:{account}:{kind}/{ref}"


def _instances(batch: SourceBatch, root: Path, account_dir: Path, account: str) -> None:
    for rel, body in _pages(batch, root, account_dir, "ec2_describe_instances"):
        for r_index, reservation in enumerate(body.get("Reservations", [])):
            for i_index, inst in enumerate(reservation.get("Instances", [])):
                batch.records_read += 1
                ref = f"{rel}#/Reservations/{r_index}/Instances/{i_index}"
                try:
                    instance_id = inst["InstanceId"]
                    sku = inst["InstanceType"]
                    created, _ = parse_timestamp(inst["LaunchTime"])
                    native = inst["State"]["Name"]
                    state = _INSTANCE_STATES[native]
                except (KeyError, ValueError, TypeError) as exc:
                    batch.quarantine(ref, f"invalid instance record: {exc!r}", inst)
                    continue
                raw_tags = _tags(inst.get("Tags"))
                tags, flags = normalize_tags(raw_tags)
                region = inst.get("Placement", {}).get("AvailabilityZone", "us-east-1a")[:-1]
                batch.resources.append(
                    Resource(
                        id=_arn(account, region, "instance", instance_id),
                        ref=instance_id,
                        provider=Provider.AWS,
                        kind=Kind.VM,
                        name=tags.get("name", instance_id),
                        account=account,
                        region=region,
                        environment=environment(tags),
                        team=first_tag(tags, TEAM_KEYS),
                        owner=first_tag(tags, OWNER_KEYS),
                        tags=tags,
                        state=state,
                        native_state=native,
                        sku=sku,
                        created_at=created,
                        flags=flags,
                        source=SOURCE,
                        source_ref=ref,
                    )
                )


def _detach_times(batch: SourceBatch, root: Path, account_dir: Path) -> dict[str, dt.datetime]:
    """Latest DetachVolume time per volume from CloudTrail LookupEvents (the last 90 days)."""
    path = account_dir / "cloudtrail_lookup_events.detach_volume.json"
    if not path.exists():
        batch.warn(
            str(path.relative_to(root)), "completeness", "no CloudTrail detach events: detach times unknown"
        )
        return {}
    out: dict[str, dt.datetime] = {}
    body = json.loads(batch.read(root, path))
    for index, event in enumerate(body.get("Events", [])):
        ref = f"{path.relative_to(root)}#/Events/{index}"
        try:
            when, _ = parse_timestamp(event["EventTime"])
            volumes = [
                r["ResourceName"] for r in event["Resources"] if r["ResourceType"] == "AWS::EC2::Volume"
            ]
        except (KeyError, ValueError, TypeError) as exc:
            batch.quarantine(ref, f"invalid CloudTrail event: {exc!r}", event)
            continue
        if event.get("EventName") != "DetachVolume":
            continue
        for volume in volumes:
            if volume not in out or when > out[volume]:
                out[volume] = when
    return out


def _volumes(batch: SourceBatch, root: Path, account_dir: Path, account: str) -> None:
    seen: set[str] = set()
    detached = _detach_times(batch, root, account_dir)
    for rel, body in _pages(batch, root, account_dir, "ec2_describe_volumes"):
        for index, vol in enumerate(body.get("Volumes", [])):
            batch.records_read += 1
            ref = f"{rel}#/Volumes/{index}"
            try:
                volume_id = vol["VolumeId"]
                size = int(vol["Size"])
                created, _ = parse_timestamp(vol["CreateTime"])
                native = vol["State"]
            except (KeyError, ValueError, TypeError) as exc:
                batch.quarantine(ref, f"invalid volume record: {exc!r}", vol)
                continue
            if native not in ("in-use", "available"):
                batch.quarantine(ref, f"invalid volume record: unsupported volume state {native!r}", vol)
                continue
            if volume_id in seen:
                batch.warn(ref, "uniqueness", f"{volume_id} returned on more than one page; kept the first")
                continue
            seen.add(volume_id)
            tags, flags = normalize_tags(_tags(vol.get("Tags")))
            attachments = vol.get("Attachments") or []
            region = vol.get("AvailabilityZone", "us-east-1a")[:-1]
            batch.resources.append(
                Resource(
                    id=_arn(account, region, "volume", volume_id),
                    ref=volume_id,
                    provider=Provider.AWS,
                    kind=Kind.DISK,
                    name=tags.get("name", volume_id),
                    account=account,
                    region=region,
                    environment=environment(tags),
                    team=first_tag(tags, TEAM_KEYS),
                    owner=first_tag(tags, OWNER_KEYS),
                    tags=tags,
                    state=State.ATTACHED if native == "in-use" else State.UNATTACHED,
                    native_state=native,
                    sku=vol.get("VolumeType", "standard"),
                    size_gb=size,
                    created_at=created,
                    detached_at=detached.get(volume_id) if native == "available" else None,
                    attached_to=attachments[0]["InstanceId"] if attachments else None,
                    flags=flags,
                    source=SOURCE,
                    source_ref=ref,
                )
            )


def _addresses(batch: SourceBatch, root: Path, account_dir: Path, account: str) -> None:
    path = account_dir / "ec2_describe_addresses.json"
    rel = str(path.relative_to(root))
    for index, addr in enumerate(json.loads(batch.read(root, path)).get("Addresses", [])):
        batch.records_read += 1
        ref = f"{rel}#/Addresses/{index}"
        if "AllocationId" not in addr or "PublicIp" not in addr:
            batch.quarantine(ref, "address without AllocationId or PublicIp", addr)
            continue
        tags, flags = normalize_tags(_tags(addr.get("Tags")))
        associated = bool(addr.get("AssociationId"))
        region = addr.get("NetworkBorderGroup", "us-east-1")
        batch.resources.append(
            Resource(
                id=_arn(account, region, "elastic-ip", addr["AllocationId"]),
                ref=addr["AllocationId"],
                provider=Provider.AWS,
                kind=Kind.PUBLIC_IP,
                name=tags.get("name", addr["PublicIp"]),
                account=account,
                region=region,
                environment=environment(tags),
                team=first_tag(tags, TEAM_KEYS),
                owner=first_tag(tags, OWNER_KEYS),
                tags=tags,
                state=State.ASSOCIATED if associated else State.UNASSOCIATED,
                native_state="associated" if associated else "unassociated",
                sku="PublicIPv4",
                created_at=None,  # DescribeAddresses has no creation time
                attached_to=addr.get("InstanceId"),
                flags=flags,
                source=SOURCE,
                source_ref=ref,
            )
        )


def _metrics(batch: SourceBatch, root: Path, account_dir: Path, account: str) -> None:
    request = json.loads(batch.read(root, account_dir / "cloudwatch_get_metric_data.request.json"))
    response = json.loads(batch.read(root, account_dir / "cloudwatch_get_metric_data.response.json"))
    query_target: dict[str, tuple[str, str]] = {}
    for query in request["MetricDataQueries"]:
        dims = {d["Name"]: d["Value"] for d in query["MetricStat"]["Metric"]["Dimensions"]}
        query_target[query["Id"]] = (dims["InstanceId"], query["MetricStat"]["Stat"])
    series: dict[str, dict[str, dict[str, float]]] = {}
    for result in response["MetricDataResults"]:
        if result.get("StatusCode") != "Complete":
            batch.warn(f"cloudwatch#{result['Id']}", "completeness", f"status {result.get('StatusCode')}")
        instance_id, stat = query_target[result["Id"]]
        bucket = series.setdefault(instance_id, {})
        for ts, value in zip(result["Timestamps"], result["Values"], strict=True):
            day = parse_timestamp(ts)[0].date().isoformat()
            bucket.setdefault(day, {})[stat] = float(value)
    region_of = {r.ref: r for r in batch.resources if r.kind is Kind.VM and r.account == account}
    for instance_id, days in series.items():
        resource = region_of.get(instance_id)
        if resource is None:
            batch.warn(
                f"cloudwatch:{instance_id}", "reconciliation", "metrics for an instance not in inventory"
            )
            continue
        points = [
            CpuPoint(day=dt.date.fromisoformat(day), avg=v["Average"], max=v.get("Maximum", v["Average"]))
            for day, v in sorted(days.items())  # CloudWatch returns TimestampDescending; store ascending
            if "Average" in v
        ]
        batch.cpu[resource.id] = points
