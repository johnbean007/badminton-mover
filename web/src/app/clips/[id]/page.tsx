import Link from "next/link";
import { notFound } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { requireMember } from "@/lib/auth";
import { type ClipStatus, STATUS_LABELS } from "@/lib/clips";
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
      "id, status, error, fps, playback_key, tournament, round, match_date, source_url, player:players!clips_player_id_fkey(name, handedness), opponent:players!clips_opponent_id_fkey(name)",
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
    .select("id, start_frame, end_frame, status")
    .eq("clip_id", id)
    .in("status", ["queued", "analysing", "ready", "failed"])
    .order("start_frame");
  const rallies = (rallyRows ?? []).map((r, i) => ({ ...r, number: i + 1 }));
  const ready = rallies.filter((r) => r.status === "ready");
  const wanted = Number(one(query.rally));
  const rally = ready.find((r) => r.number === wanted) ?? ready[0] ?? null;
  const working = status === "queued" || status === "analysing" || rallies.some((r) => r.status === "queued" || r.status === "analysing");

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

  const [{ data: hits }, videoUrl, overlayUrl] = await Promise.all([
    supabase.from("shuttle_hits").select("id, frame, hitter, confidence").eq("rally_id", rally.id).eq("deleted", false).order("frame"),
    signDownload(clip.playback_key, 6 * 3600),
    signDownload(`overlay/${rally.id}.json`, 6 * 3600),
  ]);

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
        />
      </main>
    </>
  );
}
