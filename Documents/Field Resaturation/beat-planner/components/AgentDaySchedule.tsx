"use client";

import { useState } from "react";
import { BeatResult } from "@/types";

interface AgentDayScheduleProps {
  agents: string[];
  beats: BeatResult[];
  dayAssignments: Record<string, Record<number, string>>;
  onDayChange: (agentName: string, beatId: number, day: string) => void;
}

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export default function AgentDaySchedule({
  agents,
  beats,
  dayAssignments,
  onDayChange,
}: AgentDayScheduleProps) {
  const [activeAgent, setActiveAgent] = useState<string>(agents[0] ?? "");

  const agentBeats = beats.filter((b) => b.assignedAgent === activeAgent);
  const agentDays = dayAssignments[activeAgent] ?? {};

  const daySummary = DAYS.map((day) => ({
    day,
    count: agentBeats.filter((b) => agentDays[b.beatId] === day).length,
  }));

  return (
    <div className="mt-8">
      <h3 className="text-lg font-semibold mb-3">Assign Days to Beats</h3>

      {/* Agent tabs */}
      <div className="flex gap-1 border-b border-gray-200 mb-4">
        {agents.map((agent) => (
          <button
            key={agent}
            onClick={() => setActiveAgent(agent)}
            className={`px-4 py-2 text-sm font-medium rounded-t transition-colors ${
              activeAgent === agent
                ? "bg-white border border-b-white border-gray-200 text-blue-600 -mb-px"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {agent}
          </button>
        ))}
      </div>

      {/* Beat table for active agent */}
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
          {agentBeats.length === 0 ? (
            <tr>
              <td colSpan={4} className="px-4 py-4 text-center text-gray-400 text-sm">
                No beats assigned to {activeAgent}.
              </td>
            </tr>
          ) : (
            agentBeats.map((beat) => (
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
                    value={agentDays[beat.beatId] ?? "Unassigned"}
                    onChange={(e) => onDayChange(activeAgent, beat.beatId, e.target.value)}
                    className="border border-gray-300 rounded px-2 py-1 text-sm"
                  >
                    <option>Unassigned</option>
                    {DAYS.map((d) => (
                      <option key={d}>{d}</option>
                    ))}
                  </select>
                </td>
              </tr>
            ))
          )}
          {agentBeats.length > 0 && (
            <tr className="border-t-2 border-gray-300 bg-gray-50 font-medium">
              <td colSpan={3} className="px-4 py-2">Summary</td>
              <td className="px-4 py-2 text-xs">
                {daySummary.filter((d) => d.count > 0).map((d) => (
                  <span key={d.day} className="mr-3">
                    {d.day}: {d.count}
                  </span>
                ))}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
