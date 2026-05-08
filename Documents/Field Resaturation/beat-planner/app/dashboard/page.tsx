"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import UploadForm from "@/components/UploadForm";
import { Store } from "@/types";

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

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

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
      <main className="p-6 max-w-3xl mx-auto">
        <h2 className="text-lg font-semibold mb-4">Upload Agent CSV</h2>
        <UploadForm onSuccess={(data) => setUploadResult(data as UploadResult)} />
        {uploadResult && (
          <p className="mt-6 text-gray-500 text-sm">
            Ready to generate beats for {uploadResult.agentName}.
          </p>
        )}
      </main>
    </div>
  );
}
