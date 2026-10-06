import Link from "next/link";

import { AppHeader } from "@/components/app-header";
import { requireMember } from "@/lib/auth";
import { type ClipStatus, DISCIPLINES, formatDuration, LOW_FPS, STATUS_LABELS } from "@/lib/clips";
import { signDownload } from "@/lib/r2";
import { createClient } from "@/lib/supabase/server";

import { DeleteClipButton } from "./clips/delete-clip-button";

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? "";
const uuid = /^[0-9a-f-]{36}$/i;

export default async function LibraryPage({ searchParams }: PageProps<"/">) {
  const member = await requireMember();
  const params = await searchParams;
  const filters = {
    player: one(params.player),
    opponent: one(params.opponent),
    tournament: one(params.tournament),
    discipline: one(params.discipline),
    sort: one(params.sort) === "match" ? "match" : "newest",
  };

  const supabase = await createClient();
  let query = supabase
    .from("clips")
    .select(
      "id, owner_id, status, error, discipline, tournament, round, match_date, fps, duration_s, thumb_key, created_at, player:players!clips_player_id_fkey(name), opponent:players!clips_opponent_id_fkey(name)",
    )
    // Unfinished uploads are only shown to the member who started them, so they can delete them.
    .or(`status.neq.uploading,owner_id.eq.${member.id}`);
  if (uuid.test(filters.player)) query = query.eq("player_id", filters.player);
  if (uuid.test(filters.opponent)) query = query.eq("opponent_id", filters.opponent);
  if (filters.tournament) query = query.eq("tournament", filters.tournament);
  if (filters.discipline) query = query.eq("discipline", filters.discipline);
  query =
    filters.sort === "match"
      ? query.order("match_date", { ascending: false, nullsFirst: false }).order("created_at", { ascending: false })
      : query.order("created_at", { ascending: false });

  const [{ data: clips, error }, { data: players }, { data: tournamentRows }] = await Promise.all([
    query.limit(200),
    supabase.from("players").select("id, name").order("name"),
    supabase.from("clips").select("tournament").not("tournament", "is", null).neq("status", "uploading"),
  ]);
  if (error) console.error("library query failed", error);
  const tournaments = [...new Set((tournamentRows ?? []).map((r) => r.tournament as string))].sort();

  const cards = await Promise.all(
    (clips ?? []).map(async (c) => ({
      ...c,
      // Supabase types a to-one join as an object here, but be tolerant of an array.
      playerName: (Array.isArray(c.player) ? c.player[0] : c.player)?.name as string | undefined,
      opponentName: (Array.isArray(c.opponent) ? c.opponent[0] : c.opponent)?.name as string | undefined,
      thumbUrl: c.thumb_key && c.status !== "uploading" ? await signDownload(c.thumb_key) : null,
    })),
  );
  const filtered = Boolean(filters.player || filters.opponent || filters.tournament || filters.discipline);
  const uploaded = one(params.uploaded) === "1";

  return (
    <>
      <AppHeader member={member} />
      <main className="page">
        <div className="row between end-align">
          <div>
            <div className="eyebrow">Shared library</div>
            <h1>Pro clips</h1>
          </div>
          <Link href="/clips/new" className="btn primary">
            Upload a clip
          </Link>
        </div>

        {uploaded ? <p className="notice good">Clip uploaded. It&apos;s waiting for the pre-scan, which will find the rallies.</p> : null}

        <form className="panel pad filters" method="get">
          <label className="field">
            <span className="label">Player</span>
            <select name="player" className="input" defaultValue={filters.player}>
              <option value="">Anyone</option>
              {(players ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="label">Opponent</span>
            <select name="opponent" className="input" defaultValue={filters.opponent}>
              <option value="">Anyone</option>
              {(players ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="label">Tournament</span>
            <select name="tournament" className="input" defaultValue={filters.tournament}>
              <option value="">Any</option>
              {tournaments.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="label">Discipline</span>
            <select name="discipline" className="input" defaultValue={filters.discipline}>
              <option value="">Any</option>
              {DISCIPLINES.filter((d) => d.enabled).map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="label">Sort</span>
            <select name="sort" className="input" defaultValue={filters.sort}>
              <option value="newest">Newest uploaded</option>
              <option value="match">Match date</option>
            </select>
          </label>
          <div className="row filter-actions">
            <button type="submit" className="btn">
              Apply
            </button>
            {filtered || filters.sort !== "newest" ? (
              <Link href="/" className="btn ghost">
                Clear
              </Link>
            ) : null}
          </div>
        </form>

        {cards.length === 0 ? (
          <div className="panel empty">
            {filtered ? <p>No clips match these filters.</p> : <p>No clips yet. Upload the first one.</p>}
          </div>
        ) : (
          <ul className="clip-grid">
            {cards.map((c) => {
              const status = c.status as ClipStatus;
              const canDelete = c.owner_id === member.id || member.role === "admin";
              const fps = c.fps === null ? null : Number(c.fps);
              return (
                <li key={c.id} className="clip-card">
                  <div className="thumb">
                    {/* eslint-disable-next-line @next/next/no-img-element -- short-lived signed R2 URL, not worth optimising */}
                    {c.thumbUrl ? <img src={c.thumbUrl} alt="" loading="lazy" /> : <span className="muted small">No preview</span>}
                    {c.duration_s ? <span className="duration mono">{formatDuration(Number(c.duration_s))}</span> : null}
                  </div>
                  <div className="clip-body">
                    <div className="row between">
                      <h2 className="clip-title">
                        {c.playerName ?? "Untitled clip"}
                        {c.opponentName ? <span className="muted"> vs {c.opponentName}</span> : null}
                      </h2>
                      {canDelete ? <DeleteClipButton clipId={c.id} label={c.playerName ?? "this clip"} /> : null}
                    </div>
                    <div className="muted small">
                      {[c.tournament, c.round, c.match_date && new Date(c.match_date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }), c.discipline].filter(Boolean).join(" · ") || "No match details"}
                    </div>
                    <dl className="stats mono small">
                      <div>
                        <dt>Rallies</dt>
                        <dd>–</dd>
                      </div>
                      <div>
                        <dt>Steps</dt>
                        <dd>–</dd>
                      </div>
                      <div>
                        <dt>FPS</dt>
                        <dd>{fps === null ? "–" : Math.round(fps)}</dd>
                      </div>
                    </dl>
                    <div className="row">
                      <span className={`pill ${status === "ready" ? "good" : status === "failed" || status === "uploading" ? "bad" : ""}`}>{STATUS_LABELS[status] ?? status}</span>
                      {fps !== null && fps < LOW_FPS ? <span className="pill warn">Lower timing accuracy</span> : null}
                    </div>
                    {status === "failed" && c.error ? <p className="small bad-text">{c.error}</p> : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </main>
    </>
  );
}
