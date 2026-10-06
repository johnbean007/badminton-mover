import Link from "next/link";
import { notFound } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { requireMember } from "@/lib/auth";
import { type ClipStatus, STATUS_LABELS } from "@/lib/clips";
import type { Pt } from "@/lib/court";
import { signDownload } from "@/lib/r2";
import { createClient } from "@/lib/supabase/server";

import type { Segment } from "./actions";
import { ReviewWorkspace } from "./review-workspace";

export default async function ReviewPage({ params }: PageProps<"/clips/[id]/review">) {
  const { id } = await params;
  const member = await requireMember();
  const supabase = await createClient();
  const { data: clip } = await supabase
    .from("clips")
    .select("id, owner_id, status, error, fps, width, height, duration_s, playback_key, shirt_colour, player:players!clips_player_id_fkey(name, handedness)")
    .eq("id", id)
    .maybeSingle();
  if (!clip) notFound();

  const player = Array.isArray(clip.player) ? clip.player[0] : clip.player;
  const canEdit = clip.owner_id === member.id || member.role === "admin";
  const status = clip.status as ClipStatus;

  const header = (
    <div>
      <div className="eyebrow">
        <Link href="/">Library</Link> / Review
      </div>
      <h1>{player?.name ?? "Clip"}: review rallies</h1>
    </div>
  );

  if (status !== "review" || !clip.playback_key || !clip.fps) {
    return (
      <>
        <AppHeader member={member} />
        <main className="page">
          {header}
          <div className="panel empty">
            <p>{status === "failed" ? (clip.error ?? "Finding the rallies failed.") : `This clip is ${STATUS_LABELS[status]?.toLowerCase() ?? status}.`}</p>
          </div>
        </main>
      </>
    );
  }

  const [{ data: rallies }, { data: calibrations }] = await Promise.all([
    supabase.from("rallies").select("id, start_frame, end_frame, included, thumb_key, calibration_id, near_side").eq("clip_id", id).order("start_frame"),
    supabase.from("calibrations").select("id, corners, frame").eq("clip_id", id).order("created_at"),
  ]);

  const thumbKeys = [...new Set((rallies ?? []).map((r) => r.thumb_key).filter(Boolean) as string[])];
  const thumbUrls = Object.fromEntries(await Promise.all(thumbKeys.map(async (k) => [k, await signDownload(k, 6 * 3600)] as const)));
  const segments: Segment[] = (rallies ?? []).map((r) => ({
    id: r.id,
    start: r.start_frame,
    end: r.end_frame,
    included: r.included,
    thumbKey: r.thumb_key,
    calibrationId: r.calibration_id,
  }));
  const nearSide = Object.fromEntries((rallies ?? []).map((r) => [r.id, r.near_side as "near" | "far" | "unclear" | null]));

  return (
    <>
      <AppHeader member={member} />
      <main className="page wide">
        {header}
        <ReviewWorkspace
          clipId={clip.id}
          canEdit={canEdit}
          fps={Number(clip.fps)}
          totalFrames={Math.floor(Number(clip.duration_s) * Number(clip.fps))}
          videoUrl={await signDownload(clip.playback_key, 6 * 3600)}
          initialSegments={segments}
          nearSide={nearSide}
          thumbUrls={thumbUrls}
          calibrations={(calibrations ?? []).map((c) => ({ id: c.id, corners: c.corners as Pt[], frame: c.frame ?? 0 }))}
          initialShirt={clip.shirt_colour}
        />
      </main>
    </>
  );
}
