"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import dynamic from "next/dynamic";
import UploadForm from "@/components/UploadForm";
import { Store, BeatResult } from "@/types";

const BeatMap = dynamic(() => import("@/components/BeatMap"), { ssr: false });

interface UploadResult {
  agentName: string;
  totalStores: number;
  excludedCount: number;
  gcus: { gcu: string; storeCount: number }[];
  stores: Store[];
}

export default function DashboardPage() {
  const router = useRouter();
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [beats, setBeats] = useState<BeatResult[] | null>(null);
  const [beatsLoading, setBeatsLoading] = useState(false);
  const [dayAssignments, setDayAssignments] = useState<Record<number, string>>({});

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

  async function handleUploadSuccess(data: UploadResult) {
    setUploadResult(data);
    setBeats(null);
    setDayAssignments({});
    setBeatsLoading(true);

    const res = await fetch("/api/beats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stores: data.stores, minSize: 75 }),
    });
    const result = await res.json();
    setBeatsLoading(false);
    if (res.ok) {
      setBeats(result.beats);
      const initial: Record<number, string> = {};
      result.beats.forEach((b: BeatResult) => { initial[b.beatId] = "Unassigned"; });
      setDayAssignments(initial);
    }
  }

  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  const assignedDays = Array.from(
    new Set(Object.values(dayAssignments).filter((d) => d !== "Unassigned"))
  ).sort((a, b) => days.indexOf(a) - days.indexOf(b));

  async function handleDownload(day: string) {
    if (!beats || !uploadResult) return;
    const dayBeats = beats.filter((b) => dayAssignments[b.beatId] === day);
    const res = await fetch("/api/kml", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentName: uploadResult.agentName, day, beats: dayBeats }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `route-${uploadResult.agentName}-${day.toLowerCase()}.kml`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const daySummary = days.map((day) => ({
    day,
    count: beats ? beats.filter((b) => dayAssignments[b.beatId] === day).length : 0,
  }));

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">Beat Planner</h1>
        <button
          onClick={handleLogout}
          className="text-sm text-gray-600 hover:text-red-600 border border-gray-300 px-3 py-1 rounded"
        >
          Logout
        </button>
      </header>
      <main className="p-6 max-w-5xl mx-auto">
        <h2 className="text-lg font-semibold mb-4">Upload Agent CSV</h2>
        <UploadForm onSuccess={(data) => handleUploadSuccess(data as UploadResult)} />

        {beatsLoading && (
          <p className="mt-6 text-gray-500 text-sm">Generating beats...</p>
        )}

        {beats && (
          <>
            <BeatMap beats={beats} />

            {/* Day assignment table */}
            <div className="mt-8">
              <h3 className="text-lg font-semibold mb-3">Assign Days to Beats</h3>
              <table className="w-full text-sm border border-gray-200 rounded-lg overflow-hidden">
                <thead className="bg-gray-100">
                  <tr>
                    <th className="text-left px-4 py-2">Beat</th>
                    <th className="text-left px-4 py-2">GCU</th>
                    <th className="text-left px-4 py-2">Stores</th>
                    <th className="text-left px-4 py-2">Day</th>
                  </tr>
                </thead>
                <tbody>
                  {beats.map((beat) => (
                    <tr key={beat.beatId} className="border-t border-gray-100">
                      <td className="px-4 py-2 flex items-center gap-2">
                        <span
                          className="w-3 h-3 rounded-full inline-block"
                          style={{ backgroundColor: beat.color }}
                        />
                        Beat {beat.beatId}
                      </td>
                      <td className="px-4 py-2">{beat.gcu}</td>
                      <td className="px-4 py-2">{beat.storeCount}</td>
                      <td className="px-4 py-2">
                        <select
                          value={dayAssignments[beat.beatId] || "Unassigned"}
                          onChange={(e) =>
                            setDayAssignments((prev) => ({ ...prev, [beat.beatId]: e.target.value }))
                          }
                          className="border border-gray-300 rounded px-2 py-1 text-sm"
                        >
                          <option>Unassigned</option>
                          {days.map((d) => <option key={d}>{d}</option>)}
                        </select>
                      </td>
                    </tr>
                  ))}
                  <tr className="border-t-2 border-gray-300 bg-gray-50 font-medium">
                    <td colSpan={3} className="px-4 py-2">Summary</td>
                    <td className="px-4 py-2 text-xs">
                      {daySummary.filter((d) => d.count > 0).map((d) => (
                        <span key={d.day} className="mr-3">{d.day}: {d.count}</span>
                      ))}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Download section */}
            {assignedDays.length > 0 && (
              <div className="mt-8">
                <h3 className="text-lg font-semibold mb-3">Download KML by Day</h3>
                <div className="flex flex-wrap gap-3">
                  {assignedDays.map((day) => (
                    <button
                      key={day}
                      onClick={() => handleDownload(day)}
                      className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 text-sm"
                    >
                      Download {day}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
