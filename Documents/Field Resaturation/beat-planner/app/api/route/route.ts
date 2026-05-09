import { NextRequest, NextResponse } from "next/server";
import { nearestNeighborTSP } from "@/lib/routing";

interface RouteStore {
  store_name: string;
  lat: number;
  long: number;
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  const stores: RouteStore[] = body.stores || [];

  if (stores.length === 0) {
    return NextResponse.json({ orderedStores: [] });
  }

  // Compute centroid
  const centroidLat = stores.reduce((sum, s) => sum + s.lat, 0) / stores.length;
  const centroidLng = stores.reduce((sum, s) => sum + s.long, 0) / stores.length;

  // Find starting store closest to centroid (Euclidean distance)
  let startIndex = 0;
  let minDist = Infinity;
  for (let i = 0; i < stores.length; i++) {
    const dLat = stores[i].lat - centroidLat;
    const dLng = stores[i].long - centroidLng;
    const dist = Math.sqrt(dLat * dLat + dLng * dLng);
    if (dist < minDist) {
      minDist = dist;
      startIndex = i;
    }
  }

  // Build OSRM coords: longitude first
  const coords = stores.map((s) => `${s.long},${s.lat}`).join(";");
  const osrmUrl = `https://router.project-osrm.org/table/v1/driving/${coords}`;

  let durations: number[][];
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const response = await fetch(osrmUrl, { signal: controller.signal });
    clearTimeout(timeout);

    if (!response.ok) {
      return NextResponse.json(
        { error: `OSRM returned status ${response.status}` },
        { status: 500 }
      );
    }

    const data = await response.json();
    durations = data.durations;
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: `OSRM call failed: ${message}` }, { status: 500 });
  }

  const order = nearestNeighborTSP(durations, startIndex);
  const orderedStores = order.map((i) => stores[i]);

  return NextResponse.json({ orderedStores });
}
