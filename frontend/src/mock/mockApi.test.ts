import { describe, expect, it } from "vitest";
import { MockFleetApi } from "./mockApi";

const OP = "MINA-NORTE";
const START = new Date("2026-11-13T14:00:00Z");

function ticks(api: MockFleetApi, n: number) {
  for (let i = 0; i < n; i++) api.tick();
}

describe("MockFleetApi demo flow", () => {
  it("starts with a healthy fleet and two hours of readings", async () => {
    const api = new MockFleetApi({ startTime: START });
    const machines = await api.listMachines(OP);
    expect(machines).toHaveLength(12);
    expect(machines.every((m) => m.severity === "OK" || m.severity === "WATCH")).toBe(true);
    const readings = await api.getReadings("EXC-0042", "2026-11-13T00:00:00Z", "2026-11-14T00:00:00Z");
    expect(readings.length).toBe(120);
    expect(await api.listIncidents(OP, "OPEN")).toHaveLength(0);
  });

  it("bearing fault -> critical incident with diagnosis, technician and reserved part", async () => {
    const api = new MockFleetApi({ startTime: START });
    await api.injectFault("EXC-0042", "BEARING_WEAR");
    ticks(api, 12);

    const machine = await api.getMachine("EXC-0042");
    expect(machine.severity).toBe("CRITICAL");
    expect(machine.status).toBe("STOPPED");

    const [incident] = await api.listIncidents(OP, "OPEN");
    expect(incident.machineId).toBe("EXC-0042");
    expect(incident.severity).toBe("CRITICAL");
    expect(incident.aiDiagnosis.component).toBe("BEARING");
    expect(incident.assignedTech).toBeDefined();
    expect(incident.partsReserved[0]).toMatchObject({ partId: "BRG-SPH-22", warehouse: "WH-NORTE" });
    expect(incident.messages.OPERATOR).toContain("Detén");
    expect(incident.signals.map((s) => s.metric)).toEqual(expect.arrayContaining(["temperatureC", "vibrationMmS"]));
  });

  it("technician arrival and closure restore the machine and record the history", async () => {
    const api = new MockFleetApi({ startTime: START });
    await api.injectFault("EXC-0042", "BEARING_WEAR");
    ticks(api, 12);
    const [incident] = await api.listIncidents(OP, "OPEN");
    const stockBefore = (await api.listParts()).find((p) => p.partId === "BRG-SPH-22")!.warehouses[0];

    await api.ackIncident("EXC-0042", incident.incidentId, "ARRIVED");
    expect((await api.getMachine("EXC-0042")).status).toBe("MAINTENANCE");
    ticks(api, 30);
    const closed = await api.ackIncident("EXC-0042", incident.incidentId, "RESOLVED");
    ticks(api, 1);

    expect(closed.state).toBe("RESOLVED");
    expect(closed.downtimeMinutes).toBeGreaterThanOrEqual(30);
    expect(closed.timeline.map((t) => t.event)).toEqual(["DETECTED", "ASSIGNED", "ESCALATED", "ARRIVED", "RESOLVED"]);
    expect((await api.getMachine("EXC-0042")).severity).toBe("OK");
    expect((await api.getMaintenance("EXC-0042"))[0].partsReplaced).toEqual(["BRG-SPH-22"]);
    const stockAfter = (await api.listParts()).find((p) => p.partId === "BRG-SPH-22")!.warehouses[0];
    expect(stockAfter.qty).toBe(stockBefore.qty - 1);
    expect(stockAfter.reserved).toBe(0);
    expect(await api.listIncidents(OP, "CLOSED")).toHaveLength(1);
  });

  it("contaminated fuel batch in a zone creates a zone incident (P11)", async () => {
    const api = new MockFleetApi({ startTime: START });
    await api.injectFault("EXC-0042", "FUEL_BATCH_ZONE");
    ticks(api, 12);

    const open = await api.listIncidents(OP, "OPEN");
    const inZone = open.filter((i) => i.zoneId === "Z3");
    expect(inZone.length).toBeGreaterThanOrEqual(3);
    const zoneIncident = inZone.find((i) => i.zoneIncidentId);
    expect(zoneIncident?.aiDiagnosis.zoneCause).toContain("FB-1120");
    expect(inZone.every((i) => i.zoneIncidentId === "ZINC-Z3")).toBe(true);
  });

  it("open incident queue is sorted by severity", async () => {
    const api = new MockFleetApi({ startTime: START });
    await api.injectFault("TRK-0101", "OVERHEATING");
    await api.injectFault("EXC-0043", "BEARING_WEAR");
    ticks(api, 12);
    const open = await api.listIncidents(OP, "OPEN");
    expect(open[0].severity).toBe("CRITICAL");
    expect(open.at(-1)!.severity).toBe("WARNING");
  });
});
