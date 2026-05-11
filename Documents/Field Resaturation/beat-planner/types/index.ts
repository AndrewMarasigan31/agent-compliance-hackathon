export interface Store {
  username: string;
  store_name: string;
  lat: number;
  long: number;
  gcu: string;
  last_delivered_date: string;
  rejectionReason: string;
  bucket: string;
}

export interface BeatStore extends Store {
  daysDormant: number | null;
}

export interface ClusterResult {
  beatId: number;
  color: string;
  stores: Store[];
}

export interface BeatResult {
  beatId: number;
  gcu: string;
  color: string;
  storeCount: number;
  stores: BeatStore[];
  assignedAgent?: string;
}

// Dashboard state shape for multi-agent support:
// agents: string[]  — list of agent names entered at upload (replaces single agentName)
// dayAssignments: DayAssignments — per-agent, per-beat day mapping
export type DayAssignments = { [agentName: string]: { [beatId: number]: string } };
