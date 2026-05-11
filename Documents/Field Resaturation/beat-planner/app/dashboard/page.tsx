"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import dynamic from "next/dynamic";
import UploadForm from "@/components/UploadForm";
import AgentBeatAssignment from "@/components/AgentBeatAssignment";
import { Store, BeatResult, BeatStore } from "@/types";

const BeatMap = dynamic(() => import("@/components/BeatMap"), { ssr: false });

interface UploadResult {
  agents: string[];
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
  const [beatAssignments, setBeatAssignments] = useState<Record<number, string>>({});
  const [dayAssignments, setDayAssignments] = useState<Record<number, string>>({});
  const [routedStores, setRoutedStores] = useState<Record<number, BeatStore[]>>({});
  // "assign-agents" step shows AgentBeatAssignment; "assign-days" shows day table
  const [step, setStep] = useState<"assign-agents" | "assign-days">("assign-agents");

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

  async function handleUploadSuccess(data: UploadResult) {
    setUploadResult(data);
    setBeats(null);
    setBeatAssignments({});
    setDayAssignments({});
    setStep("assign-agents");
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

  async function buildBeatsPayload(day: string) {
    if (!beats || !uploadResult) return null;
    const dayBeats = beats.filter((b) => dayAssignments[b.beatId] === day);
    return dayBeats.map((b) => ({
      ...b,
      ...(routedStores[b.beatId] ? { orderedStores: routedStores[b.beatId] } : {}),
    }));
  }

  async function handleDownload(day: string) {
    const beatsPayload = await buildBeatsPayload(day);
    if (!beatsPayload || !uploadResult) return;
    const res = await fetch("/api/kml", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentName: uploadResult.agents[0], day, beats: beatsPayload }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `route-${uploadResult.agents[0]}-${day.toLowerCase()}.kml`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleDownloadCsv(day: string) {
    const beatsPayload = await buildBeatsPayload(day);
    if (!beatsPayload || !uploadResult) return;
    const res = await fetch("/api/csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentName: uploadResult.agents[0], day, beats: beatsPayload }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `route-${uploadResult.agents[0]}-${day.toLowerCase()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const daySummary = days.map((day) => ({
    day,
    count: beats ? beats.filter((b) => dayAssignments[b.beatId] === day).length : 0,
  }));

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Beat Planner</h1>
        <button
          onClick={handleLogout}
          className="text-sm text-white bg-red-500 hover:bg-red-600 px-4 py-1.5 rounded"
        >
          Logout
        </button>
      </header>
      <main className="p-6 max-w-4xl mx-auto">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Upload Agent CSV</h2>
        <UploadForm onSuccess={(data) => handleUploadSuccess(data as UploadResult)} />

        {beatsLoading && (
          <p className="mt-6 text-gray-500 text-sm">Generating beats...</p>
        )}

        {beats && (
          <>
            <BeatMap
              beats={beats}
              onBeatsChange={(updated) => {
                setBeats(updated);
                setBeatAssignments((prev) => {
                  const validIds = new Set(updated.map((b) => b.beatId));
                  return Object.fromEntries(Object.entries(prev).filter(([id]) => validIds.has(Number(id))));
                });
                setDayAssignments((prev) => {
                  const validIds = new Set(updated.map((b) => b.beatId));
                  return Object.fromEntries(Object.entries(prev).filter(([id]) => validIds.has(Number(id))));
                });
              }}
              onRoutesChange={(rs) => setRoutedStores(rs)}
            />

            {step === "assign-agents" && uploadResult && (
              <AgentBeatAssignment
                beats={beats}
                agents={uploadResult.agents}
                onAssignmentChange={(beatId, agentName) =>
                  setBeatAssignments((prev) => ({ ...prev, [beatId]: agentName }))
                }
                onContinue={() => {
                  setBeats((prev) =>
                    prev
                      ? prev.map((b) => ({ ...b, assignedAgent: beatAssignments[b.beatId] }))
                      : prev
                  );
                  const initial: Record<number, string> = {};
                  beats.forEach((b) => { initial[b.beatId] = "Unassigned"; });
                  setDayAssignments(initial);
                  setStep("assign-days");
                }}
              />
            )}

            {step === "assign-days" && (
            <>
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
                <h3 className="text-lg font-semibold mb-3">Download by Day</h3>
                <div className="flex flex-wrap gap-3">
                  {assignedDays.map((day) => (
                    <div key={day} className="flex gap-2">
                      <button
                        onClick={() => handleDownload(day)}
                        className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 text-sm"
                      >
                        {day} KML
                      </button>
                      <button
                        onClick={() => handleDownloadCsv(day)}
                        className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 text-sm"
                      >
                        {day} CSV
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
            </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
