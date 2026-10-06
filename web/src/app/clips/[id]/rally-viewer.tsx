"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { courtToFrame, type Mat3, project, type Pt } from "@/lib/court";

// overlay/{rally_id}.json, written by the worker: per frame from `start`, the tracked player's 23
// keypoints as [x, y, confidence, …] (x, y in 1/10000ths of the frame, confidence in hundredths)
// and the shuttle as [x, y], or null where nothing was found.
type Overlay = { v: 1; fps: number; start: number; kp: (number[] | null)[]; shuttle: ([number, number] | null)[] };
export type Hit = { id: string; frame: number; hitter: "player" | "opponent"; confidence: number | null };
export type Contact = { id: string; foot: "L" | "R"; start: number; end: number; zone: string | null; out: boolean; confidence: number | null };
type Props = {
  videoUrl: string;
  overlayUrl: string;
  fps: number;
  start: number;
  end: number;
  hits: Hit[];
  contacts: Contact[];
  corners: Pt[] | null; // the court calibration (outer corners, frame fractions), for the zone grid
};
// Which feet are planted at each frame of the rally: bit 1 = left, bit 2 = right.
type Planted = Uint8Array;

const SPEEDS = [0.1, 0.25, 0.5, 1];
const KP_MIN = 0.3;
const TRAIL_S = 0.4;
// Left blue, right orange (a colour-blind-safe pair); torso and head neutral.
const C_LEFT = "#2f7be6";
const C_RIGHT = "#f08a24";
const C_MID = "#e6e6e6";
const C_SHUTTLE = "#ffd84a";
const LEFT = new Set([5, 7, 9, 11, 13, 15, 17, 18, 19]);
const RIGHT = new Set([6, 8, 10, 12, 14, 16, 20, 21, 22]);
const FEET = [15, 16, 17, 18, 19, 20, 21, 22];
const BONES: [number, number][] = [
  [5, 6], [11, 12], [5, 11], [6, 12], [0, 5], [0, 6], [5, 7], [7, 9], [6, 8], [8, 10],
  [11, 13], [13, 15], [15, 19], [15, 17], [17, 18], [19, 17], [12, 14], [14, 16], [16, 22], [16, 20], [20, 21], [22, 20],
];

const LOW_CONFIDENCE = 0.6;
// The nine zones of the near half (court metres: x across, y from the net), matching the worker.
const SINGLES = 2.59;
const COLUMN = (2 * SINGLES) / 3;
const ROWS = [0, 2.0, 4.6, 6.7];

const side = (i: number) => (LEFT.has(i) ? C_LEFT : RIGHT.has(i) ? C_RIGHT : C_MID);

function drawZones(ctx: CanvasRenderingContext2D, H: Mat3, w: number, h: number, dpr: number) {
  const P = (x: number, y: number) => {
    const [u, v] = project(H, [x, y]);
    return [u * w, v * h] as const;
  };
  const line = (a: readonly [number, number], b: readonly [number, number]) => {
    ctx.beginPath();
    ctx.moveTo(...a);
    ctx.lineTo(...b);
    ctx.stroke();
  };
  ctx.save();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
  ctx.lineWidth = 1.5 * dpr;
  ctx.setLineDash([6 * dpr, 5 * dpr]);
  for (const x of [-SINGLES, -COLUMN / 2, COLUMN / 2, SINGLES]) line(P(x, 0), P(x, 6.7));
  for (const y of ROWS) line(P(-SINGLES, y), P(SINGLES, y));
  ctx.restore();
}

function drawOverlay(ctx: CanvasRenderingContext2D, data: Overlay, frame: number, w: number, h: number, dpr: number, planted: Planted | null, zones: Mat3 | null) {
  ctx.clearRect(0, 0, w, h);
  if (zones) drawZones(ctx, zones, w, h, dpr);
  const t = frame - data.start;
  const X = (v: number) => (v / 10000) * w;
  const Y = (v: number) => (v / 10000) * h;
  const down = planted && t >= 0 && t < planted.length ? planted[t] : 0;

  // Shuttle: a fading trail over the last 0.4 s, and a ring where it is now. No ring where it wasn't found.
  const trail = Math.round(TRAIL_S * data.fps);
  ctx.lineCap = "round";
  for (let s = Math.max(1, t - trail + 1); s <= t && s < data.shuttle.length; s++) {
    const a = data.shuttle[s - 1];
    const b = data.shuttle[s];
    if (!a || !b) continue;
    ctx.globalAlpha = 0.15 + 0.85 * (1 - (t - s) / trail);
    ctx.strokeStyle = C_SHUTTLE;
    ctx.lineWidth = 3 * dpr;
    ctx.beginPath();
    ctx.moveTo(X(a[0]), Y(a[1]));
    ctx.lineTo(X(b[0]), Y(b[1]));
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
  const sh = data.shuttle[t];
  if (sh) {
    ctx.strokeStyle = C_SHUTTLE;
    ctx.lineWidth = 2.5 * dpr;
    ctx.beginPath();
    ctx.arc(X(sh[0]), Y(sh[1]), 9 * dpr, 0, 2 * Math.PI);
    ctx.stroke();
  }

  // Skeleton: low-confidence points are faded, not hidden.
  const kp = data.kp[t];
  if (!kp) return;
  const px = (i: number) => X(kp[i * 3]);
  const py = (i: number) => Y(kp[i * 3 + 1]);
  const ok = (i: number) => kp[i * 3 + 2] / 100 > KP_MIN;
  ctx.lineWidth = 3 * dpr;
  for (const [a, b] of BONES) {
    ctx.globalAlpha = ok(a) && ok(b) ? 0.95 : 0.3;
    ctx.strokeStyle = LEFT.has(a) && LEFT.has(b) ? C_LEFT : RIGHT.has(a) && RIGHT.has(b) ? C_RIGHT : C_MID;
    ctx.beginPath();
    ctx.moveTo(px(a), py(a));
    ctx.lineTo(px(b), py(b));
    ctx.stroke();
  }
  for (let i = 0; i < 23; i++) {
    const foot = FEET.includes(i);
    ctx.globalAlpha = ok(i) ? 1 : 0.3;
    ctx.beginPath();
    ctx.arc(px(i), py(i), (foot ? 5 : 3) * dpr, 0, 2 * Math.PI);
    if (foot && !(down & (LEFT.has(i) ? 1 : 2))) {
      // A foot in the air is an outline; a planted foot is filled.
      ctx.strokeStyle = side(i);
      ctx.lineWidth = 2 * dpr;
      ctx.stroke();
    } else {
      ctx.fillStyle = side(i);
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
}

export function RallyViewer({ videoUrl, overlayUrl, fps, start, end, hits, contacts, corners }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const data = useRef<Overlay | null>(null);
  const shown = useRef(-1);
  // Re-signed links on refresh would reload the video; keep the first ones.
  const [urls] = useState({ videoUrl, overlayUrl });
  const [loaded, setLoaded] = useState<"loading" | "ready" | "failed">("loading");
  const [frame, setFrame] = useState(start);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [size, setSize] = useState<[number, number]>([16, 9]);
  const [showZones, setShowZones] = useState(false);
  const zonesRef = useRef<Mat3 | null>(null);
  const planted = useMemo(() => {
    const p = new Uint8Array(end - start + 1);
    for (const c of contacts) for (let f = Math.max(c.start, start); f <= Math.min(c.end, end); f++) p[f - start] |= c.foot === "L" ? 1 : 2;
    return p;
  }, [contacts, start, end]);
  const plantedRef = useRef(planted);

  const toFrame = useCallback((seconds: number) => Math.round(seconds * fps), [fps]);

  const paint = useCallback((f: number) => {
    const c = canvas.current;
    const d = data.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    if (!d) {
      ctx.clearRect(0, 0, c.width, c.height);
      return;
    }
    drawOverlay(ctx, d, f, c.width, c.height, window.devicePixelRatio || 1, plantedRef.current, zonesRef.current);
    shown.current = f;
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch(urls.overlayUrl)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: Overlay) => {
        if (cancelled) return;
        data.current = d;
        setLoaded("ready");
        paint(video.current ? toFrame(video.current.currentTime) : start);
      })
      .catch(() => !cancelled && setLoaded("failed"));
    return () => {
      cancelled = true;
    };
  }, [urls.overlayUrl, paint, toFrame, start]);

  // Keep the canvas at the stage's size in device pixels so lines stay sharp.
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const ro = new ResizeObserver(() => {
      const dpr = window.devicePixelRatio || 1;
      c.width = Math.round(c.clientWidth * dpr);
      c.height = Math.round(c.clientHeight * dpr);
      paint(shown.current >= 0 ? shown.current : start);
    });
    ro.observe(c);
    return () => ro.disconnect();
  }, [paint, start]);

  // Draw on every frame the video actually presents, keyed on that frame's own timestamp, so the
  // overlay stays aligned at every speed. Also stops at the end of the rally.
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    // Every current browser has it; the fallback covers older ones.
    if (typeof (v as Partial<HTMLVideoElement>).requestVideoFrameCallback === "function") {
      let handle = 0;
      const onFrame = (_now: number, meta: VideoFrameCallbackMetadata) => {
        const f = toFrame(meta.mediaTime);
        paint(f);
        setFrame(f);
        if (f >= end && !v.paused) v.pause();
        handle = v.requestVideoFrameCallback(onFrame);
      };
      handle = v.requestVideoFrameCallback(onFrame);
      return () => v.cancelVideoFrameCallback(handle);
    }
    let raf = 0;
    const tick = () => {
      const f = toFrame(v.currentTime);
      if (f !== shown.current) {
        paint(f);
        setFrame(f);
      }
      if (f >= end && !v.paused) v.pause();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [toFrame, paint, end]);

  const seek = useCallback(
    (f: number) => {
      const v = video.current;
      const clamped = Math.max(start, Math.min(end, Math.round(f)));
      // Aim at the middle of the frame so rounding never lands on its neighbour.
      if (v) v.currentTime = (clamped + 0.25) / fps;
      setFrame(clamped);
      paint(clamped);
    },
    [start, end, fps, paint],
  );

  const togglePlay = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (!v.paused) return v.pause();
    if (toFrame(v.currentTime) >= end) seek(start);
    void v.play();
  }, [toFrame, end, seek, start]);

  useEffect(() => {
    plantedRef.current = planted;
    zonesRef.current = showZones && corners ? courtToFrame(corners) : null;
    paint(shown.current >= 0 ? shown.current : start);
  }, [planted, showZones, corners, paint, start]);

  function changeRate(r: number) {
    setRate(r);
    if (video.current) video.current.playbackRate = r;
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, select, textarea, button, a")) return;
      const v = video.current;
      if (!v) return;
      if (e.key === " ") {
        e.preventDefault();
        togglePlay();
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        v.pause();
        const step = (e.shiftKey ? Math.round(fps) : 1) * (e.key === "ArrowRight" ? 1 : -1);
        seek(toFrame(v.currentTime) + step);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay, seek, toFrame, fps]);

  const span = Math.max(1, end - start);
  const pos = (f: number) => `${((f - start) / span) * 100}%`;
  const secs = (f: number) => ((f - start) / fps).toFixed(2);
  const playerHits = hits.filter((h) => h.hitter === "player").length;
  const ms = (frames: number) => Math.round((frames / fps) * 1000);
  // Gap since the previous contact (either foot) ended, for the hover text.
  const gaps = new Map<string, number>();
  let lastEnd: number | null = null;
  for (const c of [...contacts].sort((a, b) => a.start - b.start)) {
    if (lastEnd !== null) gaps.set(c.id, c.start - lastEnd - 1);
    lastEnd = Math.max(lastEnd ?? c.end, c.end);
  }
  const current = contacts.filter((c) => c.start <= frame && frame <= c.end);
  const contactTitle = (c: Contact) =>
    [
      `${c.foot === "L" ? "Left" : "Right"} foot · ${c.zone ?? "no zone"}${c.out ? " (out of court)" : ""}`,
      `${secs(c.start)}–${secs(c.end)} s · ${ms(c.end - c.start + 1)} ms down`,
      gaps.has(c.id) ? `${ms(gaps.get(c.id)!)} ms after the previous contact` : "first contact",
      c.confidence !== null ? `confidence ${Math.round(c.confidence * 100)}%` : "",
    ]
      .filter(Boolean)
      .join("\n");

  return (
    <div className="viewer">
      {/* Width capped so the whole video fits on a laptop screen with the timeline below it. */}
      <div className="stage" style={{ aspectRatio: `${size[0]} / ${size[1]}`, width: `min(100%, calc(72vh * ${size[0] / size[1]}))` }}>
        <video
          ref={video}
          src={urls.videoUrl}
          preload="auto"
          playsInline
          muted
          onLoadedMetadata={(e) => {
            const v = e.currentTarget;
            setSize([v.videoWidth, v.videoHeight]);
            v.playbackRate = rate;
            seek(start);
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onSeeked={(e) => paint(toFrame(e.currentTarget.currentTime))}
        />
        <canvas ref={canvas} className="overlay" aria-hidden="true" />
        {loaded !== "ready" ? (
          <div className="stage-hint">{loaded === "loading" ? "Loading the tracking…" : "Couldn't load the tracking. Refresh to try again."}</div>
        ) : null}
      </div>

      <div className="transport">
        <button type="button" className="btn small" onClick={togglePlay}>
          {playing ? "Pause" : "Play"}
        </button>
        <button type="button" className="btn small ghost" onClick={() => seek(frame - 1)} aria-label="Back one frame">
          ‹
        </button>
        <button type="button" className="btn small ghost" onClick={() => seek(frame + 1)} aria-label="Forward one frame">
          ›
        </button>
        <span className="mono small">
          {secs(frame)} s / {secs(end)} s · frame {frame}
        </span>
        <div className="spacer" />
        {corners ? (
          <label className="small muted toggle">
            <input type="checkbox" checked={showZones} onChange={(e) => setShowZones(e.target.checked)} /> Show zones
          </label>
        ) : null}
        <div className="speeds" role="group" aria-label="Playback speed">
          {SPEEDS.map((s) => (
            <button key={s} type="button" className={`btn small ${rate === s ? "primary" : ""}`} aria-pressed={rate === s} onClick={() => changeRate(s)}>
              {s}×
            </button>
          ))}
        </div>
      </div>
      <input
        type="range"
        className="scrubber"
        min={start}
        max={end}
        step={1}
        value={frame}
        aria-label="Position in the rally"
        onChange={(e) => {
          video.current?.pause();
          seek(Number(e.target.value));
        }}
      />

      <div className="timeline panel">
        <div className="lane">
          <span className="lane-label small muted">Shuttle</span>
          <div className="lane-track">
            {hits.map((h) => (
              <span
                key={h.id}
                className={`hit ${h.hitter}`}
                style={{ left: pos(h.frame) }}
                title={`${h.hitter === "player" ? "Player" : "Opponent"} hit at ${secs(h.frame)} s${h.confidence !== null ? ` · confidence ${Math.round(h.confidence * 100)}%` : ""}`}
              />
            ))}
            <span className="playhead" style={{ left: pos(frame) }} />
          </div>
        </div>
        {(["L", "R"] as const).map((foot) => (
          <div className="lane" key={foot}>
            <span className="lane-label small muted">{foot === "L" ? "Left foot" : "Right foot"}</span>
            <div className="lane-track">
              {contacts
                .filter((c) => c.foot === foot)
                .map((c) => (
                  <span
                    key={c.id}
                    className={`contact ${foot} ${c.confidence !== null && c.confidence < LOW_CONFIDENCE ? "low" : ""} ${current.includes(c) ? "now" : ""}`}
                    style={{ left: pos(c.start), width: `max(3px, ${((c.end - c.start + 1) / span) * 100}%)` }}
                    title={contactTitle(c)}
                  />
                ))}
              <span className="playhead" style={{ left: pos(frame) }} />
            </div>
          </div>
        ))}
        <p className="small muted lane-note">
          {hits.length} hits ({playerHits} by the player) · {contacts.length} foot contacts
          {current.length ? ` · now: ${current.map((c) => `${c.foot} in ${c.zone ?? "?"}${c.out ? " (out)" : ""}`).join(", ")}` : ""}
        </p>
      </div>

      <div className="legend small muted">
        <span>
          <i style={{ background: C_LEFT }} /> Left
        </span>
        <span>
          <i style={{ background: C_RIGHT }} /> Right
        </span>
        <span>
          <i className="ring" style={{ borderColor: C_SHUTTLE }} /> Shuttle
        </span>
        <span>
          <i className="diamond filled" /> Player hit
        </span>
        <span>
          <i className="diamond" /> Opponent hit
        </span>
        <span>Space plays, arrows step a frame (Shift: a second).</span>
      </div>
    </div>
  );
}
