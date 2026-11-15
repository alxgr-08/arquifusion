"""Prepare and validate the agreed ENSAYO-0001 kit offline. Never seed or contact AWS."""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import ensayo_kit, seed_ensayo, seed_grants  # noqa: E402

OPERATION = "ENSAYO-0001"
MACHINE = "ENS-0001"
TECHNICIAN = "T-E0001"
WAREHOUSE = "WH-E0001"
DEFAULT_KIT = ROOT / "build/cierre-demo-20261115/ensayo-0001"


class PreflightError(ValueError):
    """A public, sanitized explanation that contains no participant identifiers."""


def require(ok, message):
    if not ok:
        raise PreflightError(message)


def validate_rows(rows):
    expected = {
        (f"OP#{OPERATION}", "META"),
        (f"OP#{OPERATION}", "ZONE#ZE"),
        (f"OP#{OPERATION}", f"WH#{WAREHOUSE}"),
        (f"OP#{OPERATION}", "INCIDENT_SETTINGS"),
        (f"MACHINE#{MACHINE}", "PROFILE"),
        (f"TECH#{TECHNICIAN}", "PROFILE"),
        ("GRANT#accounts.google.com#ensayo-operador-0001", f"OP#{OPERATION}"),
    }
    keyed = {(item["PK"], item["SK"]): item for item in rows}
    require(len(rows) == len(keyed) == 7 and set(keyed) == expected, "Expected exactly the seven demo seed rows")
    metadata = keyed[(f"OP#{OPERATION}", "META")]
    require(
        metadata["operationId"] == OPERATION and metadata["zones"] == ["ZE"] and metadata["warehouses"] == [WAREHOUSE],
        "Operation metadata does not match the isolated demo",
    )
    zone = keyed[(f"OP#{OPERATION}", "ZONE#ZE")]
    warehouse = keyed[(f"OP#{OPERATION}", f"WH#{WAREHOUSE}")]
    require(
        zone["zoneId"] == "ZE" and warehouse["warehouseId"] == WAREHOUSE and warehouse["zones"] == ["ZE"],
        "Unexpected zone or warehouse",
    )
    settings = keyed[(f"OP#{OPERATION}", "INCIDENT_SETTINGS")]
    require(
        settings["zoneWarehouses"] == {"ZE": [WAREHOUSE]} and settings["settings"]["partsFromOtherWarehouses"] is False,
        "The demo must not reserve stock from shared warehouses",
    )
    for kind, identifier in (("MACHINE", MACHINE), ("TECH", TECHNICIAN)):
        item = keyed[(f"{kind}#{identifier}", "PROFILE")]
        require(
            item["operationId"] == OPERATION and item["GSI1PK"] == f"OP#{OPERATION}#{kind}",
            "Profile ownership does not match the demo operation",
        )
    machine = keyed[(f"MACHINE#{MACHINE}", "PROFILE")]
    tech = keyed[(f"TECH#{TECHNICIAN}", "PROFILE")]
    require(machine["machineId"] == MACHINE and machine["zoneId"] == "ZE", "Unexpected demo machine")
    require(tech["techId"] == TECHNICIAN and tech["availability"] == "AVAILABLE", "Unexpected demo technician")
    require(
        all(item.get("run") == "0001" for item in rows if not item["PK"].startswith("GRANT#")),
        "Unexpected run ownership",
    )
    synthetic = keyed[("GRANT#accounts.google.com#ensayo-operador-0001", f"OP#{OPERATION}")]
    require(
        synthetic["operationId"] == OPERATION and synthetic["role"] == "MAINT_LEAD", "Unexpected local operator grant"
    )
    return [
        {"PK": "GRANT#accounts.google.com#<synthetic-operator>" if pk.startswith("GRANT#") else pk, "SK": sk}
        for pk, sk in sorted(keyed)
    ]


def validate_real_grants(rows):
    require(isinstance(rows, list) and bool(rows), "Real participant grants must be a non-empty list")
    roles = set()
    identities = set()
    for row in rows:
        require(isinstance(row, dict), "Invalid participant grant")
        require(
            isinstance(row.get("sub"), str) and re.fullmatch(r"[0-9]{1,255}", row["sub"]) is not None,
            "Complete every participant with their real Google subject; templates are not usable grants",
        )
        require(row.get("operationId") == OPERATION, "Participant grant belongs to another operation")
        require(
            row.get("iss", "accounts.google.com") in {"accounts.google.com", "https://accounts.google.com"},
            "Unexpected identity provider",
        )
        require(row["sub"] not in identities, "Duplicate participant grant would replace another role")
        identities.add(row["sub"])
        role = row.get("role")
        require(role in {"MAINT_LEAD", "TECHNICIAN", "SUPERVISOR"}, "Unexpected role for the agreed demo")
        require(
            role != "TECHNICIAN" or row.get("techId") == TECHNICIAN, "Participant technician does not match the demo"
        )
        roles.add(role)
    require({"MAINT_LEAD", "TECHNICIAN"} <= roles, "The agreed demo needs its maintenance lead and assigned technician")
    try:
        seed_grants.build_items(rows, {})
    except (ValueError, KeyError, TypeError):
        raise PreflightError("Participant grants failed the owner's existing validator") from None
    return {"participants": len(rows), "roles": sorted(roles)}


def inspect(kit, *, require_real_grants=False, grants_file=None):
    rows = seed_ensayo.load_items(kit)
    public_keys = validate_rows(rows)
    participant_status = "NOT_CHECKED"
    participant_summary = None
    if require_real_grants:
        path = grants_file or kit / "grants-participantes.json"
        participant_summary = validate_real_grants(json.loads(path.read_text(encoding="utf-8")))
        participant_status = "VALIDATED_OFFLINE_NOT_WRITTEN"
    hashes = {name: hashlib.sha256((kit / name).read_bytes()).hexdigest().upper() for name in seed_ensayo.ROWS}
    return {
        "status": "VALIDATED_OFFLINE_NOT_SEEDED",
        "operation": OPERATION,
        "machine": MACHINE,
        "technician": TECHNICIAN,
        "warehouse": WAREHOUSE,
        "rows": public_keys,
        "seedFileSha256": hashes,
        "syntheticOperatorGrant": "LOCAL_FIXTURE_ONLY; not a real Google participant",
        "realParticipantGrants": participant_status,
        "participantSummary": participant_summary,
        "participantIdentityVerified": False,
        "remoteCollisionsChecked": False,
        "cloudCalls": [],
        "ssmWrites": [],
        "requiredNextStep": "Approved account/collision check, then separately authorized conditional seed",
    }


def prepare(kit, *, now: datetime | None = None):
    require(kit.resolve().is_relative_to((ROOT / "build").resolve()), "Keep generated demo data under build/")
    require(not kit.exists(), "Kit already exists; inspect it without changing event timestamps")
    files = ensayo_kit.build(1, now=now)
    kit.mkdir(parents=True, exist_ok=False)
    for name, value in files.items():
        (kit / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result = inspect(kit)
    (kit / "offline-preflight.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "check"])
    parser.add_argument("--kit", type=Path, default=DEFAULT_KIT)
    parser.add_argument(
        "--require-real-grants",
        action="store_true",
        help="Reject incomplete participant input; Google login must later verify identity ownership",
    )
    parser.add_argument("--grants-file", type=Path)
    args = parser.parse_args()
    if args.action == "prepare" and (args.require_real_grants or args.grants_file):
        parser.error("Prepare only generates templates; use check for an explicitly supplied participant file")
    if args.grants_file and not args.require_real_grants:
        parser.error("--grants-file requires --require-real-grants")
    try:
        result = (
            prepare(args.kit)
            if args.action == "prepare"
            else inspect(args.kit, require_real_grants=args.require_real_grants, grants_file=args.grants_file)
        )
    except PreflightError as error:
        parser.exit(1, str(error) + "\n")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Offline preflight failed ({type(error).__name__}); no cloud actions performed\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
