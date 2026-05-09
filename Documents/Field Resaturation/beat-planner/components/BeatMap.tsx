"use client";

import { useEffect, useRef, useState } from "react";
import { BeatResult, BeatStore } from "@/types";

type RouteStatus = "not-routed" | "loading" | "routed" | "error";

interface BeatMapProps {
  beats: BeatResult[];
  onBeatsChange: (beats: BeatResult[]) => void;
}

interface LassoSelection {
  stores: (BeatStore & { fromBeatId: number })[];
}

// Ray-casting point-in-polygon
function pointInPolygon(lat: number, lng: number, polygon: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const intersect = yi > lng !== yj > lng && lat < ((xj - xi) * (lng - yi)) / (yj - yi) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

export default function BeatMap({ beats, onBeatsChange }: BeatMapProps) {
  const mapRef = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapInstanceRef = useRef<any>(null);
  const mapInitialized = useRef(false);
  const [selectedGcu, setSelectedGcu] = useState("all");
  const [lassoSelection, setLassoSelection] = useState<LassoSelection | null>(null);
  const [targetBeatId, setTargetBeatId] = useState<number | "">("");
  const [warning, setWarning] = useState("");

  const [routeStatuses, setRouteStatuses] = useState<Record<number, RouteStatus>>({});
  const [routedStores, setRoutedStores] = useState<Record<number, BeatStore[]>>({});

  const gcus = Array.from(new Set(beats.map((b) => b.gcu))).sort();
  const visibleBeats = selectedGcu === "all" ? beats : beats.filter((b) => b.gcu === selectedGcu);

  // Clear stale route statuses and routed stores when beats change (e.g. after lasso reassignment)
  useEffect(() => {
    const validIds = new Set(beats.map((b) => b.beatId));
    setRouteStatuses((prev) => {
      const next = { ...prev };
      for (const id in next) {
        if (!validIds.has(Number(id))) delete next[id];
      }
      return next;
    });
    setRoutedStores((prev) => {
      const next = { ...prev };
      for (const id in next) {
        if (!validIds.has(Number(id))) delete next[id];
      }
      return next;
    });
  }, [beats]);

  async function handleGenerateRoute(beatId: number) {
    const beat = beats.find((b) => b.beatId === beatId);
    if (!beat) return;
    setRouteStatuses((prev) => ({ ...prev, [beatId]: "loading" }));
    try {
      const res = await fetch("/api/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stores: beat.stores }),
      });
      if (!res.ok) throw new Error("Route failed");
      const data = await res.json();
      setRoutedStores((prev) => ({ ...prev, [beatId]: data.orderedStores }));
      setRouteStatuses((prev) => ({ ...prev, [beatId]: "routed" }));
    } catch {
      setRouteStatuses((prev) => ({ ...prev, [beatId]: "error" }));
    }
  }

  // Init map + draw control once
  useEffect(() => {
    if (mapInitialized.current || !mapRef.current) return;
    mapInitialized.current = true;

    (async () => {
      const L = await import("leaflet");
      // leaflet-draw needs window.L before it loads
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any).L = L.default ?? L;
      await import("leaflet-draw");

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Lx: any = (window as any).L;

      // Add leaflet-draw CSS
      if (!document.querySelector('link[href*="leaflet.draw"]')) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = "https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css";
        document.head.appendChild(link);
      }

      // Fix icon paths
      delete Lx.Icon.Default.prototype._getIconUrl;
      Lx.Icon.Default.mergeOptions({
        iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      });

      const map = Lx.map(mapRef.current);
      Lx.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap contributors",
      }).addTo(map);
      mapInstanceRef.current = map;

      // Draw control — polygon only
      const drawnItems = new Lx.FeatureGroup().addTo(map);
      const drawControl = new Lx.Control.Draw({
        draw: {
          polygon: { shapeOptions: { color: "#6366f1", weight: 2 } },
          polyline: false, rectangle: false, circle: false,
          circlemarker: false, marker: false,
        },
        edit: { featureGroup: drawnItems, edit: false, remove: false },
      });
      map.addControl(drawControl);

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      map.on("draw:created", (e: any) => {
        const latlngs: [number, number][] = e.layer.getLatLngs()[0].map(
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (p: any) => [p.lat, p.lng] as [number, number]
        );
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const currentBeats: BeatResult[] = (window as any).__beatPlannerBeats ?? [];
        const selected: (BeatStore & { fromBeatId: number })[] = [];
        for (const beat of currentBeats) {
          for (const store of beat.stores) {
            if (pointInPolygon(store.lat, store.long, latlngs)) {
              selected.push({ ...store, fromBeatId: beat.beatId });
            }
          }
        }
        if (selected.length === 0) return;
        setLassoSelection({ stores: selected });
        setTargetBeatId("");
        setWarning("");
      });
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Redraw markers when beats, filter, or routes change
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const Lx: any = (window as any).L;
    if (!Lx) return;

    // Keep beats accessible to the draw:created handler
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__beatPlannerBeats = beats;

    // Clear marker layers only
    map.eachLayer((layer: unknown) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if (!(layer instanceof Lx.TileLayer) && !(layer as any)._url) {
        try { map.removeLayer(layer); } catch { /* ignore */ }
      }
    });

    const allLatLngs: [number, number][] = [];
    for (const beat of visibleBeats) {
      const ordered = routedStores[beat.beatId];
      if (ordered && ordered.length > 0) {
        // Draw polyline connecting stores in visit sequence
        const latlngs: [number, number][] = ordered.map((s) => [s.lat, s.long]);
        Lx.polyline(latlngs, { color: beat.color, weight: 3, opacity: 0.8 }).addTo(map);
        // Draw numbered DivIcon markers
        ordered.forEach((store, i) => {
          const icon = Lx.divIcon({
            html: `<div style="width:24px;height:24px;border-radius:50%;background:${beat.color};color:white;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:bold;border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.3)">${i + 1}</div>`,
            className: "",
            iconSize: [24, 24],
            iconAnchor: [12, 12],
          });
          const marker = Lx.marker([store.lat, store.long], { icon });
          marker.bindPopup(
            `<b>${store.store_name}</b><br/>Beat ${beat.beatId} · Stop #${i + 1}<br/>` +
            `Last order: ${store.last_delivered_date || "N/A"}<br/>` +
            `Reason: ${store.rejectionReason || "N/A"}`
          );
          marker.addTo(map);
          allLatLngs.push([store.lat, store.long]);
        });
      } else {
        // Draw default circle markers for unrouted beats
        for (const store of beat.stores) {
          const circle = Lx.circleMarker([store.lat, store.long], {
            radius: 6, color: beat.color, fillColor: beat.color,
            fillOpacity: 0.8, weight: 1,
          });
          circle.bindPopup(
            `<b>${store.store_name}</b><br/>Beat ${beat.beatId}<br/>` +
            `Last order: ${store.last_delivered_date || "N/A"}<br/>` +
            `Reason: ${store.rejectionReason || "N/A"}`
          );
          circle.addTo(map);
          allLatLngs.push([store.lat, store.long]);
        }
      }
    }

    if (allLatLngs.length > 0 && !map._fitted) {
      map.fitBounds(allLatLngs);
      map._fitted = true;
    }
  }, [visibleBeats, beats, routedStores]);

  function handleReassign() {
    if (targetBeatId === "" || !lassoSelection) return;

    const selectedUsernames = new Set(lassoSelection.stores.map((s) => s.username));
    const sourceBeatIds = Array.from(new Set(lassoSelection.stores.map((s) => s.fromBeatId)));
    const targetId = Number(targetBeatId);

    // Warn if any source beat would be emptied
    if (!warning) {
      const wouldEmpty = sourceBeatIds.filter((id) => {
        const beat = beats.find((b) => b.beatId === id);
        return beat && beat.stores.filter((s) => !selectedUsernames.has(s.username)).length === 0;
      });
      if (wouldEmpty.length > 0) {
        setWarning(`Beat${wouldEmpty.length > 1 ? "s" : ""} ${wouldEmpty.join(", ")} will be empty and removed. Continue?`);
        return;
      }
    }

    const sourceBeatIdSet = new Set(sourceBeatIds);
    const updated: BeatResult[] = beats
      .map((beat) => {
        if (beat.beatId === targetId) {
          const incoming: BeatStore[] = lassoSelection.stores
            .filter((s) => s.fromBeatId !== targetId)
            .map(({ fromBeatId: _f, ...s }) => s);
          return { ...beat, stores: [...beat.stores, ...incoming], storeCount: beat.storeCount + incoming.length };
        }
        if (sourceBeatIdSet.has(beat.beatId)) {
          const remaining = beat.stores.filter((s) => !selectedUsernames.has(s.username));
          return { ...beat, stores: remaining, storeCount: remaining.length };
        }
        return beat;
      })
      .filter((b) => b.stores.length > 0);

    // Invalidate routes for all affected beats (source + target)
    const affectedIds = [...sourceBeatIds, targetId];
    setRouteStatuses((prev) => {
      const next = { ...prev };
      for (const id of affectedIds) delete next[id];
      return next;
    });
    setRoutedStores((prev) => {
      const next = { ...prev };
      for (const id of affectedIds) delete next[id];
      return next;
    });

    onBeatsChange(updated);
    setLassoSelection(null);
    setWarning("");
  }

  return (
    <div className="mt-6">
      <div className="flex items-center gap-3 mb-3">
        <label className="text-sm font-medium text-gray-700">Filter by GCU:</label>
        <select
          value={selectedGcu}
          onChange={(e) => setSelectedGcu(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm"
        >
          <option value="all">All GCUs</option>
          {gcus.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
        <span className="text-xs text-gray-400 ml-2">
          Use the polygon tool (▱) on the map to select and reassign stores
        </span>
      </div>

      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />

      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <div ref={mapRef} style={{ height: 480 }} className="rounded-lg border border-gray-200" />
        </div>

        {/* Beat list panel */}
        <div className="w-72 flex-shrink-0 border border-gray-200 rounded-lg bg-white overflow-hidden flex flex-col">
          <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
            <h3 className="text-sm font-semibold text-gray-700">Beat Routes</h3>
          </div>
          <div className="overflow-y-auto flex-1" style={{ maxHeight: 480 }}>
            {beats.map((beat) => {
              const status: RouteStatus = routeStatuses[beat.beatId] ?? "not-routed";
              return (
                <div key={beat.beatId} className="flex items-center gap-2 px-4 py-3 border-b border-gray-100 last:border-b-0">
                  <span
                    className="w-3 h-3 rounded-full flex-shrink-0"
                    style={{ backgroundColor: beat.color }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-gray-800">Beat {beat.beatId}</div>
                    <div className="text-xs text-gray-500">{beat.gcu} · {beat.storeCount} stores</div>
                  </div>
                  <div className="flex flex-col items-end gap-1.5">
                    {status === "not-routed" && (
                      <span className="text-xs px-2 py-0.5 bg-gray-100 text-gray-500 rounded-full whitespace-nowrap">Not routed</span>
                    )}
                    {status === "loading" && (
                      <span className="text-xs px-2 py-0.5 bg-blue-100 text-blue-600 rounded-full whitespace-nowrap">Routing…</span>
                    )}
                    {status === "routed" && (
                      <span className="text-xs px-2 py-0.5 bg-green-100 text-green-700 rounded-full whitespace-nowrap">Routed ✓</span>
                    )}
                    {status === "error" && (
                      <span className="text-xs px-2 py-0.5 bg-red-100 text-red-600 rounded-full whitespace-nowrap">Route failed — try again</span>
                    )}
                    <button
                      onClick={() => handleGenerateRoute(beat.beatId)}
                      disabled={status === "loading"}
                      className="text-xs bg-indigo-600 text-white px-2 py-1 rounded hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1 whitespace-nowrap"
                    >
                      {status === "loading" ? (
                        <>
                          <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                          </svg>
                          Loading…
                        </>
                      ) : (
                        "Generate Route"
                      )}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {lassoSelection && (
        <div className="mt-4 p-4 bg-indigo-50 border border-indigo-200 rounded-lg">
          <p className="text-sm font-medium text-indigo-800 mb-3">
            {lassoSelection.stores.length} store{lassoSelection.stores.length !== 1 ? "s" : ""} selected — move to beat:
          </p>
          {warning && (
            <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 mb-3">
              ⚠ {warning}
            </p>
          )}
          <div className="flex items-center gap-3">
            <select
              value={targetBeatId}
              onChange={(e) => { setTargetBeatId(Number(e.target.value)); setWarning(""); }}
              className="border border-gray-300 rounded px-2 py-1 text-sm"
            >
              <option value="">Select target beat…</option>
              {beats
                .filter((b) => !lassoSelection.stores.every((s) => s.fromBeatId === b.beatId))
                .map((b) => (
                  <option key={b.beatId} value={b.beatId}>
                    Beat {b.beatId} ({b.gcu}) — {b.storeCount} stores
                  </option>
                ))}
            </select>
            <button
              onClick={handleReassign}
              disabled={targetBeatId === ""}
              className="bg-indigo-600 text-white px-4 py-1.5 rounded text-sm hover:bg-indigo-700 disabled:opacity-40"
            >
              {warning ? "Confirm" : "Move"}
            </button>
            <button
              onClick={() => { setLassoSelection(null); setWarning(""); }}
              className="text-sm text-gray-500 hover:text-gray-700"
            >
              Cancel
            </button>
          </div>
          <div className="mt-2 text-xs text-gray-500 max-h-20 overflow-y-auto">
            {lassoSelection.stores.map((s) => (
              <span key={s.username} className="mr-2">{s.store_name} (Beat {s.fromBeatId})</span>
            ))}
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-3">
        {visibleBeats.map((beat) => (
          <div key={beat.beatId} className="flex items-center gap-1.5 text-sm">
            <span className="inline-block w-4 h-4 rounded-full border" style={{ backgroundColor: beat.color }} />
            <span>Beat {beat.beatId} ({beat.gcu}) — {beat.storeCount} stores</span>
          </div>
        ))}
      </div>
    </div>
  );
}
