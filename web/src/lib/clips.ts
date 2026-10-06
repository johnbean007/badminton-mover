// Clip rules shared by the upload page (browser) and the server actions.

export const MAX_BYTES = 500 * 1024 * 1024;
export const MAX_SECONDS = 10 * 60;
export const LOW_FPS = 50; // below this, step timing is less precise

export const VIDEO_TYPES: Record<string, string> = { "video/mp4": "mp4", "video/quicktime": "mov" };

export const DISCIPLINES = [
  { value: "MS", label: "Men's singles", enabled: true },
  { value: "WS", label: "Women's singles", enabled: true },
  { value: "MD", label: "Men's doubles", enabled: false },
  { value: "WD", label: "Women's doubles", enabled: false },
  { value: "XD", label: "Mixed doubles", enabled: false },
] as const;
export const SINGLES = ["MS", "WS"];

export const ROUNDS = ["R64", "R32", "R16", "QF", "SF", "F", "Other"] as const;

export type ClipStatus = "uploading" | "uploaded" | "prescanning" | "review" | "queued" | "analysing" | "ready" | "failed";

export const STATUS_LABELS: Record<ClipStatus, string> = {
  uploading: "Upload not finished",
  uploaded: "Waiting for pre-scan",
  prescanning: "Finding rallies…",
  review: "Ready to review",
  queued: "Queued",
  analysing: "Analysing",
  ready: "Ready",
  failed: "Failed",
};

// R2 keys are always derived from the clip id, never trusted from the database row, so a delete can
// only ever touch that clip's own files.
export const clipKeys = (clipId: string, ext: string) => ({
  original: `originals/${clipId}.${ext}`,
  playback: `playback/${clipId}.mp4`,
  thumb: `thumbs/clips/${clipId}.jpg`,
});

export const formatBytes = (n: number) => (n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`);

export const formatDuration = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
