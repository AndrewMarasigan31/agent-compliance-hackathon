export interface Store {
  username: string;
  store_name: string;
  lat: number;
  long: number;
  gcu: string;
  last_delivered_date: string;
  rejectionReason: string;
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
}
