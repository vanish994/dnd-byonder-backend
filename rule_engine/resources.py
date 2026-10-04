from __future__ import annotations

from typing import Any

RECOVERY_POLICIES = frozenset({"short_rest", "long_rest", "turn", "never"})


def _resources(owner: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resources = owner.setdefault("resources", {})
    if not isinstance(resources, dict):
        raise ValueError("resources must be an object")
    return resources


def _validate_resource(resource_id: str, resource: dict[str, Any]) -> None:
    if not isinstance(resource_id, str) or not resource_id:
        raise ValueError("resource id must be a non-empty string")
    if not isinstance(resource, dict):
        raise ValueError("resource must be an object")
    maximum = resource.get("maximum")
    current = resource.get("current")
    recovery = resource.get("recovery", "never")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        raise ValueError("resource maximum must be a non-negative integer")
    if not isinstance(current, int) or isinstance(current, bool) or not 0 <= current <= maximum:
        raise ValueError("resource current must be between zero and maximum")
    if recovery not in RECOVERY_POLICIES:
        raise ValueError(f"unknown resource recovery policy: {recovery}")


def validate_resources(owner: dict[str, Any]) -> None:
    for resource_id, resource in _resources(owner).items():
        _validate_resource(resource_id, resource)


def define_resource(
    owner: dict[str, Any],
    resource_id: str,
    *,
    maximum: int,
    current: int | None = None,
    recovery: str = "never",
) -> dict[str, Any]:
    resources = _resources(owner)
    if resource_id in resources:
        raise ValueError("resource already exists")
    resource = {
        "id": resource_id,
        "current": maximum if current is None else current,
        "maximum": maximum,
        "recovery": recovery,
    }
    _validate_resource(resource_id, resource)
    resources[resource_id] = resource
    return resource


def consume_resource(owner: dict[str, Any], resource_id: str, amount: int = 1) -> dict[str, Any]:
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValueError("resource amount must be a positive integer")
    resources = _resources(owner)
    resource = resources.get(resource_id)
    if resource is None:
        raise ValueError("resource does not exist")
    _validate_resource(resource_id, resource)
    if resource["current"] < amount:
        raise ValueError("resource has insufficient uses")
    resource["current"] -= amount
    return resource


def recover_resource(owner: dict[str, Any], resource_id: str, amount: int | None = None) -> dict[str, Any]:
    resources = _resources(owner)
    resource = resources.get(resource_id)
    if resource is None:
        raise ValueError("resource does not exist")
    _validate_resource(resource_id, resource)
    requested = resource["maximum"] if amount is None else amount
    if not isinstance(requested, int) or isinstance(requested, bool) or requested < 0:
        raise ValueError("recovery amount must be a non-negative integer")
    resource["current"] = min(resource["maximum"], resource["current"] + requested)
    return resource


def recover_for_rest(owner: dict[str, Any], rest_type: str) -> list[str]:
    if rest_type not in {"short_rest", "long_rest"}:
        raise ValueError("unknown rest type")
    resources = owner.get("resources", {})
    if not isinstance(resources, dict):
        raise ValueError("resources must be an object")
    recovered: list[str] = []
    for resource_id, resource in resources.items():
        _validate_resource(resource_id, resource)
        if resource["recovery"] == rest_type or (
            rest_type == "long_rest" and resource["recovery"] == "short_rest"
        ):
            if resource["current"] != resource["maximum"]:
                resource["current"] = resource["maximum"]
                recovered.append(resource_id)
    return recovered


def recover_for_turn(owner: dict[str, Any]) -> list[str]:
    resources = owner.get("resources", {})
    if not isinstance(resources, dict):
        raise ValueError("resources must be an object")
    recovered: list[str] = []
    for resource_id, resource in resources.items():
        _validate_resource(resource_id, resource)
        if resource["recovery"] == "turn" and resource["current"] != resource["maximum"]:
            resource["current"] = resource["maximum"]
            recovered.append(resource_id)
    return recovered
