import { Map as MapLibreMap, Marker, NavigationControl, setWorkerUrl, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useRef } from "react";
import type { Machine, Technician } from "../api/types";
import { SEVERITY_COLOR, STATUS_LABEL } from "../lib/domain";

// The bundle moves maplibre-gl.mjs, so its default worker URL (relative to import.meta.url) 404s.
setWorkerUrl(workerUrl);

const OSM_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

interface Props {
  machines: Machine[];
  technicians?: Technician[];
  onSelect?: (machineId: string) => void;
  height?: number;
}

/** Amazon Location style when VITE_MAP_STYLE_URL is set (Rol 1), OpenStreetMap otherwise. */
const NO_TECHNICIANS: Technician[] = [];

export function FleetMap({ machines, technicians = NO_TECHNICIANS, onSelect, height = 460 }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap>(null);
  const markers = useRef<Marker[]>([]);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!container.current) return;
    map.current = new MapLibreMap({
      container: container.current,
      style: import.meta.env.VITE_MAP_STYLE_URL || OSM_STYLE,
      center: [-69.98, -23.602],
      zoom: 13.2,
    });
    map.current.addControl(new NavigationControl(), "top-right");
    return () => map.current?.remove();
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    markers.current.forEach((mk) => mk.remove());
    const zones = new Map<string, { lat: number; lon: number; n: number }>();
    for (const machine of machines) {
      const z = zones.get(machine.zoneId) ?? { lat: 0, lon: 0, n: 0 };
      zones.set(machine.zoneId, { lat: z.lat + machine.lat, lon: z.lon + machine.lon, n: z.n + 1 });
    }
    markers.current = [
      ...[...zones].map(([zoneId, z]) => {
        const el = document.createElement("div");
        el.className = "zone-area";
        el.textContent = zoneId;
        return new Marker({ element: el }).setLngLat([z.lon / z.n, z.lat / z.n]).addTo(m);
      }),
      ...machines.map((machine) => {
        const el = document.createElement("button");
        el.className = `marker ${machine.severity === "CRITICAL" ? "pulse" : ""}`;
        el.style.background = machine.status === "OFFLINE" ? "#777" : SEVERITY_COLOR[machine.severity];
        el.title = `${machine.machineId} · ${STATUS_LABEL[machine.status]} · salud ${Math.round(machine.healthScore)}`;
        el.textContent = machine.machineId.split("-")[0][0];
        el.onclick = () => onSelectRef.current?.(machine.machineId);
        return new Marker({ element: el }).setLngLat([machine.lon, machine.lat]).addTo(m);
      }),
      ...technicians.map((t) => {
        const el = document.createElement("div");
        el.className = `marker tech ${t.availability.toLowerCase()}`;
        el.title = `${t.name} (${t.techId}) · ${t.availability}`;
        el.textContent = "T";
        return new Marker({ element: el }).setLngLat([t.lon, t.lat]).addTo(m);
      }),
    ];
  }, [machines, technicians]);

  return <div ref={container} className="map" style={{ height }} />;
}
