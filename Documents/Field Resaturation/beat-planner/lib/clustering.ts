import { kmeans } from "ml-kmeans";
import { Store, ClusterResult } from "@/types";

const BEAT_COLORS = [
  "red", "blue", "green", "yellow", "purple",
  "pink", "orange", "ltblue", "brown", "gray",
];

function centroidDistance(a: number[], b: number[]): number {
  return Math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2);
}

export function createBeats(stores: Store[], minSize: number): ClusterResult[] {
  if (stores.length === 0) return [];
  if (stores.length < minSize) {
    return [
      {
        beatId: 0,
        stores,
        color: BEAT_COLORS[0],
      },
    ];
  }

  const k = Math.max(1, Math.floor(stores.length / minSize));
  const data = stores.map((s) => [s.lat, s.long]);

  const result = kmeans(data, k, { initialization: "kmeans++" });

  // Group stores by cluster
  const clusters: Store[][] = Array.from({ length: k }, () => []);
  result.clusters.forEach((clusterIdx, storeIdx) => {
    clusters[clusterIdx].push(stores[storeIdx]);
  });

  // Centroids from result (as [lat, long])
  const centroids: number[][] = result.centroids;

  // Iteratively merge smallest cluster into nearest neighbor until all >= minSize
  let activeClusters = clusters.map((stores, i) => ({ stores, centroid: centroids[i] }));

  let changed = true;
  while (changed) {
    changed = false;
    const smallest = activeClusters.reduce((min, c, i) =>
      c.stores.length < activeClusters[min].stores.length ? i : min, 0);

    if (activeClusters[smallest].stores.length >= minSize) break;
    if (activeClusters.length === 1) break;

    // Find nearest other cluster
    let nearestIdx = -1;
    let nearestDist = Infinity;
    for (let i = 0; i < activeClusters.length; i++) {
      if (i === smallest) continue;
      const d = centroidDistance(activeClusters[smallest].centroid, activeClusters[i].centroid);
      if (d < nearestDist) {
        nearestDist = d;
        nearestIdx = i;
      }
    }

    if (nearestIdx === -1) break;

    // Merge smallest into nearest
    const merged = [
      ...activeClusters[smallest].stores,
      ...activeClusters[nearestIdx].stores,
    ];
    const mergedCentroid = [
      merged.reduce((s, st) => s + st.lat, 0) / merged.length,
      merged.reduce((s, st) => s + st.long, 0) / merged.length,
    ];

    activeClusters[nearestIdx] = { stores: merged, centroid: mergedCentroid };
    activeClusters.splice(smallest, 1);
    changed = true;
  }

  return activeClusters.map((c, i) => ({
    beatId: i,
    stores: c.stores,
    color: BEAT_COLORS[i % BEAT_COLORS.length],
  }));
}
