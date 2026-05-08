"use client";

import { useState, useRef, DragEvent, ChangeEvent } from "react";

interface GcuSummary {
  gcu: string;
  storeCount: number;
}

interface UploadResult {
  agentName: string;
  totalStores: number;
  excludedCount: number;
  gcus: GcuSummary[];
}

interface UploadFormProps {
  onSuccess: (result: UploadResult & { stores: unknown[] }) => void;
}

export default function UploadForm({ onSuccess }: UploadFormProps) {
  const [agentName, setAgentName] = useState("");
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

  async function handleUpload() {
    if (!agentName.trim()) {
      setError("Agent name is required");
      return;
    }
    if (!file) {
      setError("Please select a CSV file");
      return;
    }

    setError("");
    setLoading(true);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("agentName", agentName.trim());

    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();
    setLoading(false);

    if (!res.ok) {
      setError(data.error || "Upload failed");
      return;
    }

    setResult(data);
    onSuccess(data);
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Agent Name</label>
        <input
          type="text"
          value={agentName}
          onChange={(e) => setAgentName(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="e.g. JuanDelacruz"
          required
        />
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
        disabled={loading}
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
