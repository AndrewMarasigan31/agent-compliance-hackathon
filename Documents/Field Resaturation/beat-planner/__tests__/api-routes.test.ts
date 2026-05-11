/**
 * @jest-environment node
 */
import { NextRequest } from "next/server";
import { POST as kmlPOST } from "@/app/api/kml/route";
import { POST as csvPOST } from "@/app/api/csv/route";
import { BeatResult, BeatStore } from "@/types";

function makeStore(overrides: Partial<BeatStore> = {}): BeatStore {
  return {
    username: "store-user",
    store_name: "Test Store",
    lat: 14.5,
    long: 121.0,
    gcu: "GCU-Alpha",
    last_delivered_date: "2026-01-01",
    rejectionReason: "",
    bucket: "Active",
    daysDormant: 5,
    ...overrides,
  };
}

function makeBeat(id: number, agent: string, stores: BeatStore[] = []): BeatResult {
  return {
    beatId: id,
    gcu: "GCU-Alpha",
    color: "red",
    storeCount: stores.length,
    stores,
    assignedAgent: agent,
  };
}

function makeRequest(path: string, body: unknown): NextRequest {
  return new NextRequest(`http://localhost${path}`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

describe("POST /api/kml", () => {
  it("KML output contains only beats whose assignedAgent matches the given agentName", async () => {
    const aliceStore = makeStore({ store_name: "Alice Store" });
    const aliceBeat = makeBeat(1, "Alice", [aliceStore]);

    const req = makeRequest("/api/kml", {
      agentName: "Alice",
      day: "Monday",
      beats: [aliceBeat],
    });
    const res = await kmlPOST(req);
    const body = await res.text();

    expect(body).toContain("Alice Store");
    expect(body).toContain("Beat 1");
  });

  it("KML output contains only beats assigned to the given day", async () => {
    const store = makeStore({ store_name: "Monday Store" });
    const beat = makeBeat(2, "Bob", [store]);

    const req = makeRequest("/api/kml", {
      agentName: "Bob",
      day: "Tuesday",
      beats: [beat],
    });
    const res = await kmlPOST(req);
    const body = await res.text();

    expect(body).toContain("Bob - Tuesday");
    expect(body).toContain("Monday Store");
  });

  it("KML filename is [agentName]-[day].kml", async () => {
    const req = makeRequest("/api/kml", {
      agentName: "Ralph",
      day: "Wednesday",
      beats: [],
    });
    const res = await kmlPOST(req);
    const disposition = res.headers.get("Content-Disposition");

    expect(disposition).toContain("Ralph-Wednesday.kml");
  });
});

describe("POST /api/csv", () => {
  it("CSV output contains only rows for beats matching the given agent and day", async () => {
    const store = makeStore({ store_name: "Alice CSV Store", username: "alice-user" });
    const beat = makeBeat(3, "Alice", [store]);

    const req = makeRequest("/api/csv", {
      agentName: "Alice",
      day: "Monday",
      beats: [beat],
    });
    const res = await csvPOST(req);
    const body = await res.text();

    expect(body).toContain("Alice CSV Store");
    expect(body).toContain("alice-user");
  });

  it("CSV filename header is [agentName]-[day].csv", async () => {
    const req = makeRequest("/api/csv", {
      agentName: "Ralph",
      day: "Friday",
      beats: [],
    });
    const res = await csvPOST(req);
    const disposition = res.headers.get("Content-Disposition");

    expect(disposition).toContain("Ralph-Friday.csv");
  });
});
