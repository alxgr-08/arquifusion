import { Map as MapLibreMap, Marker, NavigationControl, ScaleControl, setWorkerUrl, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useRef } from "react";
import terrainUrl from "../assets/relieve-flota.webp";
import type { Machine, Technician } from "../api/types";
import { SEVERITY_COLOR, STATUS_LABEL } from "../lib/domain";

// The bundle moves maplibre-gl.mjs, so its default worker URL (relative to import.meta.url) 404s.
setWorkerUrl(workerUrl);

const FLEET_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    },
    relief: {
      type: "image",
      url: terrainUrl,
      // Geographic edges of the elevation mosaic, clockwise from its NW corner.
      coordinates: [
        [-70.13671875, -23.44308893112178], [-69.8291015625, -23.44308893112178],
        [-69.8291015625, -23.725011735951792], [-70.13671875, -23.725011735951792],
      ],
    },
  },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#f2efe9" } },
    { id: "osm", type: "raster", source: "osm" },
    {
      id: "relief", type: "raster", source: "relief",
      paint: { "raster-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0, 11, 0.40, 13, 0.78] },
    },
  ],
};

interface Props {
  machines: Machine[];
  technicians?: Technician[];
  onSelect?: (machineId: string) => void;
  height?: number;
}

const NO_TECHNICIANS: Technician[] = [];

export function FleetMap({ machines, technicians = NO_TECHNICIANS, onSelect, height = 460 }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap>(null);
  const markers = useRef<Marker[]>([]);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!container.current) return;
    const customStyle = import.meta.env.VITE_MAP_STYLE_URL;
    map.current = new MapLibreMap({
      container: container.current,
      style: customStyle || FLEET_STYLE,
      attributionControl: customStyle ? {} : {
        customAttribution: '<a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md">Relieve: Mapzen · USGS</a>',
      },
      center: [-69.98, -23.602],
      zoom: 13.2,
    });
    map.current.addControl(new NavigationControl(), "top-right");
    map.current.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");
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
