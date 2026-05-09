export function nearestNeighborTSP(matrix: number[][], startIndex: number): number[] {
  const n = matrix.length;
  if (n === 0) return [];

  const visited = new Array(n).fill(false);
  const route: number[] = [];
  let current = startIndex;

  for (let step = 0; step < n; step++) {
    visited[current] = true;
    route.push(current);

    let nearestDist = Infinity;
    let nearest = -1;
    for (let j = 0; j < n; j++) {
      if (!visited[j] && matrix[current][j] < nearestDist) {
        nearestDist = matrix[current][j];
        nearest = j;
      }
    }

    if (nearest === -1) break;
    current = nearest;
  }

  return route;
}
