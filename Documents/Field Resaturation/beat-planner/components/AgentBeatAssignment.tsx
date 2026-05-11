"use client";

import { useState } from "react";
import { BeatResult } from "@/types";

interface AgentBeatAssignmentProps {
  beats: BeatResult[];
  agents: string[];
  onAssignmentChange: (beatId: number, agentName: string) => void;
  onContinue: () => void;
}

export default function AgentBeatAssignment({
  beats,
  agents,
  onAssignmentChange,
  onContinue,
}: AgentBeatAssignmentProps) {
  const [assignments, setAssignments] = useState<Record<number, string>>({});

  function handleChange(beatId: number, agentName: string) {
    setAssignments((prev) => ({ ...prev, [beatId]: agentName }));
    onAssignmentChange(beatId, agentName);
  }

  const assignedCount = Object.values(assignments).filter((a) => a !== "").length;
  const allAssigned = assignedCount === beats.length;

  const gcuGroups = beats.reduce<Record<string, BeatResult[]>>((acc, beat) => {
    if (!acc[beat.gcu]) acc[beat.gcu] = [];
    acc[beat.gcu].push(beat);
    return acc;
  }, {});

  const sortedGcus = Object.keys(gcuGroups).sort();

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold">Assign Agents to Beats</h3>
        <span className="text-sm text-gray-600">
          {assignedCount} of {beats.length} beats assigned
        </span>
      </div>

      <table className="w-full text-sm border border-gray-200 rounded-lg overflow-hidden">
        <thead className="bg-gray-100">
          <tr>
            <th className="text-left px-4 py-2">Beat</th>
            <th className="text-left px-4 py-2">Stores</th>
            <th className="text-left px-4 py-2">Agent</th>
          </tr>
        </thead>
        <tbody>
          {sortedGcus.map((gcu) => (
            <>
              <tr key={`gcu-${gcu}`} className="bg-blue-50 border-t border-gray-200">
                <td colSpan={3} className="px-4 py-2 font-semibold text-blue-800">
                  {gcu}
                </td>
              </tr>
              {gcuGroups[gcu].map((beat) => (
                <tr key={beat.beatId} className="border-t border-gray-100">
                  <td className="px-4 py-2 flex items-center gap-2">
                    <span
                      className="w-3 h-3 rounded-full inline-block flex-shrink-0"
                      style={{ backgroundColor: beat.color }}
                    />
                    Beat {beat.beatId}
                  </td>
                  <td className="px-4 py-2">{beat.storeCount}</td>
                  <td className="px-4 py-2">
                    <select
                      value={assignments[beat.beatId] ?? ""}
                      onChange={(e) => handleChange(beat.beatId, e.target.value)}
                      className="border border-gray-300 rounded px-2 py-1 text-sm"
                    >
                      <option value="">-- Assign agent --</option>
                      {agents.map((agent) => (
                        <option key={agent} value={agent}>
                          {agent}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </>
          ))}
        </tbody>
      </table>

      <div className="mt-4 flex justify-end">
        <button
          onClick={onContinue}
          disabled={!allAssigned}
          className="bg-blue-600 text-white px-6 py-2 rounded-md hover:bg-blue-700 disabled:opacity-50"
        >
          Continue
        </button>
      </div>
    </div>
  );
}
