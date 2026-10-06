"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { DISCIPLINES, formatBytes, formatDuration, MAX_BYTES, MAX_SECONDS, ROUNDS, VIDEO_TYPES } from "@/lib/clips";

import { deleteClip, finishUpload, startUpload } from "../actions";

type Player = { name: string; handedness: string | null };
type Upload =
  | { state: "none" }
  | { state: "reading"; file: File }
  | { state: "uploading"; file: File; clipId: string; loaded: number }
  | { state: "uploaded"; file: File; clipId: string }
  | { state: "failed"; file: File | null; message: string };

const normalise = (s: string) => s.trim().replace(/\s+/g, " ").toLowerCase();

// Reads duration and size from the file in the browser, and grabs a frame for the library card.
async function probe(file: File) {
  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.preload = "metadata";
  video.muted = true;
  video.src = url;
  try {
    await new Promise<void>((resolve, reject) => {
      video.onloadedmetadata = () => resolve();
      video.onerror = () => reject(new Error("unreadable"));
    });
    const meta = { duration: video.duration, width: video.videoWidth, height: video.videoHeight };

    let thumb: Blob | null = null;
    try {
      video.currentTime = Math.min(meta.duration * 0.1, 5);
      await new Promise<void>((resolve, reject) => {
        video.onseeked = () => resolve();
        setTimeout(() => reject(new Error("seek timeout")), 5000);
      });
      const canvas = document.createElement("canvas");
      canvas.width = 480;
      canvas.height = Math.round((480 * meta.height) / meta.width) || 270;
      canvas.getContext("2d")!.drawImage(video, 0, 0, canvas.width, canvas.height);
      thumb = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.8));
    } catch {
      // No thumbnail if this browser can't decode the video; the card shows a placeholder.
    }
    return { ...meta, thumb };
  } finally {
    URL.revokeObjectURL(url);
  }
}

function put(url: string, body: Blob, type: string, xhrRef: { current: XMLHttpRequest | null }, onProgress?: (loaded: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhrRef.current = xhr;
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", type);
    if (onProgress) xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`R2 returned ${xhr.status}`)));
    xhr.onerror = () => reject(new Error("network"));
    xhr.onabort = () => reject(new Error("aborted"));
    xhr.send(body);
  });
}

export function UploadForm({ players, tournaments }: { players: Player[]; tournaments: string[] }) {
  const router = useRouter();
  const [upload, setUpload] = useState<Upload>({ state: "none" });
  const [playerName, setPlayerName] = useState("");
  const [saving, setSaving] = useState<"no" | "waiting" | "saving">("no");
  const [message, setMessage] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const xhr = useRef<XMLHttpRequest | null>(null);
  const pendingDetails = useRef<FormData | null>(null);
  const clipIdRef = useRef<string | null>(null);

  const busy = upload.state === "reading" || upload.state === "uploading" || saving !== "no";
  useEffect(() => {
    if (!busy) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  const known = players.find((p) => normalise(p.name) === normalise(playerName));

  async function save(clipId: string, details: FormData) {
    setSaving("saving");
    setMessage(null);
    const res = await finishUpload(clipId, details);
    if (res.ok) {
      router.push("/?uploaded=1");
      return;
    }
    setSaving("no");
    setMessage(res.message);
  }

  async function choose(file: File) {
    setMessage(null);
    const type = file.type || (file.name.toLowerCase().endsWith(".mov") ? "video/quicktime" : file.name.toLowerCase().endsWith(".mp4") ? "video/mp4" : "");
    if (!VIDEO_TYPES[type]) return setUpload({ state: "failed", file: null, message: "Only MP4 and MOV videos can be uploaded." });
    if (file.size > MAX_BYTES) return setUpload({ state: "failed", file: null, message: `This video is ${formatBytes(file.size)}. The limit is 500 MB.` });

    setUpload({ state: "reading", file });
    let meta: Awaited<ReturnType<typeof probe>>;
    try {
      meta = await probe(file);
    } catch {
      return setUpload({ state: "failed", file: null, message: "This browser can't read that video. Try exporting it as an H.264 MP4." });
    }
    if (meta.duration > MAX_SECONDS) {
      return setUpload({ state: "failed", file: null, message: `This video is ${formatDuration(meta.duration)} long. The limit is 10 minutes; trim it first.` });
    }

    const start = await startUpload({ type, size: file.size, duration: meta.duration, width: meta.width, height: meta.height, thumbSize: meta.thumb?.size ?? null });
    if (!start.ok) return setUpload({ state: "failed", file: null, message: start.message });

    clipIdRef.current = start.clipId;
    setUpload({ state: "uploading", file, clipId: start.clipId, loaded: 0 });
    try {
      if (start.thumbUrl && meta.thumb) await put(start.thumbUrl, meta.thumb, "image/jpeg", xhr);
      await put(start.uploadUrl, file, type, xhr, (loaded) => setUpload({ state: "uploading", file, clipId: start.clipId, loaded }));
    } catch (e) {
      if ((e as Error).message === "aborted") return;
      await deleteClip(start.clipId);
      clipIdRef.current = null;
      pendingDetails.current = null;
      setSaving("no");
      return setUpload({ state: "failed", file: null, message: "The upload stopped part-way. Check your connection and choose the file again." });
    }
    setUpload({ state: "uploaded", file, clipId: start.clipId });
    if (pendingDetails.current) await save(start.clipId, pendingDetails.current);
  }

  async function cancel() {
    xhr.current?.abort();
    const clipId = clipIdRef.current;
    clipIdRef.current = null;
    pendingDetails.current = null;
    setSaving("no");
    setUpload({ state: "none" });
    if (clipId) await deleteClip(clipId);
  }

  function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const details = new FormData(e.currentTarget);
    if (upload.state === "uploaded") return save(upload.clipId, details);
    if (upload.state === "uploading" || upload.state === "reading") {
      pendingDetails.current = details;
      setSaving("waiting");
      return;
    }
    setMessage("Choose a video first.");
  }

  const file = upload.state === "none" ? null : upload.file;
  const pct = upload.state === "uploading" ? Math.floor((upload.loaded / upload.file.size) * 100) : upload.state === "uploaded" ? 100 : 0;

  return (
    <form onSubmit={submit} className="stack-lg">
      <section className="panel pad stack">
        <div className="label">Video</div>
        {file && upload.state !== "failed" ? (
          <div className="stack">
            <div className="row between">
              <span>
                <strong>{file.name}</strong> <span className="muted mono small">{formatBytes(file.size)}</span>
              </span>
              <button type="button" className="btn ghost small" onClick={cancel} disabled={saving === "saving"}>
                {upload.state === "uploaded" ? "Remove" : "Cancel"}
              </button>
            </div>
            <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Upload progress">
              <div style={{ width: `${pct}%` }} />
            </div>
            <div className="muted small">
              {upload.state === "reading" ? "Reading video…" : upload.state === "uploading" ? `Uploading… ${pct}%. You can fill in the details meanwhile.` : "Uploaded."}
            </div>
          </div>
        ) : (
          <label
            className={`dropzone ${dragging ? "over" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const f = e.dataTransfer.files[0];
              if (f) choose(f);
            }}
          >
            <input type="file" accept="video/mp4,video/quicktime,.mp4,.mov" className="sr-only" onChange={(e) => e.target.files?.[0] && choose(e.target.files[0])} />
            <strong>Drop an MP4 or MOV here, or click to choose</strong>
            <span className="muted small">Up to 10 minutes and 500 MB. Singles, with the player you want to track on the near side.</span>
          </label>
        )}
        {upload.state === "failed" ? <p className="notice bad">{upload.message}</p> : null}
      </section>

      <section className="panel pad grid-form">
        <div className="field">
          <label htmlFor="player" className="label">
            Player <span className="req">required</span>
          </label>
          <input id="player" name="player" required className="input" list="player-names" autoComplete="off" value={playerName} onChange={(e) => setPlayerName(e.target.value)} placeholder="Start typing a name" />
          <span className="muted small">The near-side player whose footwork is tracked. New names are added as players.</span>
        </div>
        <div className="field">
          <label htmlFor="handedness" className="label">
            Handedness <span className="req">required</span>
          </label>
          {known?.handedness ? (
            <div className="input static">{known.handedness === "L" ? "Left-handed" : "Right-handed"} <span className="muted small">· from player record</span></div>
          ) : (
            <select id="handedness" name="handedness" required className="input" defaultValue="">
              <option value="" disabled>
                Choose
              </option>
              <option value="R">Right-handed</option>
              <option value="L">Left-handed</option>
            </select>
          )}
        </div>
        <div className="field">
          <label htmlFor="discipline" className="label">
            Discipline <span className="req">required</span>
          </label>
          <select id="discipline" name="discipline" required className="input" defaultValue="MS">
            {DISCIPLINES.map((d) => (
              <option key={d.value} value={d.value} disabled={!d.enabled}>
                {d.label}
                {d.enabled ? "" : " (coming later)"}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="opponent" className="label">
            Opponent
          </label>
          <input id="opponent" name="opponent" className="input" list="player-names" autoComplete="off" />
        </div>
        <div className="field">
          <label htmlFor="tournament" className="label">
            Tournament
          </label>
          <input id="tournament" name="tournament" className="input" list="tournament-names" autoComplete="off" maxLength={120} placeholder="e.g. All England 2025" />
        </div>
        <div className="field">
          <label htmlFor="round" className="label">
            Round
          </label>
          <select id="round" name="round" className="input" defaultValue="">
            <option value="">Not set</option>
            {ROUNDS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="match_date" className="label">
            Match date
          </label>
          <input id="match_date" name="match_date" type="date" className="input" />
        </div>
        <div className="field">
          <label htmlFor="source_url" className="label">
            Source link
          </label>
          <input id="source_url" name="source_url" type="url" className="input" maxLength={500} placeholder="https://www.youtube.com/watch?v=…" />
        </div>
        <div className="field wide">
          <label htmlFor="notes" className="label">
            Notes
          </label>
          <textarea id="notes" name="notes" className="input" rows={3} maxLength={2000} />
        </div>
        <datalist id="player-names">
          {players.map((p) => (
            <option key={p.name} value={p.name} />
          ))}
        </datalist>
        <datalist id="tournament-names">
          {tournaments.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      </section>

      {message ? <p className="notice bad">{message}</p> : null}
      <div className="row end">
        <button type="submit" className="btn primary" disabled={saving !== "no" || upload.state === "none" || upload.state === "failed"}>
          {saving === "waiting" ? `Will save when the upload finishes (${pct}%)` : saving === "saving" ? "Saving…" : "Save clip"}
        </button>
      </div>
    </form>
  );
}
