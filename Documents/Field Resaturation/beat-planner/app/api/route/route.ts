import { NextRequest, NextResponse } from "next/server";
import { nearestNeighborTSP } from "@/lib/routing";

interface RouteStore {
  store_name: string;
  lat: number;
  long: number;
}

function haversineKm(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
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

  // Find starting store closest to centroid
  let startIndex = 0;
  let minDist = Infinity;
  for (let i = 0; i < stores.length; i++) {
    const d = haversineKm(stores[i].lat, stores[i].long, centroidLat, centroidLng);
    if (d < minDist) {
      minDist = d;
      startIndex = i;
    }
  }

  // Build Haversine distance matrix
  const n = stores.length;
  const matrix: number[][] = Array.from({ length: n }, (_, i) =>
    Array.from({ length: n }, (_, j) =>
      haversineKm(stores[i].lat, stores[i].long, stores[j].lat, stores[j].long)
    )
  );

  const order = nearestNeighborTSP(matrix, startIndex);
  const orderedStores = order.map((i) => stores[i]);

  return NextResponse.json({ orderedStores });
}
