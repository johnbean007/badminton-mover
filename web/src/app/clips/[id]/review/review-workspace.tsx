"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { CORNER_NAMES, COURT_FIT_OK, COURT_LINES, checkCorners, courtToFrame, NET_LINE, project, type Pt } from "@/lib/court";

import { runSideCheck, type Segment, saveCalibration, saveSegments, saveShirtColour, startAnalysis } from "./actions";

type Calibration = { id: string; corners: Pt[]; frame: number };
type Mode = "view" | "corners" | "shirt";
type Props = {
  clipId: string;
  canEdit: boolean;
  fps: number;
  totalFrames: number;
  videoUrl: string;
  initialSegments: Segment[];
  nearSide: Record<string, "near" | "far" | "unclear" | null>;
  courtFit: Record<string, number | null>;
  thumbUrls: Record<string, string>;
  calibrations: Calibration[];
  initialShirt: string | null;
  sideCheck: { status: string; progress: number } | null;
};

const MIN_SEGMENT_FRAMES = 15;

const clock = (frame: number, fps: number) => {
  const s = frame / fps;
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
};

export function ReviewWorkspace(props: Props) {
  const { clipId, canEdit, fps, totalFrames } = props;
  const router = useRouter();
  const video = useRef<HTMLVideoElement>(null);
  const overlay = useRef<SVGSVGElement>(null);
  const stopAt = useRef<number | null>(null);
  const dragging = useRef<number | null>(null);

  // Refreshes re-sign the links; keep the first ones so the video and thumbnails don't reload.
  const [videoUrl] = useState(props.videoUrl);
  const [thumbUrls] = useState(props.thumbUrls);
  const [segments, setSegments] = useState(props.initialSegments);
  const [selected, setSelected] = useState<number | null>(props.initialSegments.length ? 0 : null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [aspect, setAspect] = useState("16 / 9");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [calibrations, setCalibrations] = useState(props.calibrations);
  const [draft, setDraft] = useState<Pt[]>([]);
  const [shirt, setShirt] = useState(props.initialShirt);
  const [sample, setSample] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const seg = selected !== null ? segments[selected] : null;
  const calibration = calibrations.find((c) => c.id === seg?.calibrationId) ?? calibrations.find((c) => segments.some((s) => s.calibrationId === c.id)) ?? null;
  const shownCorners = mode === "corners" ? draft : (calibration?.corners ?? []);
  const H = shownCorners.length === 4 ? courtToFrame(shownCorners) : null;
  const kept = segments.filter((s) => s.included && props.nearSide[s.id ?? ""] !== "far");
  const allCalibrated = kept.length > 0 && kept.every((s) => s.calibrationId);
  const badFit = (s: Segment) => s.id !== null && props.courtFit[s.id] !== null && props.courtFit[s.id]! < COURT_FIT_OK;
  const misfits = kept.filter(badFit).map((s) => segments.indexOf(s) + 1);

  // While the worker checks sides, refresh the server data (segment badges) every few seconds.
  const checking = props.sideCheck?.status === "queued" || props.sideCheck?.status === "running";
  useEffect(() => {
    if (!checking) return;
    const t = setInterval(() => router.refresh(), 4000);
    return () => clearInterval(t);
  }, [checking, router]);

  async function checkSides() {
    setMessage(null);
    const res = await runSideCheck(clipId);
    if (!res.ok) setMessage({ tone: "bad", text: res.message });
    router.refresh();
  }

  // Follow the playhead smoothly while playing, and stop at the end of a segment being previewed.
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const v = video.current;
      if (v) {
        const f = Math.min(totalFrames - 1, Math.floor(v.currentTime * fps + 1e-3));
        setFrame(f);
        if (stopAt.current !== null && f >= stopAt.current) {
          v.pause();
          stopAt.current = null;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, fps, totalFrames]);

  function seek(f: number) {
    const clamped = Math.max(0, Math.min(totalFrames - 1, Math.round(f)));
    if (video.current) video.current.currentTime = (clamped + 0.5) / fps;
    setFrame(clamped);
  }

  function togglePlay() {
    const v = video.current;
    if (!v) return;
    if (v.paused) {
      stopAt.current = null;
      void v.play();
    } else v.pause();
  }

  function playSegment(i: number) {
    setSelected(i);
    seek(segments[i].start);
    stopAt.current = segments[i].end;
    void video.current?.play();
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, select, textarea, button")) return;
      const v = video.current;
      if (!v) return;
      const now = Math.floor(v.currentTime * fps + 1e-3);
      if (e.key === " ") {
        e.preventDefault();
        if (v.paused) void v.play();
        else v.pause();
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        v.pause();
        const step = (e.shiftKey ? Math.round(fps) : 1) * (e.key === "ArrowRight" ? 1 : -1);
        const f = Math.max(0, Math.min(totalFrames - 1, now + step));
        v.currentTime = (f + 0.5) / fps;
        setFrame(f);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fps, totalFrames]);

  async function commit(next: Segment[], nextSelected: number | null) {
    const before = segments;
    setSegments(next);
    setSelected(nextSelected);
    setSaving(true);
    setMessage(null);
    const res = await saveSegments(clipId, next);
    setSaving(false);
    if (res.ok) setSegments(res.segments);
    else {
      setSegments(before);
      setMessage({ tone: "bad", text: res.message });
    }
  }

  const inside = (f: number) => segments.findIndex((s) => f >= s.start && f <= s.end);

  function split() {
    if (!seg || selected === null) return;
    if (frame - seg.start < MIN_SEGMENT_FRAMES || seg.end - frame < MIN_SEGMENT_FRAMES) {
      return setMessage({ tone: "bad", text: "Move the playhead inside the segment, at least half a second from either end, then split." });
    }
    const left = { ...seg, end: frame - 1 };
    const right: Segment = { ...seg, id: null, start: frame };
    void commit([...segments.slice(0, selected), left, right, ...segments.slice(selected + 1)], selected + 1);
  }

  function merge() {
    if (selected === null || selected >= segments.length - 1) return;
    const a = segments[selected];
    const b = segments[selected + 1];
    const joined = { ...a, end: b.end, included: a.included || b.included };
    void commit([...segments.slice(0, selected), joined, ...segments.slice(selected + 2)], selected);
  }

  function trim(edge: "start" | "end") {
    if (!seg || selected === null) return;
    const prevEnd = selected > 0 ? segments[selected - 1].end : -1;
    const nextStart = selected < segments.length - 1 ? segments[selected + 1].start : totalFrames;
    const next = edge === "start" ? { ...seg, start: frame } : { ...seg, end: frame };
    if (next.end - next.start < MIN_SEGMENT_FRAMES) return setMessage({ tone: "bad", text: "A segment must be at least half a second long." });
    if (next.start <= prevEnd || next.end >= nextStart) return setMessage({ tone: "bad", text: "That would overlap the next segment. Merge them instead." });
    void commit(segments.map((s, i) => (i === selected ? next : s)), selected);
  }

  function toggleIncluded() {
    if (!seg || selected === null) return;
    void commit(segments.map((s, i) => (i === selected ? { ...s, included: !s.included } : s)), selected);
  }

  function addSegment() {
    if (inside(frame) >= 0) return setMessage({ tone: "bad", text: "The playhead is already inside a segment." });
    const nextStart = segments.find((s) => s.start > frame)?.start ?? totalFrames;
    const end = Math.min(frame + Math.round(5 * fps), nextStart - 1, totalFrames - 1);
    if (end - frame < MIN_SEGMENT_FRAMES) return setMessage({ tone: "bad", text: "Not enough room before the next segment." });
    const fresh: Segment = { id: null, start: frame, end, included: true, thumbKey: null, calibrationId: calibration?.id ?? null };
    const next = [...segments, fresh].sort((a, b) => a.start - b.start);
    void commit(next, next.indexOf(fresh));
  }

  function framePoint(e: React.PointerEvent): Pt | null {
    const box = overlay.current?.getBoundingClientRect();
    if (!box) return null;
    return [(e.clientX - box.left) / box.width, (e.clientY - box.top) / box.height];
  }

  function onOverlayDown(e: React.PointerEvent<SVGSVGElement>) {
    const p = framePoint(e);
    if (!p) return;
    if (mode === "corners" && draft.length < 4) setDraft([...draft, p]);
    else if (mode === "shirt") pickShirt(p);
  }

  function onOverlayMove(e: React.PointerEvent<SVGSVGElement>) {
    if (dragging.current === null) return;
    const p = framePoint(e);
    if (p) setDraft((d) => d.map((c, i) => (i === dragging.current ? p : c)));
  }

  function pickShirt([x, y]: Pt) {
    const v = video.current;
    if (!v) return;
    try {
      const canvas = document.createElement("canvas");
      canvas.width = v.videoWidth;
      canvas.height = v.videoHeight;
      const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
      ctx.drawImage(v, 0, 0);
      const r = 4;
      const px = ctx.getImageData(Math.round(x * v.videoWidth) - r, Math.round(y * v.videoHeight) - r, 2 * r + 1, 2 * r + 1).data;
      let R = 0, G = 0, B = 0;
      for (let i = 0; i < px.length; i += 4) {
        R += px[i];
        G += px[i + 1];
        B += px[i + 2];
      }
      const n = px.length / 4;
      setSample("#" + [R, G, B].map((c) => Math.round(c / n).toString(16).padStart(2, "0")).join(""));
    } catch {
      setMessage({ tone: "bad", text: "This browser wouldn't let the page read the video's colours. Try Chrome or Safari." });
    }
  }

  async function saveCorners(onlyThis: boolean) {
    const problem = checkCorners(draft);
    if (problem) return setMessage({ tone: "bad", text: problem });
    setSaving(true);
    const res = await saveCalibration(clipId, draft, frame, onlyThis && seg?.id ? seg.id : null);
    setSaving(false);
    if (!res.ok) return setMessage({ tone: "bad", text: res.message });
    setCalibrations([...calibrations, { id: res.calibrationId, corners: draft, frame }]);
    setSegments(segments.map((s) => (!onlyThis || s.id === seg?.id ? { ...s, calibrationId: res.calibrationId } : s)));
    setMode("view");
    setMessage({ tone: "good", text: onlyThis ? "Calibration saved for this segment." : "Calibration saved for every segment." });
    router.refresh();
  }

  async function keepShirt() {
    if (!sample) return;
    setSaving(true);
    const res = await saveShirtColour(clipId, sample);
    setSaving(false);
    if (!res.ok) return setMessage({ tone: "bad", text: res.message });
    setShirt(sample);
    setSample(null);
    setMode("view");
    router.refresh();
  }

  async function analyse() {
    setStarting(true);
    setMessage(null);
    const res = await startAnalysis(clipId);
    setStarting(false);
    if (!res.ok) return setMessage({ tone: "bad", text: res.message });
    router.push("/?analysing=1");
  }

  const lines = H
    ? [...COURT_LINES.map((l) => ({ l, net: false })), { l: NET_LINE, net: true }].map(({ l, net }) => ({ a: project(H, l[0]), b: project(H, l[1]), net }))
    : [];
  const busy = saving || starting;

  return (
    <div className="review">
      <div className="review-main">
        <div className={`stage mode-${mode}`} style={{ aspectRatio: aspect }}>
          <video
            ref={video}
            src={videoUrl}
            crossOrigin="anonymous"
            preload="auto"
            playsInline
            muted
            onLoadedMetadata={(e) => {
              const v = e.currentTarget;
              setAspect(`${v.videoWidth} / ${v.videoHeight}`);
              v.playbackRate = rate;
            }}
            onPlay={() => setPlaying(true)}
            onPause={(e) => {
              setPlaying(false);
              setFrame(Math.floor(e.currentTarget.currentTime * fps + 1e-3));
            }}
            onSeeked={(e) => setFrame(Math.floor(e.currentTarget.currentTime * fps + 1e-3))}
          />
          <svg
            ref={overlay}
            className="overlay"
            viewBox="0 0 1 1"
            preserveAspectRatio="none"
            onPointerDown={onOverlayDown}
            onPointerMove={onOverlayMove}
            onPointerUp={() => (dragging.current = null)}
          >
            {lines.map(({ a, b, net }, i) => (
              <line key={i} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className={net ? "court-net" : "court-line"} vectorEffect="non-scaling-stroke" />
            ))}
            {mode === "corners" && draft.length > 1 && draft.length < 4 ? (
              <polyline points={draft.map((p) => p.join(",")).join(" ")} className="court-line" vectorEffect="non-scaling-stroke" />
            ) : null}
          </svg>
          {mode === "corners"
            ? draft.map((p, i) => (
                <button
                  key={i}
                  type="button"
                  className="corner"
                  style={{ left: `${p[0] * 100}%`, top: `${p[1] * 100}%` }}
                  aria-label={`${CORNER_NAMES[i]} corner, drag to adjust`}
                  onPointerDown={(e) => {
                    e.stopPropagation();
                    dragging.current = i;
                    overlay.current?.setPointerCapture(e.pointerId);
                  }}
                >
                  <span>{CORNER_NAMES[i]}</span>
                </button>
              ))
            : null}
          {mode !== "view" ? (
            <div className="stage-hint">
              {mode === "shirt"
                ? "Click the tracked player's shirt."
                : draft.length < 4
                  ? `Click the ${CORNER_NAMES[draft.length]} outer corner of the court.`
                  : "Drag any corner to adjust, then save."}
            </div>
          ) : null}
        </div>

        <div className="transport">
          <button type="button" className="btn small" onClick={togglePlay}>
            {playing ? "Pause" : "Play"}
          </button>
          <button type="button" className="btn small ghost" onClick={() => seek(frame - 1)} aria-label="Back one frame">
            ◀ 1
          </button>
          <button type="button" className="btn small ghost" onClick={() => seek(frame + 1)} aria-label="Forward one frame">
            1 ▶
          </button>
          <span className="mono small">
            {clock(frame, fps)} · frame {frame}
          </span>
          <div className="spacer" />
          <label className="small muted">
            Speed{" "}
            <select
              className="input small-input"
              value={rate}
              onChange={(e) => {
                const r = Number(e.target.value);
                setRate(r);
                if (video.current) video.current.playbackRate = r;
              }}
            >
              {[0.1, 0.25, 0.5, 1].map((r) => (
                <option key={r} value={r}>
                  {r}×
                </option>
              ))}
            </select>
          </label>
        </div>

        <div
          className="overview"
          role="slider"
          aria-label="Position in clip"
          aria-valuemin={0}
          aria-valuemax={totalFrames - 1}
          aria-valuenow={frame}
          tabIndex={0}
          onPointerDown={(e) => {
            const box = e.currentTarget.getBoundingClientRect();
            seek(((e.clientX - box.left) / box.width) * totalFrames);
          }}
        >
          {segments.map((s, i) => (
            <div
              key={s.id ?? `new-${i}`}
              className={`ov-seg ${s.included ? "" : "off"} ${i === selected ? "sel" : ""}`}
              style={{ left: `${(s.start / totalFrames) * 100}%`, width: `${((s.end - s.start + 1) / totalFrames) * 100}%` }}
            />
          ))}
          <div className="ov-head" style={{ left: `${(frame / totalFrames) * 100}%` }} />
        </div>

        <ol className="segments" aria-label="Proposed rally segments">
          {segments.map((s, i) => {
            const side = s.id ? props.nearSide[s.id] : null;
            const thumb = s.thumbKey ? thumbUrls[s.thumbKey] : null;
            return (
              <li key={s.id ?? `new-${i}`}>
                <button
                  type="button"
                  className={`seg-card ${i === selected ? "sel" : ""} ${s.included && side !== "far" ? "" : "off"}`}
                  onClick={() => {
                    setSelected(i);
                    seek(s.start);
                  }}
                  onDoubleClick={() => playSegment(i)}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- short-lived signed R2 URL */}
                  {thumb ? <img src={thumb} alt="" /> : <span className="seg-thumb-empty" />}
                  <span className="seg-meta">
                    <strong>Segment {i + 1}</strong>
                    <span className="mono small">
                      {clock(s.start, fps)} · {((s.end - s.start + 1) / fps).toFixed(1)} s
                    </span>
                    {!s.included ? (
                      <span className="pill small">Removed</span>
                    ) : side === "far" ? (
                      <span className="pill warn small">Player on far side</span>
                    ) : side === "near" ? (
                      <span className="pill good small">Near side</span>
                    ) : side === "unclear" ? (
                      <span className="pill small">Side unclear</span>
                    ) : null}
                    {s.included && badFit(s) ? <span className="pill warn small">Court doesn&apos;t fit</span> : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
        {segments.length === 0 ? <p className="muted">The pre-scan found no main-camera rallies. Use &ldquo;New segment here&rdquo; to mark one.</p> : null}
      </div>

      <aside className="review-side">
        {message ? <p className={`notice ${message.tone}`}>{message.text}</p> : null}

        <section className="panel pad stack">
          <h2 className="side-title">1 · Segments</h2>
          <p className="muted small">
            {segments.filter((s) => s.included).length} of {segments.length} kept. Remove replays and close-ups; split two rallies the camera didn&apos;t cut between. Double-click a segment to play it.
          </p>
          {canEdit ? (
            <div className="btn-grid">
              <button type="button" className="btn small" disabled={!seg || busy} onClick={() => selected !== null && playSegment(selected)}>
                Play segment
              </button>
              <button type="button" className="btn small" disabled={!seg || busy} onClick={toggleIncluded}>
                {seg?.included === false ? "Restore" : "Remove"}
              </button>
              <button type="button" className="btn small" disabled={!seg || busy} onClick={() => trim("start")}>
                Start here
              </button>
              <button type="button" className="btn small" disabled={!seg || busy} onClick={() => trim("end")}>
                End here
              </button>
              <button type="button" className="btn small" disabled={!seg || busy} onClick={split}>
                Split here
              </button>
              <button type="button" className="btn small" disabled={selected === null || selected >= segments.length - 1 || busy} onClick={merge}>
                Merge with next
              </button>
              <button type="button" className="btn small wide" disabled={busy} onClick={addSegment}>
                New segment here
              </button>
            </div>
          ) : null}
          <p className="muted small">{saving ? "Saving…" : canEdit ? "Changes save as you go." : "Only the uploader or the admin can edit this review."}</p>
        </section>

        <section className="panel pad stack">
          <h2 className="side-title">2 · Court {allCalibrated ? <span className="pill good small">Done</span> : null}</h2>
          <p className="muted small">Pause on a clear view of the court, then click its four outer corners (where the outermost side lines meet the baselines): near-left, near-right, far-right, far-left.</p>
          {canEdit && mode === "corners" ? (
            <div className="btn-grid">
              <button type="button" className="btn small primary" disabled={draft.length < 4 || busy} onClick={() => saveCorners(false)}>
                Save for all segments
              </button>
              <button type="button" className="btn small" disabled={draft.length < 4 || !seg?.id || busy} onClick={() => saveCorners(true)}>
                Save for this segment
              </button>
              <button type="button" className="btn small" onClick={() => setDraft([])}>
                Start again
              </button>
              <button type="button" className="btn small ghost" onClick={() => setMode("view")}>
                Cancel
              </button>
            </div>
          ) : canEdit ? (
            <button
              type="button"
              className="btn small"
              disabled={busy || mode !== "view"}
              onClick={() => {
                video.current?.pause();
                setDraft(calibration?.corners ?? []);
                setMode("corners");
              }}
            >
              {calibration ? "Adjust calibration" : "Calibrate court"}
            </button>
          ) : null}
        </section>

        <section className="panel pad stack">
          <h2 className="side-title">3 · Shirt colour {shirt ? <span className="pill good small">Done</span> : null}</h2>
          <p className="muted small">Click the tracked player&apos;s shirt so the analysis can tell them from their opponent.</p>
          <div className="row">
            {shirt ? <span className="swatch" style={{ background: shirt }} title={`Saved: ${shirt}`} /> : null}
            {sample ? <span className="swatch" style={{ background: sample }} title={`Picked: ${sample}`} /> : null}
          </div>
          {canEdit ? (
            mode === "shirt" ? (
              <div className="btn-grid">
                <button type="button" className="btn small primary" disabled={!sample || busy} onClick={keepShirt}>
                  Use this colour
                </button>
                <button
                  type="button"
                  className="btn small ghost"
                  onClick={() => {
                    setSample(null);
                    setMode("view");
                  }}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="btn small"
                disabled={busy || mode !== "view"}
                onClick={() => {
                  video.current?.pause();
                  setMode("shirt");
                }}
              >
                {shirt ? "Pick again" : "Pick shirt colour"}
              </button>
            )
          ) : null}
        </section>

        <section className="panel pad stack">
          <h2 className="side-title">
            4 · Near-side check {props.sideCheck?.status === "done" ? <span className="pill good small">Done</span> : null}
          </h2>
          <p className="muted small">
            {checking
              ? `Checking which side the player is on… ${Math.round((props.sideCheck?.progress ?? 0) * 100)}%`
              : props.sideCheck?.status === "failed"
                ? "The check failed. Try again."
                : props.sideCheck?.status === "done"
                  ? "Segments with the player on the far side are skipped. “Side unclear” segments are kept."
                  : "Runs by itself once the court and shirt colour are set."}
          </p>
          {canEdit && shirt && allCalibrated && !checking ? (
            <button type="button" className="btn small" disabled={busy} onClick={checkSides}>
              Check again
            </button>
          ) : null}
        </section>

        {canEdit ? (
          <section className="panel pad stack">
            <h2 className="side-title">5 · Analyse</h2>
            <p className="muted small">
              {kept.length} segment{kept.length === 1 ? "" : "s"} will be analysed for footwork and shuttle.
            </p>
            {misfits.length ? (
              <p className="notice warn">
                The court lines don&apos;t match the camera view in segment{misfits.length === 1 ? "" : "s"} {misfits.join(", ")}: the camera zoomed or moved.
                Select {misfits.length === 1 ? "it" : "each one"}, pause on a clear view and click the corners again in step 2 with &ldquo;Save for this segment&rdquo;, or court zones will be wrong.
              </p>
            ) : null}
            <button type="button" className="btn primary" disabled={busy || checking || !shirt || !allCalibrated || kept.length === 0} onClick={analyse}>
              {starting ? "Starting…" : "Analyse"}
            </button>
          </section>
        ) : null}
      </aside>
    </div>
  );
}
