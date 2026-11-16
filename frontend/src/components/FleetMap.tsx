import { LngLatBounds, Map as MapLibreMap, Marker, NavigationControl, ScaleControl, setWorkerUrl, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useMemo, useRef } from "react";
import atacamaUrl from "../assets/relieve-flota.webp";
import pascoUrl from "../assets/relieve-cerro-pasco.webp";
import type { Machine, Technician } from "../api/types";
import { SEVERITY_COLOR, STATUS_LABEL } from "../lib/domain";

// The bundle moves maplibre-gl.mjs, so its default worker URL (relative to import.meta.url) 404s.
setWorkerUrl(workerUrl);

// MapLibre expects image corners clockwise from the northwest, in longitude/latitude.
const ATACAMA = new LngLatBounds([-70.13671875, -23.725011735951792], [-69.8291015625, -23.44308893112178]);
const PASCO = new LngLatBounds([-76.376953125, -10.790140750321735], [-76.1572265625, -10.574222078332808]);
const TOWN_CENTER: [number, number] = [-76.257, -10.6815];

function fleetStyle(pascoDemo: boolean): StyleSpecification {
  const bounds = pascoDemo ? PASCO : ATACAMA;
  const streets: StyleSpecification["layers"][number] = { id: "osm", type: "raster", source: "osm" };
  const relief: StyleSpecification["layers"][number] = {
    id: "relief", type: "raster", source: "relief",
    paint: { "raster-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0, 11, 0.40, 13, 0.78] },
  };
  return {
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
        url: pascoDemo ? pascoUrl : atacamaUrl,
        coordinates: [
          bounds.getNorthWest().toArray(), bounds.getNorthEast().toArray(),
          bounds.getSouthEast().toArray(), bounds.getSouthWest().toArray(),
        ],
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#f2efe9" } },
      // In town keep street names legible. The local relief stays visible if street tiles are unavailable.
      ...(pascoDemo ? [relief, streets] : [streets, relief]),
    ],
  };
}

interface Props {
  operationId: string;
  machines: Machine[];
  technicians?: Technician[];
  onSelect?: (machineId: string) => void;
  onHover?: (machineId: string | undefined) => void;
  highlight?: string;
  height?: number;
}

const NO_TECHNICIANS: Technician[] = [];
const located = (p: { lat?: number; lon?: number }) => Number.isFinite(p.lat) && Number.isFinite(p.lon);

export function FleetMap({ operationId, machines: allMachines, technicians: allTechnicians = NO_TECHNICIANS, onSelect, onHover, highlight, height = 460 }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap>(null);
  const framed = useRef(false);
  const markers = useRef<Marker[]>([]);
  const machineEls = useRef(new Map<string, HTMLElement>());
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onHoverRef = useRef(onHover);
  onHoverRef.current = onHover;
  // MapLibre throws on a NaN coordinate, which would blank the page: skip rows without a position.
  const machines = useMemo(() => allMachines.filter(located), [allMachines]);
  const technicians = useMemo(() => allTechnicians.filter(located), [allTechnicians]);
  const pascoDemo = operationId === "ENSAYO-0001";
  const showPascoLocation = pascoDemo && machines.some((m) => PASCO.contains([m.lon, m.lat]));

  useEffect(() => {
    if (!container.current) return;
    const customStyle = import.meta.env.VITE_MAP_STYLE_URL;
    map.current = new MapLibreMap({
      container: container.current,
      style: customStyle || fleetStyle(pascoDemo),
      attributionControl: customStyle ? {} : {
        customAttribution: '<a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md">Relieve: Mapzen · USGS</a>',
      },
      center: pascoDemo ? TOWN_CENTER : [-69.98, -23.602],
      zoom: 13.2,
    });
    map.current.addControl(new NavigationControl(), "top-right");
    map.current.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");
    return () => {
      map.current?.remove();
      map.current = null;
      framed.current = false;
    };
  }, [pascoDemo]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!framed.current && machines.length) {
      const bounds = new LngLatBounds();
      machines.forEach((machine) => bounds.extend([machine.lon, machine.lat]));
      technicians.forEach((tech) => bounds.extend([tech.lon, tech.lat]));
      if (showPascoLocation) bounds.extend(TOWN_CENTER);
      m.fitBounds(bounds, { padding: 40, maxZoom: pascoDemo ? 13.8 : 13.2, duration: 0 });
      framed.current = true;
    }
    markers.current.forEach((mk) => mk.remove());
    machineEls.current.clear();
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
        el.onmouseenter = () => onHoverRef.current?.(machine.machineId);
        el.onmouseleave = () => onHoverRef.current?.(undefined);
        machineEls.current.set(machine.machineId, el);
        return new Marker({ element: el }).setLngLat([machine.lon, machine.lat]).addTo(m);
      }),
      ...technicians.map((t) => {
        const el = document.createElement("div");
        el.className = `marker tech ${(t.availability ?? "").toLowerCase()}`;
        el.title = `${t.name} (${t.techId}) · ${t.availability ?? "sin estado"}`;
        el.textContent = "T";
        return new Marker({ element: el }).setLngLat([t.lon, t.lat]).addTo(m);
      }),
    ];
  }, [machines, technicians, pascoDemo, showPascoLocation]);

  // Hovering a table row lights up its marker (and the other way round through onHover).
  useEffect(() => {
    machineEls.current.forEach((el, id) => el.classList.toggle("hl", id === highlight));
  }, [highlight, machines]);

  return <>
    {showPascoLocation && <p className="map-location">Cerro de Pasco, Perú · Flota simulada</p>}
    <div ref={container} className="map" style={{ height }} />
  </>;
}
