from af_shared import keys


def test_machine_keys_match_design() -> None:
    assert keys.machine_profile("EXC-0042") == {"PK": "MACHINE#EXC-0042", "SK": "PROFILE"}
    assert keys.machine_gsi1("MINA-NORTE", "OPERATING", "EXC-0042") == {
        "GSI1PK": "OP#MINA-NORTE#MACHINE",
        "GSI1SK": "OPERATING#EXC-0042",
    }


def test_open_incidents_sort_critical_first() -> None:
    critical = keys.incident_gsi1("MINA-NORTE", "OPEN", "CRITICAL", "2026-11-13T15:00:00Z")["GSI1SK"]
    warning = keys.incident_gsi1("MINA-NORTE", "OPEN", "WARNING", "2026-11-13T14:00:00Z")["GSI1SK"]
    assert critical < warning


def test_zone_index_keys() -> None:
    assert keys.incident_gsi2("MINA-NORTE", "Z3", "2026-11-13T14:06:10Z", "EXC-0042") == {
        "GSI2PK": "ZONE#MINA-NORTE#Z3",
        "GSI2SK": "2026-11-13T14:06:10Z#EXC-0042",
    }
