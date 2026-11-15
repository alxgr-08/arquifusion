"""Deterministic diagnosis: fallback when the LLM is unavailable, refuses or fails validation."""

from __future__ import annotations

from collections import Counter

from af_shared import Diagnosis, DiagnosisRequest, DiagnosisResult, RoleMessages, ZoneAnalysis

STRONG_Z = 2.0
ZONE_MIN_OTHERS = 2  # this machine + 2 others = 3 machines (guide 5.4)
HOT_AMBIENT_C = 35.0

METRIC_LABEL = {
    "temperatureC": "temperatura",
    "vibrationMmS": "vibración",
    "pressureBar": "presión hidráulica",
    "fuelRateLph": "consumo de combustible",
    "rpm": "RPM",
}

# component -> (probable cause, recommended action, repair minutes)
PLAYBOOK: dict[str, tuple[str, str, int]] = {
    "BEARING": (
        "Probable desgaste de rodamiento: temperatura y vibración suben juntas.",
        "Inspeccionar rodamientos y lubricación; reemplazar si hay holgura o ruido.",
        120,
    ),
    "HYDRAULIC_SYSTEM": (
        "Probable fuga o falla en el sistema hidráulico: la presión cae por debajo de lo normal.",
        "Revisar mangueras, sellos y nivel de aceite hidráulico; cambiar filtro hidráulico.",
        90,
    ),
    "COOLING_SYSTEM": (
        "Probable falla de refrigeración: la temperatura sube sin otra causa mecánica.",
        "Revisar nivel de refrigerante, radiador obstruido y ventilador.",
        60,
    ),
    "ENGINE": (
        "Probable pérdida de eficiencia del motor: sube el consumo junto con temperatura o RPM anómalas.",
        "Revisar inyección, filtro de aire y compresión del motor.",
        150,
    ),
    "FUEL_SYSTEM": (
        "Consumo de combustible anormal sin otros síntomas: posible inyector o filtro de combustible.",
        "Revisar inyectores y filtro de combustible; verificar calidad del lote.",
        60,
    ),
    "DRIVETRAIN": (
        "Vibración anormal aislada: posible desbalance o componente suelto en el tren de fuerza.",
        "Inspeccionar fijaciones, ejes y orugas o neumáticos.",
        90,
    ),
    "UNKNOWN": (
        "Comportamiento anómalo sin patrón claro.",
        "Inspección general y revisión de sensores.",
        60,
    ),
}


def classify(strong: set[str]) -> tuple[str, str]:
    """Map the set of strongly deviating metrics to (component, confidence)."""
    if {"temperatureC", "vibrationMmS"} <= strong:
        return "BEARING", "HIGH"
    if "pressureBar" in strong:
        return "HYDRAULIC_SYSTEM", "HIGH" if len(strong) == 1 else "MEDIUM"
    if "fuelRateLph" in strong and strong & {"temperatureC", "rpm"}:
        return "ENGINE", "MEDIUM"
    if strong == {"temperatureC"}:
        return "COOLING_SYSTEM", "MEDIUM"
    if strong == {"fuelRateLph"}:
        return "FUEL_SYSTEM", "MEDIUM"
    if strong == {"vibrationMmS"}:
        return "DRIVETRAIN", "MEDIUM"
    if "rpm" in strong:
        return "ENGINE", "LOW"
    return "UNKNOWN", "LOW"


def shared_fuel_batch(req: DiagnosisRequest) -> tuple[str | None, int]:
    """Most common fuel batch in the zone (this machine included) if it explains the zone incident."""
    others = req.zone_correlation
    if len(others) < ZONE_MIN_OTHERS:
        return None, 0
    total = len(others) + 1
    own_batch = req.context.fuel_batch_id if req.context else None
    batches = Counter(m.fuel_batch_id for m in others if m.fuel_batch_id)
    if own_batch:
        batches[own_batch] += 1
    batch, count = batches.most_common(1)[0] if batches else (None, 0)
    return (batch, count) if batch and count >= max(3, total * 0.6) else (None, 0)


def analyze_zone(req: DiagnosisRequest) -> ZoneAnalysis | None:
    others = req.zone_correlation
    if len(others) < ZONE_MIN_OTHERS:
        return None
    a = req.anomaly
    total = len(others) + 1
    evidence = [f"{total} máquinas con incidentes en {a.zone_id} en las últimas 2 h"]

    batch, count = shared_fuel_batch(req)
    if batch:
        evidence.append(f"{count} de {total} usan el lote de combustible {batch}")
        return ZoneAnalysis(
            isZoneIncident=True, commonCause=f"Posible lote de combustible contaminado {batch}.", evidence=evidence
        )

    models = Counter([m.model for m in others] + [a.model])
    model, model_count = models.most_common(1)[0]
    if model_count >= max(3, total * 0.6):
        evidence.append(f"{model_count} de {total} son del modelo {model}")
        return ZoneAnalysis(
            isZoneIncident=True,
            commonCause=f"Posible falla recurrente del modelo {model} o de un componente instalado recientemente.",
            evidence=evidence,
        )

    ambient = req.context.ambient_temp_c if req.context else None
    if ambient is not None and ambient >= HOT_AMBIENT_C:
        evidence.append(f"Temperatura ambiente alta: {ambient:.0f} °C")
        return ZoneAnalysis(
            isZoneIncident=True, commonCause="Condiciones climáticas extremas en la zona.", evidence=evidence
        )

    return ZoneAnalysis(
        isZoneIncident=True,
        commonCause="Causa común no identificada: revisar terreno y condiciones de operación de la zona.",
        evidence=evidence,
    )


def build_messages(req: DiagnosisRequest, d: Diagnosis, zone: ZoneAnalysis | None) -> RoleMessages:
    a = req.anomaly
    where = f"{a.machine_id} ({a.zone_id})"
    readings = ", ".join(
        f"{METRIC_LABEL.get(s.metric, s.metric)} {s.value:g} (normal {s.baseline:g})" for s in a.signals[:3]
    )
    zone_note = f" Incidente de zona: {zone.common_cause}" if zone else ""
    operator = (
        f"Detén {a.machine_id} de forma segura y espera al técnico."
        if d.stop_machine
        else f"Continúa operando {a.machine_id} con precaución y reduce la carga; un técnico revisará pronto."
    )
    return RoleMessages(
        OPERATOR=operator,
        TECHNICIAN=f"{where}: {d.probable_cause} Lecturas: {readings}. Acción: {d.recommended_action}",
        MAINT_LEAD=(
            f"Incidente {a.severity} en {where}, salud {a.health_score:g}/100. Componente: {d.component}. "
            f"Reparación estimada {d.estimated_repair_minutes} min.{zone_note}"
        ),
        SUPERVISOR=(
            f"{a.machine_id} {'detenida' if d.stop_machine else 'operando con restricción'} en {a.zone_id}.{zone_note}"
        ),
        MANAGER=(
            f"{a.operation_id}: incidente {a.severity} en {a.machine_id}; impacto estimado "
            f"{d.estimated_repair_minutes} min de inactividad.{zone_note}"
        ),
    )


def diagnose(req: DiagnosisRequest) -> DiagnosisResult:
    a = req.anomaly
    strong = {s.metric for s in a.signals if s.z_score >= STRONG_Z} or {max(a.signals, key=lambda s: s.z_score).metric}
    component, confidence = classify(strong)
    if "fuelRateLph" in {s.metric for s in a.signals} and shared_fuel_batch(req)[0]:
        component, confidence = "FUEL_SYSTEM", "HIGH"  # contaminated batch explains fuel + temperature (P11)
    cause, action, minutes = PLAYBOOK[component]

    recurrence = [h for h in req.history if h.type == "INCIDENT"]
    if recurrence:
        cause += f" Reincidencia: {len(recurrence)} incidente(s) previo(s) reciente(s)."

    diagnosis = Diagnosis(
        probableCause=cause,
        component=component,
        confidence=confidence,
        recommendedAction=action,
        stopMachine=a.severity == "CRITICAL",
        estimatedRepairMinutes=minutes,
    )
    zone = analyze_zone(req)
    return DiagnosisResult(
        source="RULES",
        modelId=None,
        diagnosis=diagnosis,
        zoneAnalysis=zone,
        messages=build_messages(req, diagnosis, zone),
    )
