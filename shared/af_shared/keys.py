"""DynamoDB single-table key builders (table af-{env}-arquifusion). Source of truth: CLAUDE.md."""

SEV_RANK = {"CRITICAL": 1, "WARNING": 2, "WATCH": 3, "OK": 4}


def machine_pk(machine_id: str) -> str:
    return f"MACHINE#{machine_id}"


def machine_profile(machine_id: str) -> dict[str, str]:
    return {"PK": machine_pk(machine_id), "SK": "PROFILE"}


def machine_gsi1(operation_id: str, status: str, machine_id: str) -> dict[str, str]:
    return {"GSI1PK": f"OP#{operation_id}#MACHINE", "GSI1SK": f"{status}#{machine_id}"}


def reading_sk(timestamp: str) -> str:
    return f"READING#{timestamp}"


def incident_sk(created_at: str, incident_id: str) -> str:
    return f"INCIDENT#{created_at}#{incident_id}"


def incident_gsi1(operation_id: str, state: str, severity: str, created_at: str) -> dict[str, str]:
    return {"GSI1PK": f"OP#{operation_id}#INCIDENT#{state}", "GSI1SK": f"{SEV_RANK[severity]}#{created_at}"}


def incident_gsi2(operation_id: str, zone_id: str, created_at: str, machine_id: str) -> dict[str, str]:
    return {"GSI2PK": f"ZONE#{operation_id}#{zone_id}", "GSI2SK": f"{created_at}#{machine_id}"}


def maintenance_sk(date: str, maintenance_id: str) -> str:
    return f"MAINT#{date}#{maintenance_id}"


def kpi_item(machine_id: str, operation_id: str, date: str) -> dict[str, str]:
    return {
        "PK": machine_pk(machine_id),
        "SK": f"KPI#{date}",
        "GSI1PK": f"OP#{operation_id}#KPI#{date}",
        "GSI1SK": machine_id,
    }


def technician_keys(tech_id: str, operation_id: str, availability: str) -> dict[str, str]:
    return {
        "PK": f"TECH#{tech_id}",
        "SK": "PROFILE",
        "GSI1PK": f"OP#{operation_id}#TECH",
        "GSI1SK": f"{availability}#{tech_id}",
    }


def assignment_keys(tech_id: str, ts: str, incident_id: str) -> dict[str, str]:
    return {"PK": f"TECH#{tech_id}", "SK": f"ASSIGN#{ts}#{incident_id}"}


def stock_keys(part_id: str, warehouse_id: str) -> dict[str, str]:
    return {
        "PK": f"PART#{part_id}",
        "SK": f"STOCK#{warehouse_id}",
        "GSI1PK": f"WH#{warehouse_id}",
        "GSI1SK": f"PART#{part_id}",
    }


def forecast_keys(part_id: str, warehouse_id: str, iso_week: str) -> dict[str, str]:
    return {"PK": f"PART#{part_id}", "SK": f"FORECAST#{warehouse_id}#{iso_week}"}


def model_baseline_keys(model: str) -> dict[str, str]:
    return {"PK": f"MODEL#{model}", "SK": "BASELINE"}
