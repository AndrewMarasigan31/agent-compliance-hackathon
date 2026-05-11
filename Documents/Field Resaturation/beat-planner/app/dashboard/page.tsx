"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import dynamic from "next/dynamic";
import UploadForm from "@/components/UploadForm";
import AgentBeatAssignment from "@/components/AgentBeatAssignment";
import AgentDaySchedule from "@/components/AgentDaySchedule";
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
  const [dayAssignments, setDayAssignments] = useState<Record<string, Record<number, string>>>({});
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
      setDayAssignments({});
    }
  }

  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  // Flatten all per-agent day assignments to compute assigned days for downloads
  const flatDayAssignments: Record<number, { agentName: string; day: string }> = {};
  for (const [agentName, agentDays] of Object.entries(dayAssignments)) {
    for (const [beatIdStr, day] of Object.entries(agentDays)) {
      if (day !== "Unassigned") {
        flatDayAssignments[Number(beatIdStr)] = { agentName, day };
      }
    }
  }

  const assignedDays = Array.from(
    new Set(Object.values(flatDayAssignments).map((v) => v.day))
  ).sort((a, b) => days.indexOf(a) - days.indexOf(b));

  async function buildBeatsPayload(agentName: string, day: string) {
    if (!beats || !uploadResult) return null;
    const agentDays = dayAssignments[agentName] ?? {};
    const dayBeats = beats.filter(
      (b) => b.assignedAgent === agentName && agentDays[b.beatId] === day
    );
    return dayBeats.map((b) => ({
      ...b,
      ...(routedStores[b.beatId] ? { orderedStores: routedStores[b.beatId] } : {}),
    }));
  }

  async function handleDownload(agentName: string, day: string) {
    const beatsPayload = await buildBeatsPayload(agentName, day);
    if (!beatsPayload || !uploadResult) return;
    const res = await fetch("/api/kml", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentName, day, beats: beatsPayload }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${agentName}-${day.toLowerCase()}.kml`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleDownloadCsv(agentName: string, day: string) {
    const beatsPayload = await buildBeatsPayload(agentName, day);
    if (!beatsPayload || !uploadResult) return;
    const res = await fetch("/api/csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentName, day, beats: beatsPayload }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${agentName}-${day.toLowerCase()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

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
                  const updatedBeats = beats.map((b) => ({ ...b, assignedAgent: beatAssignments[b.beatId] }));
                  setBeats(updatedBeats);
                  // Build nested per-agent day assignments
                  const initial: Record<string, Record<number, string>> = {};
                  for (const b of updatedBeats) {
                    const agent = beatAssignments[b.beatId];
                    if (agent) {
                      if (!initial[agent]) initial[agent] = {};
                      initial[agent][b.beatId] = "Unassigned";
                    }
                  }
                  setDayAssignments(initial);
                  setStep("assign-days");
                }}
              />
            )}

            {step === "assign-days" && uploadResult && (
            <>
            <AgentDaySchedule
              agents={uploadResult.agents}
              beats={beats}
              dayAssignments={dayAssignments}
              onDayChange={(agentName, beatId, day) =>
                setDayAssignments((prev) => ({
                  ...prev,
                  [agentName]: { ...(prev[agentName] ?? {}), [beatId]: day },
                }))
              }
            />

            {/* Download section — per-agent per-day (full per-agent UI comes in US-006) */}
            {assignedDays.length > 0 && uploadResult && (
              <div className="mt-8">
                <h3 className="text-lg font-semibold mb-3">Download by Agent &amp; Day</h3>
                {uploadResult.agents.map((agent) => {
                  const agentDays = dayAssignments[agent] ?? {};
                  const agentAssignedDays = days.filter(
                    (d) => Object.values(agentDays).some((v) => v === d)
                  );
                  if (agentAssignedDays.length === 0) return null;
                  return (
                    <div key={agent} className="mb-6">
                      <h4 className="text-sm font-semibold text-gray-700 mb-2">{agent}</h4>
                      <div className="flex flex-wrap gap-3">
                        {agentAssignedDays.map((day) => (
                          <div key={day} className="flex gap-2">
                            <button
                              onClick={() => handleDownload(agent, day)}
                              className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 text-sm"
                            >
                              {day} KML
                            </button>
                            <button
                              onClick={() => handleDownloadCsv(agent, day)}
                              className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 text-sm"
                            >
                              {day} CSV
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
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
