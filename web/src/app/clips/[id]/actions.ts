"use server";

import { revalidatePath } from "next/cache";

import { requireMember } from "@/lib/auth";
import { checkCorners, frameToCourt, type NearPoints, outerCornersFromClicks, type Pt } from "@/lib/court";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";
import { queueJob } from "@/lib/worker";

type Result = { ok: true } | { ok: false; message: string };

// Gives one analysed rally a new court calibration (the camera zoomed or moved for it), then re-runs
// the stages that use the court: court fit, contacts and zones. Pose isn't tracked again.
// clicks: near-left, near-right, far-right, far-left; the near pair is on the baseline or, when the
// camera cuts the baseline off, on the doubles long service line.
export async function recalibrateRally(clipId: string, rallyId: string, clicks: Pt[], near: NearPoints, frame: number): Promise<Result> {
  const member = await requireMember();
  const supabase = await createClient();
  const { data: clip } = await supabase.from("clips").select("id, owner_id, status").eq("id", clipId).maybeSingle();
  if (!clip) return { ok: false, message: "This clip has gone." };
  if (clip.owner_id !== member.id && member.role !== "admin") return { ok: false, message: "Only the uploader or the admin can recalibrate this clip." };
  if (clip.status !== "ready") return { ok: false, message: "Wait for the analysis to finish first." };
  const { data: rally } = await supabase.from("rallies").select("id, status").eq("id", rallyId).eq("clip_id", clipId).maybeSingle();
  if (!rally || rally.status !== "ready") return { ok: false, message: "That rally isn't analysed." };

  if (near !== "baseline" && near !== "service") return { ok: false, message: "Choose which near points you clicked." };
  const problem = checkCorners(clicks);
  if (problem) return { ok: false, message: problem };
  const corners = outerCornersFromClicks(clicks, near);
  const H = corners && frameToCourt(corners);
  if (!corners || !H || corners.some((p) => p.some((v) => !Number.isFinite(v) || Math.abs(v) > 5))) {
    return { ok: false, message: "Those points don't make a court. Try again." };
  }

  const { data: cal, error } = await supabase
    .from("calibrations")
    .insert({ clip_id: clipId, corners, homography: H, frame: Math.max(0, Math.round(frame)) })
    .select("id")
    .single();
  if (error) {
    console.error("recalibrateRally insert failed", error);
    return { ok: false, message: "Couldn't save the calibration. Try again." };
  }
  const { error: linkError } = await supabase.from("rallies").update({ calibration_id: cal.id }).eq("id", rallyId);
  if (linkError) return { ok: false, message: "Saved the calibration but couldn't apply it. Try again." };
  // The old score no longer applies; the worker sets a new one.
  await createAdminClient().from("rallies").update({ court_fit: null }).eq("id", rallyId);
  await queueJob(clipId, "reanalyse", member.id);
  revalidatePath(`/clips/${clipId}`);
  return { ok: true };
}
