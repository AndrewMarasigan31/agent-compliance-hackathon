export interface Store {
  username: string;
  store_name: string;
  lat: number;
  long: number;
  gcu: string;
  last_delivered_date: string;
  rejectionReason: string;
}

export interface BeatResult {
  beatId: number;
  gcu: string;
  color: string;
  storeCount: number;
  stores: Store[];
}
