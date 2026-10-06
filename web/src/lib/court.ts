// Court geometry, shared by the review page (drawing) and the server (saving).
// Court metres, matching the spike and the worker: x = across the court (positive to the near
// player's right), y = from the net (0) towards the near baseline (+6.7).

export type Pt = [number, number];
export type Mat3 = number[][]; // 3×3

// Members click the four outer corners of the court (where the doubles sidelines meet the
// baselines): they're the most visible points in broadcast footage and what people naturally pick.
export const CORNER_NAMES = ["near-left", "near-right", "far-right", "far-left"] as const;
export const COURT_CORNERS: Pt[] = [
  [-3.05, 6.7],
  [3.05, 6.7],
  [3.05, -6.7],
  [-3.05, -6.7],
];

// Lines to draw over the video as a fit check (court metres).
const HALF_L = 6.7;
const SINGLES = 2.59;
const DOUBLES = 3.05;
const SHORT = 1.98;
const LONG_DOUBLES = 5.94;
export const COURT_LINES: [Pt, Pt][] = [
  [[-DOUBLES, HALF_L], [DOUBLES, HALF_L]],
  [[-DOUBLES, -HALF_L], [DOUBLES, -HALF_L]],
  [[-DOUBLES, -HALF_L], [-DOUBLES, HALF_L]],
  [[DOUBLES, -HALF_L], [DOUBLES, HALF_L]],
  [[-SINGLES, -HALF_L], [-SINGLES, HALF_L]],
  [[SINGLES, -HALF_L], [SINGLES, HALF_L]],
  [[-DOUBLES, SHORT], [DOUBLES, SHORT]],
  [[-DOUBLES, -SHORT], [DOUBLES, -SHORT]],
  [[-DOUBLES, LONG_DOUBLES], [DOUBLES, LONG_DOUBLES]],
  [[-DOUBLES, -LONG_DOUBLES], [DOUBLES, -LONG_DOUBLES]],
  [[0, SHORT], [0, HALF_L]],
  [[0, -SHORT], [0, -HALF_L]],
];
export const NET_LINE: [Pt, Pt] = [[-DOUBLES, 0], [DOUBLES, 0]];

// The homography taking the four `from` points to the four `to` points (standard 8-unknown solve).
export function homography(from: Pt[], to: Pt[]): Mat3 | null {
  const A: number[][] = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = from[i];
    const [u, v] = to[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  }
  // Gaussian elimination with partial pivoting on the 8×9 augmented matrix.
  for (let c = 0; c < 8; c++) {
    let p = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    if (Math.abs(A[p][c]) < 1e-12) return null; // three corners in a line
    [A[c], A[p]] = [A[p], A[c]];
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const f = A[r][c] / A[c][c];
      for (let k = c; k < 9; k++) A[r][k] -= f * A[c][k];
    }
  }
  const h = A.map((row, i) => row[8] / row[i]);
  return [
    [h[0], h[1], h[2]],
    [h[3], h[4], h[5]],
    [h[6], h[7], 1],
  ];
}

export function project(H: Mat3, [x, y]: Pt): Pt {
  const w = H[2][0] * x + H[2][1] * y + H[2][2];
  return [(H[0][0] * x + H[0][1] * y + H[0][2]) / w, (H[1][0] * x + H[1][1] * y + H[1][2]) / w];
}

// Corners are fractions of the frame (0–1). A usable fit is a convex quadrilateral, clicked in
// order, with the near baseline wider on screen than the far one (the camera is behind it).
export function checkCorners(corners: Pt[]): string | null {
  if (corners.length !== 4 || corners.some((p) => p.length !== 2 || p.some((v) => !Number.isFinite(v) || v < -0.2 || v > 1.2))) {
    return "Click all four corners on the video.";
  }
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const signs = corners.map((p, i) => Math.sign(cross(p, corners[(i + 1) % 4], corners[(i + 2) % 4])));
  if (signs.some((s) => s === 0 || s !== signs[0])) return "The corners cross over. Click the outer corners in order: near-left, near-right, far-right, far-left.";
  const nearWidth = Math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]);
  const farWidth = Math.hypot(corners[2][0] - corners[3][0], corners[2][1] - corners[3][1]);
  if (nearWidth <= farWidth) return "The near baseline should look wider than the far one. Check you started with the near-left corner.";
  return null;
}

// The worker scores how well a rally's calibration matches its camera view (share of the court lines
// on white pixels, about 0.95 when it fits); below this the camera has moved and zones would be wrong.
export const COURT_FIT_OK = 0.75;

// When a zoomed-in camera cuts off the near baseline, the near points can instead be where the outer
// side lines meet the near doubles long service line (0.76 m in front of the baseline).
export type NearPoints = "baseline" | "service";
const LONG_SERVICE = 5.94;
export const clickModel = (near: NearPoints): Pt[] =>
  near === "baseline" ? COURT_CORNERS : [[-3.05, LONG_SERVICE], [3.05, LONG_SERVICE], [3.05, -6.7], [-3.05, -6.7]];

// The four outer court corners (frame fractions, possibly off-screen) implied by clicked points, so
// every calibration is stored the same way whichever points were clicked.
export function outerCornersFromClicks(clicks: Pt[], near: NearPoints): Pt[] | null {
  const toFrame = homography(clickModel(near), clicks);
  return toFrame ? COURT_CORNERS.map((p) => project(toFrame, p)) : null;
}

// Frame fractions → court metres (what the worker uses).
export const frameToCourt = (corners: Pt[]) => homography(corners, COURT_CORNERS);
// Court metres → frame fractions (for drawing).
export const courtToFrame = (corners: Pt[]) => homography(COURT_CORNERS, corners);
