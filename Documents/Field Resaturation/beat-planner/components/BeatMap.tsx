"use client";

import { useEffect, useRef, useState } from "react";
import { BeatResult } from "@/types";

interface BeatMapProps {
  beats: BeatResult[];
}

export default function BeatMap({ beats }: BeatMapProps) {
  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<unknown>(null);
  const [selectedGcu, setSelectedGcu] = useState<string>("all");

  const gcus = Array.from(new Set(beats.map((b) => b.gcu))).sort();

  const visibleBeats =
    selectedGcu === "all" ? beats : beats.filter((b) => b.gcu === selectedGcu);

  useEffect(() => {
    if (typeof window === "undefined" || !mapRef.current) return;

    // Dynamically import leaflet to avoid SSR issues
    import("leaflet").then((L) => {
      // Fix default marker icon issue with Next.js
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (L.Icon.Default.prototype as any)._getIconUrl;
      L.Icon.Default.mergeOptions({
        iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
        iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
        shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      });

      if (!mapInstanceRef.current) {
        const map = L.map(mapRef.current!);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: "&copy; OpenStreetMap contributors",
        }).addTo(map);
        mapInstanceRef.current = map;
      }

      const map = mapInstanceRef.current as ReturnType<typeof L.map>;

      // Clear existing layers
      map.eachLayer((layer) => {
        if (!(layer instanceof L.TileLayer)) {
          map.removeLayer(layer);
        }
      });

      const allLatLngs: [number, number][] = [];

      for (const beat of visibleBeats) {
        for (const store of beat.stores) {
          const circle = L.circleMarker([store.lat, store.long], {
            radius: 6,
            color: beat.color,
            fillColor: beat.color,
            fillOpacity: 0.8,
            weight: 1,
          });
          circle.bindPopup(
            `<b>${store.store_name}</b><br/>Last order: ${store.last_delivered_date || "N/A"}<br/>Reason: ${store.rejectionReason || "N/A"}`
          );
          circle.addTo(map);
          allLatLngs.push([store.lat, store.long]);
        }
      }

      if (allLatLngs.length > 0) {
        map.fitBounds(allLatLngs);
      }
    });
  }, [visibleBeats]);

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
          {gcus.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
      </div>

      {/* Leaflet CSS */}
      <link
        rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      />

      <div ref={mapRef} style={{ height: 480 }} className="rounded-lg border border-gray-200" />

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-3">
        {visibleBeats.map((beat) => (
          <div key={beat.beatId} className="flex items-center gap-1.5 text-sm">
            <span
              className="inline-block w-4 h-4 rounded-full border"
              style={{ backgroundColor: beat.color }}
            />
            <span>Beat {beat.beatId} ({beat.gcu}) — {beat.storeCount} stores</span>
          </div>
        ))}
      </div>
    </div>
  );
}
