import { NextRequest, NextResponse } from "next/server";
import { BeatResult, BeatStore } from "@/types";

type BeatPayload = BeatResult & { orderedStores?: BeatStore[] };

function csvCell(value: string | number | null | undefined): string {
  const str = String(value ?? "");
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

export async function POST(req: NextRequest) {
  const { agentName, day, beats }: { agentName: string; day: string; beats: BeatPayload[] } =
    await req.json();

  const headers = ["Store Name", "Username", "Beat", "GCU", "Last Order", "Days Dormant", "Tag", "Rejection Reason", "Lat", "Long"];
  const rows: string[][] = [headers];

  for (const beat of beats) {
    const storesInOrder = beat.orderedStores ?? beat.stores;
    for (const store of storesInOrder) {
      rows.push([
        store.store_name,
        store.username,
        String(beat.beatId),
        beat.gcu,
        store.last_delivered_date,
        String(store.daysDormant ?? ""),
        store.bucket,
        store.rejectionReason,
        String(store.lat),
        String(store.long),
      ]);
    }
  }

  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  const filename = `${agentName}-${day}.csv`;

  return new NextResponse(csv, {
    headers: {
      "Content-Type": "text/csv",
      "Content-Disposition": `attachment; filename=${filename}`,
    },
  });
}
