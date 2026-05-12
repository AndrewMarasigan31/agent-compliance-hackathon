import { NextRequest, NextResponse } from "next/server";
import Papa from "papaparse";
import { Store } from "@/types";

const REQUIRED_COLUMNS = [
  "username",
  "store_name",
  "lat",
  "long",
  "gcu",
  "last_delivered_date",
  "bakit hindi umorder si customer?",
];

const EXCLUDED_REASONS = [
  "Permanently Closed",
  "Temporarily Closed",
  "Duplicate Account",
  "Masikip ang Daan",
  "Hindi mahanap yung tindahan",
];

const ACTIVE_ORDER_STATUSES = [
  "pending",
  "packed",
  "processing",
  "dispatched",
  "ready_to_redispatch",
];

export async function POST(req: NextRequest) {
  const formData = await req.formData();
  const file = formData.get("file");
  const agentName = formData.get("agentName");

  if (!file || !(file instanceof File)) {
    return NextResponse.json({ error: "No file provided" }, { status: 400 });
  }

  if (!file.name.endsWith(".csv")) {
    return NextResponse.json({ error: "File must be a CSV" }, { status: 400 });
  }

  const text = await file.text();

  const parsed = Papa.parse<Record<string, string>>(text, {
    header: true,
    skipEmptyLines: true,
  });

  const headers = parsed.meta.fields || [];
  const missing = REQUIRED_COLUMNS.filter((col) => !headers.includes(col));
  if (missing.length > 0) {
    return NextResponse.json(
      { error: `Missing required columns: ${missing.join(", ")}` },
      { status: 400 }
    );
  }

  let excludedCount = 0;
  const stores: Store[] = [];

  for (const row of parsed.data) {
    const rejectionReason = row["bakit hindi umorder si customer?"] || "";

    if (EXCLUDED_REASONS.includes(rejectionReason)) {
      excludedCount++;
      continue;
    }

    const orderStatus = (row["latest_order_status"] || "").trim().toLowerCase();
    if (ACTIVE_ORDER_STATUSES.includes(orderStatus)) {
      excludedCount++;
      continue;
    }

    // Only include stores delivered within the last 30 days
    const lastDelivered = row["last_delivered_date"]?.trim() || "";
    if (!lastDelivered) {
      excludedCount++;
      continue;
    }
    const deliveryDate = new Date(lastDelivered);
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - 30);
    if (isNaN(deliveryDate.getTime()) || deliveryDate < cutoff) {
      excludedCount++;
      continue;
    }

    stores.push({
      username: row["username"] || "",
      store_name: row["store_name"] || "",
      lat: parseFloat(row["lat"]) || 0,
      long: parseFloat(row["long"]) || 0,
      gcu: row["gcu"] || "",
      last_delivered_date: lastDelivered,
      rejectionReason,
      bucket: row["bucket"] || "",
    });
  }

  const gcuMap: Record<string, number> = {};
  for (const store of stores) {
    gcuMap[store.gcu] = (gcuMap[store.gcu] || 0) + 1;
  }
  const gcus = Object.entries(gcuMap).map(([gcu, storeCount]) => ({ gcu, storeCount }));

  return NextResponse.json({
    agentName: agentName || "",
    totalStores: stores.length,
    excludedCount,
    gcus,
    stores,
  });
}
