import Link from "next/link";
import { notFound } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { requireMember } from "@/lib/auth";
import { type ClipStatus, STATUS_LABELS } from "@/lib/clips";
import type { Pt } from "@/lib/court";
import type { MovementType } from "@/lib/movements";
import { signDownload } from "@/lib/r2";
import { createClient } from "@/lib/supabase/server";

import { AutoRefresh } from "../clip-actions";
import { RallyViewer } from "./rally-viewer";

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";
const rallyLabels: Record<string, string> = { queued: "Queued", analysing: "Analysing…", failed: "Failed" };

export default async function ViewerPage({ params, searchParams }: PageProps<"/clips/[id]">) {
  const { id } = await params;
  const query = await searchParams;
  const member = await requireMember();
  const supabase = await createClient();
  const { data: clip } = await supabase
    .from("clips")
    .select(
      "id, owner_id, status, error, fps, playback_key, tournament, round, match_date, source_url, player:players!clips_player_id_fkey(name, handedness), opponent:players!clips_opponent_id_fkey(name)",
    )
    .eq("id", id)
    .maybeSingle();
  if (!clip) notFound();

  const player = Array.isArray(clip.player) ? clip.player[0] : clip.player;
  const opponent = Array.isArray(clip.opponent) ? clip.opponent[0] : clip.opponent;
  const status = clip.status as ClipStatus;
  const fps = Number(clip.fps);

  const { data: rallyRows } = await supabase
    .from("rallies")
    .select("id, start_frame, end_frame, status, calibration_id, court_fit")
    .eq("clip_id", id)
    .in("status", ["queued", "analysing", "ready", "failed"])
    .order("start_frame");
  const rallies = (rallyRows ?? []).map((r, i) => ({ ...r, number: i + 1 }));
  const ready = rallies.filter((r) => r.status === "ready");
  const wanted = Number(one(query.rally));
  const rally = ready.find((r) => r.number === wanted) ?? ready[0] ?? null;
  const { data: reruns } = await supabase.from("jobs").select("status").eq("clip_id", id).eq("type", "reanalyse").order("created_at", { ascending: false }).limit(1);
  const updating = reruns?.[0]?.status === "queued" || reruns?.[0]?.status === "running";
  const working = updating || status === "queued" || status === "analysing" || rallies.some((r) => r.status === "queued" || r.status === "analysing");
  const canEdit = clip.owner_id === member.id || member.role === "admin";

  const header = (
    <div className="row between end-align">
      <div>
        <div className="eyebrow">
          <Link href="/">Library</Link> / Viewer
        </div>
        <h1>
          {player?.name ?? "Clip"}
          {opponent?.name ? <span className="muted"> vs {opponent.name}</span> : null}
        </h1>
        <div className="muted small">
          {[clip.tournament, clip.round, clip.match_date && new Date(clip.match_date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })]
            .filter(Boolean)
            .join(" · ") || "No match details"}
          {clip.source_url ? (
            <>
              {" · "}
              <a href={clip.source_url} target="_blank" rel="noopener noreferrer" className="link">
                Source
              </a>
            </>
          ) : null}
        </div>
      </div>
      {fps ? <span className="pill mono">{Math.round(fps * 100) / 100} fps</span> : null}
    </div>
  );

  if (!rally || !clip.playback_key || !fps) {
    const text =
      status === "failed"
        ? (clip.error ?? "The analysis failed.")
        : status === "review"
          ? "This clip's rallies haven't been reviewed yet."
          : `This clip is ${STATUS_LABELS[status]?.toLowerCase() ?? status}. The viewer opens once a rally has been analysed.`;
    return (
      <>
        <AppHeader member={member} />
        <AutoRefresh active={working} />
        <main className="page">
          {header}
          <div className="panel empty">
            <p>{text}</p>
            {status === "review" ? (
              <Link href={`/clips/${id}/review`} className="btn small primary">
                Review rallies
              </Link>
            ) : null}
          </div>
        </main>
      </>
    );
  }

  const [{ data: hits }, { data: subjects }, { data: calibration }, videoUrl, overlayUrl] = await Promise.all([
    supabase.from("shuttle_hits").select("id, frame, hitter, confidence").eq("rally_id", rally.id).eq("deleted", false).order("frame"),
    supabase.from("rally_subjects").select("id").eq("rally_id", rally.id).eq("side", "near").limit(1),
    rally.calibration_id ? supabase.from("calibrations").select("corners").eq("id", rally.calibration_id).maybeSingle() : Promise.resolve({ data: null }),
    signDownload(clip.playback_key, 6 * 3600),
    signDownload(`overlay/${rally.id}.json`, 6 * 3600),
  ]);
  const [{ data: contacts }, { data: movements }] = subjects?.length
    ? await Promise.all([
        supabase
          .from("contacts")
          .select("id, foot, start_frame, end_frame, zone, out_of_court, confidence, court_x_m, court_y_m")
          .eq("subject_id", subjects[0].id)
          .eq("deleted", false)
          .order("start_frame"),
        supabase
          .from("movements")
          .select("id, movement_type, foot, zone, start_frame, end_frame, confidence, details")
          .eq("subject_id", subjects[0].id)
          .eq("deleted", false)
          .order("start_frame"),
      ])
    : [{ data: [] }, { data: [] }];

  return (
    <>
      <AppHeader member={member} />
      <AutoRefresh active={working} />
      <main className="page wide">
        {header}
        <nav className="rally-tabs" aria-label="Rallies">
          {rallies.map((r) => {
            const secs = ((r.end_frame - r.start_frame + 1) / fps).toFixed(1);
            const label = (
              <>
                Rally {r.number} <span className="muted small mono">{secs} s</span>
                {r.status !== "ready" ? <span className="muted small"> · {rallyLabels[r.status] ?? r.status}</span> : null}
              </>
            );
            return r.status === "ready" ? (
              <Link key={r.id} href={`/clips/${id}?rally=${r.number}`} className={`rally-tab ${r.id === rally.id ? "sel" : ""}`} aria-current={r.id === rally.id ? "page" : undefined}>
                {label}
              </Link>
            ) : (
              <span key={r.id} className="rally-tab off">
                {label}
              </span>
            );
          })}
        </nav>
        <RallyViewer
          key={rally.id}
          videoUrl={videoUrl}
          overlayUrl={overlayUrl}
          fps={fps}
          start={rally.start_frame}
          end={rally.end_frame}
          hits={(hits ?? []).map((h) => ({ id: h.id, frame: h.frame, hitter: h.hitter as "player" | "opponent", confidence: h.confidence === null ? null : Number(h.confidence) }))}
          contacts={(contacts ?? []).map((c) => ({
            id: c.id,
            foot: c.foot as "L" | "R",
            start: c.start_frame,
            end: c.end_frame,
            zone: c.zone,
            out: c.out_of_court,
            confidence: c.confidence === null ? null : Number(c.confidence),
          }))}
          movements={(movements ?? []).map((m) => ({
            id: m.id,
            type: m.movement_type as MovementType,
            foot: m.foot as "L" | "R" | "both",
            zone: m.zone,
            start: m.start_frame,
            end: m.end_frame,
            confidence: m.confidence === null ? null : Number(m.confidence),
            details: m.details as Record<string, number | string | boolean | null> | null,
          }))}
          corners={(calibration?.corners as Pt[] | undefined) ?? null}
          courtFit={rally.court_fit === null ? null : Number(rally.court_fit)}
          clipId={id}
          rallyId={rally.id}
          canEdit={canEdit && status === "ready"}
          updating={updating}
        />
      </main>
    </>
  );
}
