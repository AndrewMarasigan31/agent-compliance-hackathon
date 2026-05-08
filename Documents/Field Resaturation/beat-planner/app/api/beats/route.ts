import { NextRequest, NextResponse } from "next/server";
import { createBeats } from "@/lib/clustering";
import { Store, BeatResult } from "@/types";

function daysDormant(lastDeliveredDate: string): number | null {
  if (!lastDeliveredDate) return null;
  const last = new Date(lastDeliveredDate);
  if (isNaN(last.getTime())) return null;
  const today = new Date();
  const diff = today.getTime() - last.getTime();
  return Math.floor(diff / (1000 * 60 * 60 * 24));
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  const stores: Store[] = body.stores || [];
  const minSize: number = body.minSize ?? 75;

  // Group stores by GCU
  const gcuMap: Record<string, Store[]> = {};
  for (const store of stores) {
    if (!gcuMap[store.gcu]) gcuMap[store.gcu] = [];
    gcuMap[store.gcu].push(store);
  }

  const beats: BeatResult[] = [];
  let globalBeatId = 0;

  for (const [gcu, gcuStores] of Object.entries(gcuMap)) {
    const clusters = createBeats(gcuStores, minSize);
    for (const cluster of clusters) {
      beats.push({
        beatId: globalBeatId++,
        gcu,
        color: cluster.color,
        storeCount: cluster.stores.length,
        stores: cluster.stores.map((s) => ({
          ...s,
          daysDormant: daysDormant(s.last_delivered_date),
        })),
      });
    }
  }

  return NextResponse.json({ beats });
}
