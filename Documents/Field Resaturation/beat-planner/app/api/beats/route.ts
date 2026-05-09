import { NextRequest, NextResponse } from "next/server";
import { createBeats } from "@/lib/clustering";
import { Store, BeatResult } from "@/types";

// 20 visually distinct colors — no repeats across all beats
const BEAT_COLORS = [
  "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
  "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
  "#dcbeff", "#9a6324", "#800000", "#aaffc3", "#808000",
  "#ffd8b1", "#000075", "#a9a9a9", "#ffffff", "#000000",
];

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
        beatId: globalBeatId,
        gcu,
        color: BEAT_COLORS[globalBeatId % BEAT_COLORS.length],
        storeCount: cluster.stores.length,
        stores: cluster.stores.map((s) => ({
          ...s,
          daysDormant: daysDormant(s.last_delivered_date),
        })),
      });
      globalBeatId++;
    }
  }

  return NextResponse.json({ beats });
}
