"use server";

import { revalidatePath } from "next/cache";

import { requireMember } from "@/lib/auth";
import { checkCorners, frameToCourt, type Pt } from "@/lib/court";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";
import { queueJob } from "@/lib/worker";

type Result<T = object> = ({ ok: true } & T) | { ok: false; message: string };
export type Segment = { id: string | null; start: number; end: number; included: boolean; thumbKey: string | null; calibrationId: string | null };

// Loads the clip if the member may edit it and it's in review.
async function editableClip(clipId: string) {
  const member = await requireMember();
  const supabase = await createClient();
  const { data: clip } = await supabase
    .from("clips")
    .select("id, owner_id, status, fps, duration_s, shirt_colour")
    .eq("id", clipId)
    .maybeSingle();
  if (!clip) return { error: "This clip has gone." } as const;
  if (clip.owner_id !== member.id && member.role !== "admin") return { error: "Only the uploader or the admin can review this clip." } as const;
  if (clip.status !== "review") return { error: "This clip isn't waiting for review any more." } as const;
  return { member, supabase, clip } as const;
}

// Replaces the clip's segments with the edited list: updates rows that kept their id, adds new ones
// (from splits and "new segment"), and removes the rest. Returns the list with every id filled in.
export async function saveSegments(clipId: string, segments: Segment[]): Promise<Result<{ segments: Segment[] }>> {
  const ctx = await editableClip(clipId);
  if ("error" in ctx) return { ok: false, message: ctx.error! };
  const { supabase, clip } = ctx;

  const lastFrame = Math.ceil(Number(clip.duration_s) * Number(clip.fps));
  const sorted = [...segments].sort((a, b) => a.start - b.start);
  for (let i = 0; i < sorted.length; i++) {
    const s = sorted[i];
    if (!Number.isInteger(s.start) || !Number.isInteger(s.end) || s.start < 0 || s.end <= s.start || s.end > lastFrame + 1) {
      return { ok: false, message: "A segment has an impossible start or end." };
    }
    if (i > 0 && s.start <= sorted[i - 1].end) return { ok: false, message: "Segments can't overlap." };
  }

  const { data: existing } = await supabase.from("rallies").select("id").eq("clip_id", clipId);
  const keep = new Set(sorted.filter((s) => s.id).map((s) => s.id));
  const gone = (existing ?? []).map((r) => r.id).filter((id) => !keep.has(id));
  if (gone.length) {
    const { error } = await supabase.from("rallies").delete().in("id", gone);
    if (error) return { ok: false, message: "Couldn't save the segments. Try again." };
  }

  const saved: Segment[] = [];
  for (const [index, s] of sorted.entries()) {
    const values = { index, start_frame: s.start, end_frame: s.end, included: s.included, calibration_id: s.calibrationId };
    if (s.id) {
      const { error } = await supabase.from("rallies").update(values).eq("id", s.id).eq("clip_id", clipId);
      if (error) return { ok: false, message: "Couldn't save the segments. Try again." };
      saved.push(s);
    } else {
      const { data, error } = await supabase
        .from("rallies")
        .insert({ ...values, clip_id: clipId, thumb_key: s.thumbKey })
        .select("id")
        .single();
      if (error) return { ok: false, message: "Couldn't save the segments. Try again." };
      saved.push({ ...s, id: data.id });
    }
  }
  return { ok: true, segments: saved };
}

// Saves the four clicked corners and points the segments at them: every segment, or just one when
// the camera moved for that segment.
export async function saveCalibration(clipId: string, corners: Pt[], frame: number, onlySegmentId: string | null): Promise<Result<{ calibrationId: string }>> {
  const ctx = await editableClip(clipId);
  if ("error" in ctx) return { ok: false, message: ctx.error! };
  const problem = checkCorners(corners);
  if (problem) return { ok: false, message: problem };
  const H = frameToCourt(corners);
  if (!H) return { ok: false, message: "Those corners don't make a court. Try again." };

  const { supabase } = ctx;
  const { data: cal, error } = await supabase
    .from("calibrations")
    .insert({ clip_id: clipId, corners, homography: H, frame: Math.max(0, Math.round(frame)) })
    .select("id")
    .single();
  if (error) {
    console.error("saveCalibration failed", error);
    return { ok: false, message: "Couldn't save the calibration. Try again." };
  }
  let q = supabase.from("rallies").update({ calibration_id: cal.id }).eq("clip_id", clipId);
  if (onlySegmentId) q = q.eq("id", onlySegmentId);
  const { error: linkError } = await q;
  if (linkError) return { ok: false, message: "Saved the calibration but couldn't apply it. Try again." };
  return { ok: true, calibrationId: cal.id };
}

export async function saveShirtColour(clipId: string, hex: string): Promise<Result> {
  const ctx = await editableClip(clipId);
  if ("error" in ctx) return { ok: false, message: ctx.error! };
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return { ok: false, message: "That isn't a colour." };
  const { error } = await ctx.supabase.from("clips").update({ shirt_colour: hex.toLowerCase() }).eq("id", clipId);
  if (error) return { ok: false, message: "Couldn't save the shirt colour. Try again." };
  return { ok: true };
}

// Sends the reviewed segments for pose and step analysis (milestone 4 does the work).
export async function startAnalysis(clipId: string): Promise<Result> {
  const ctx = await editableClip(clipId);
  if ("error" in ctx) return { ok: false, message: ctx.error! };
  const { supabase, clip, member } = ctx;
  if (!clip.shirt_colour) return { ok: false, message: "Pick the player's shirt colour first." };

  const { data: rallies } = await supabase.from("rallies").select("id, included, calibration_id, near_side").eq("clip_id", clipId);
  const chosen = (rallies ?? []).filter((r) => r.included && r.near_side !== "far");
  if (chosen.length === 0) return { ok: false, message: "Keep at least one segment with the player on the near side." };
  if (chosen.some((r) => !r.calibration_id)) return { ok: false, message: "Calibrate the court first." };

  const admin = createAdminClient();
  const { error } = await admin.from("rallies").update({ status: "queued" }).in("id", chosen.map((r) => r.id));
  if (error) return { ok: false, message: "Couldn't start the analysis. Try again." };
  await admin.from("clips").update({ status: "queued" }).eq("id", clipId);
  await queueJob(clipId, "analyse", member.id);
  revalidatePath("/");
  return { ok: true };
}
