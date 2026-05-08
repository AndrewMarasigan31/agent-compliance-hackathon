"use client";

import { useRouter } from "next/navigation";

export default function DashboardPage() {
  const router = useRouter();

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm px-6 py-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">Beat Planner Dashboard</h1>
        <button
          onClick={handleLogout}
          className="text-sm text-gray-600 hover:text-red-600 border border-gray-300 px-3 py-1 rounded"
        >
          Logout
        </button>
      </header>
      <main className="p-6">
        <p className="text-gray-500">Upload an agent CSV to get started.</p>
      </main>
    </div>
  );
}
