"use client";

import { useState, useRef, DragEvent, ChangeEvent } from "react";

interface GcuSummary {
  gcu: string;
  storeCount: number;
}

interface UploadResult {
  agents: string[];
  totalStores: number;
  excludedCount: number;
  gcus: GcuSummary[];
}

interface UploadFormProps {
  onSuccess: (result: UploadResult & { stores: unknown[] }) => void;
}

const MAX_AGENTS = 5;

export default function UploadForm({ onSuccess }: UploadFormProps) {
  const [agents, setAgents] = useState<string[]>([""]);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    const dropped = e.dataTransfer.files[0];
    if (dropped && dropped.name.endsWith(".csv")) {
      setFile(dropped);
    }
  }

  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0];
    if (selected) setFile(selected);
  }

  function handleAgentChange(index: number, value: string) {
    setAgents((prev) => prev.map((a, i) => (i === index ? value : a)));
  }

  function handleAddAgent() {
    if (agents.length < MAX_AGENTS) {
      setAgents((prev) => [...prev, ""]);
    }
  }

  function handleRemoveAgent(index: number) {
    if (agents.length > 1) {
      setAgents((prev) => prev.filter((_, i) => i !== index));
    }
  }

  const allAgentsFilled = agents.every((a) => a.trim() !== "");
  const canUpload = allAgentsFilled && file !== null;

  async function handleUpload() {
    if (!canUpload) return;

    setError("");
    setLoading(true);
    const formData = new FormData();
    formData.append("file", file!);
    formData.append("agentName", agents[0].trim());

    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();
    setLoading(false);

    if (!res.ok) {
      setError(data.error || "Upload failed");
      return;
    }

    const resultWithAgents = { ...data, agents: agents.map((a) => a.trim()) };
    setResult(resultWithAgents);
    onSuccess(resultWithAgents);
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Agent Names</label>
        <div className="space-y-2">
          {agents.map((agent, index) => (
            <div key={index} className="flex items-center gap-2">
              <input
                type="text"
                value={agent}
                onChange={(e) => handleAgentChange(index, e.target.value)}
                className="flex-1 border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder={`Agent ${index + 1} name`}
              />
              {agents.length > 1 && (
                <button
                  type="button"
                  onClick={() => handleRemoveAgent(index)}
                  className="text-red-500 hover:text-red-700 px-2 py-1 text-sm"
                  aria-label="Remove agent"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
        </div>
        {agents.length < MAX_AGENTS && (
          <button
            type="button"
            onClick={handleAddAgent}
            className="mt-2 text-sm text-blue-600 hover:text-blue-800"
          >
            + Add Agent
          </button>
        )}
      </div>

      <div
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
        onClick={() => fileInputRef.current?.click()}
        className="border-2 border-dashed border-gray-300 rounded-md px-6 py-10 text-center cursor-pointer hover:border-blue-400 transition-colors"
      >
        {file ? (
          <p className="text-sm text-gray-800 font-medium">{file.name}</p>
        ) : (
          <p className="text-sm text-gray-500">Drop a CSV file here or click to select</p>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          onChange={handleFileChange}
          className="hidden"
        />
      </div>

      {error && <p className="text-red-500 text-sm">{error}</p>}

      <button
        onClick={handleUpload}
        disabled={loading || !canUpload}
        className="bg-blue-600 text-white px-6 py-2 rounded-md hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "Uploading..." : "Upload"}
      </button>

      {result && (
        <div className="mt-4 p-4 bg-green-50 rounded-md border border-green-200">
          <h3 className="font-semibold text-green-800 mb-2">Upload Summary</h3>
          <p className="text-sm text-gray-700">Total stores: {result.totalStores}</p>
          <p className="text-sm text-gray-700">Excluded stores: {result.excludedCount}</p>
          <div className="mt-2">
            <p className="text-sm font-medium text-gray-700">GCUs found:</p>
            <ul className="mt-1 space-y-1">
              {result.gcus.map((g) => (
                <li key={g.gcu} className="text-sm text-gray-600">
                  {g.gcu}: {g.storeCount} stores
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}
