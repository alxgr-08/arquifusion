"""Role x severity notification matrix (P9). Owner: Rol 2.

One SNS topic per role (ARNs in SSM /af/notify/topics). Every message carries attributes severity, role,
channel, kind and techId so subscriptions can filter (e.g. operator SMS only for CRITICAL, each technician
only their own incidents). PANEL and DAILY are not pushed: the web panel and the daily summary show them.
"""

from __future__ import annotations

import unicodedata
from typing import Any

MATRIX: dict[str, dict[str, str | None]] = {
    "OPERATOR": {"WATCH": None, "WARNING": "PANEL", "CRITICAL": "SMS"},
    "TECHNICIAN": {"WATCH": None, "WARNING": "EMAIL", "CRITICAL": "SMS"},
    "MAINT_LEAD": {"WATCH": "DAILY", "WARNING": "IMMEDIATE", "CRITICAL": "IMMEDIATE"},
    "SUPERVISOR": {"WATCH": None, "WARNING": "PANEL", "CRITICAL": "IMMEDIATE"},
    "MANAGER": {"WATCH": None, "WARNING": "DAILY", "CRITICAL": None},  # only zone incidents or > 4 h stopped
}
PUSH = {"SMS", "EMAIL", "IMMEDIATE"}
KIND_ROLES: dict[str, dict[str, str]] = {
    "ZONE_INCIDENT": {"SUPERVISOR": "IMMEDIATE", "MANAGER": "IMMEDIATE"},
    "RESTOCK": {"MAINT_LEAD": "IMMEDIATE"},
    "NO_TECHNICIAN": {"MAINT_LEAD": "IMMEDIATE"},
    "TECH_TIMEOUT": {"MAINT_LEAD": "IMMEDIATE"},
    "LONG_DOWNTIME": {"MANAGER": "IMMEDIATE", "MAINT_LEAD": "IMMEDIATE"},
    "RESOLVED": {"MAINT_LEAD": "IMMEDIATE", "SUPERVISOR": "IMMEDIATE"},
    "DAILY_SUMMARY": {"MAINT_LEAD": "DAILY", "MANAGER": "DAILY"},
}


def ascii_text(text: str, limit: int | None = None) -> str:
    clean = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    clean = " ".join(clean.split()) if limit else clean
    return clean[:limit] if limit else clean


def channel_for(kind: str, role: str, severity: str, payload: dict[str, Any]) -> str | None:
    if kind in ("INCIDENT", "ESCALATED"):
        sev = "CRITICAL" if kind == "ESCALATED" else severity
        if role == "TECHNICIAN" and not (payload.get("assignment") or {}).get("assigned"):
            return None
        return MATRIX.get(role, {}).get(sev)
    if kind == "ZONE_INCIDENT" and not (payload.get("zoneIncident") or {}).get("created", True):
        return None
    return KIND_ROLES.get(kind, {}).get(role)


def compose(kind: str, role: str, payload: dict[str, Any]) -> tuple[str, str]:
    inc = payload.get("incident") or {}
    diag = payload.get("diagnosis") or {}  # ai-diagnosis response (contracts/ai-diagnosis-response.schema.json)
    detail = diag.get("diagnosis") or {}
    asg = payload.get("assignment") or {}
    parts = payload.get("parts") or {}
    machine, sev, zone = inc.get("machineId", "-"), inc.get("severity", "-"), inc.get("zoneId", "-")
    head = f"[{sev}] {machine} ({zone})"

    if kind in ("INCIDENT", "ESCALATED"):
        body = (diag.get("messages") or {}).get(role) or detail.get("probableCause") or "Anomalia detectada."
        facts = []
        if asg.get("assigned"):
            eta = f", ETA {round(asg['etaMinutes'])} min" if asg.get("etaMinutes") is not None else ""
            facts.append(f"Tecnico {asg['techId']}{eta}")
        if parts.get("reserved"):
            facts.append(
                "Repuestos: " + ", ".join(f"{p['partId']} x{p['qty']} ({p['warehouse']})" for p in parts["reserved"])
            )
        if role == "TECHNICIAN":
            facts.append(f"Incidente {inc.get('incidentId')}. Confirma llegada en la app.")
        prefix = "ESCALADO a CRITICAL. " if kind == "ESCALATED" else ""
        return f"{head} {detail.get('component', 'ANOMALIA')}", prefix + body + (
            "\n" + "\n".join(facts) if facts else ""
        )
    if kind == "ZONE_INCIDENT":
        z = payload.get("zoneIncident") or {}
        machines = ", ".join(z.get("machines", []))
        return (
            f"[ZONA {zone}] Incidente de zona",
            f"{len(z.get('machines', []))} maquinas con incidentes en {zone}: {machines}. "
            f"{z.get('hypothesis') or (diag.get('zoneAnalysis') or {}).get('commonCause') or ''}".strip(),
        )
    if kind == "RESTOCK":
        missing = ", ".join(f"{p['partId']} x{p['qty']}" for p in parts.get("missing", []))
        return f"{head} Reposicion urgente", f"Sin stock para {inc.get('incidentId')}: {missing}."
    if kind == "NO_TECHNICIAN":
        return (
            f"{head} Sin tecnico disponible",
            f"Ningun tecnico disponible para {inc.get('incidentId')}. Reintentando.",
        )
    if kind == "TECH_TIMEOUT":
        return (
            f"{head} Tecnico sin confirmar",
            f"{asg.get('timedOutTech') or 'El tecnico'} no confirmo a tiempo {inc.get('incidentId')}. Reasignando.",
        )
    if kind == "LONG_DOWNTIME":
        return f"{head} Detenida mas de lo esperado", f"{inc.get('incidentId')} sigue abierto. Revisar impacto."
    if kind == "RESOLVED":
        closed = payload.get("closed") or {}
        rec = f" Reincidencia de {closed['recurrenceOf']}." if closed.get("recurrenceOf") else ""
        return (
            f"{head} Resuelto",
            f"{inc.get('incidentId')} cerrado. Detenida {closed.get('downtimeMinutes', '-')} min.{rec}",
        )
    if kind == "DAILY_SUMMARY":
        return "Resumen diario de mantenimiento", payload.get("summary") or "Sin novedades."
    return head, detail.get("probableCause", "")


def notify(payload: dict[str, Any], sns: Any, topics: dict[str, str]) -> dict[str, Any]:
    kind = payload.get("kind", "INCIDENT")
    inc = payload.get("incident") or {}
    severity = inc.get("severity", "WARNING")
    roles = [payload["role"]] if payload.get("role") else list(KIND_ROLES.get(kind) or MATRIX)
    sent, skipped = [], []
    for role in roles:
        channel = channel_for(kind, role, severity, payload)
        if channel is None or (channel not in PUSH and kind != "DAILY_SUMMARY"):
            skipped.append({"role": role, "reason": channel or "NOT_IN_MATRIX"})
            continue
        topic = topics.get(role)
        if not topic:
            skipped.append({"role": role, "reason": "NO_TOPIC"})
            continue
        subject, message = compose(kind, role, payload)
        attributes = {
            "severity": severity if kind != "ESCALATED" else "CRITICAL",
            "role": role,
            "channel": channel,
            "kind": kind,
            "machineId": inc.get("machineId"),
            "techId": (payload.get("assignment") or {}).get("techId"),
        }
        response = sns.publish(
            TopicArn=topic,
            Subject=ascii_text(subject, 99),
            Message=message,
            MessageAttributes={k: {"DataType": "String", "StringValue": str(v)} for k, v in attributes.items() if v},
        )
        sent.append({"role": role, "channel": channel, "messageId": response.get("MessageId")})
    return {"kind": kind, "sent": sent, "skipped": skipped}
