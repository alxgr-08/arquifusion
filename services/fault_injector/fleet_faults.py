"""Fault types accepted by POST /simulator/faults. Must match services/simulator/fleet_sim.py FAULTS
and FaultType in frontend/src/api/types.ts. Kept here so this Lambda does not import the simulator package."""

FAULT_TYPES = {"BEARING_WEAR", "OVERHEATING", "HYDRAULIC_LEAK", "FUEL_BATCH_ZONE", "SENSOR_FAILURE", "STOPPED"}
ZONE_FAULTS = {"FUEL_BATCH_ZONE"}
