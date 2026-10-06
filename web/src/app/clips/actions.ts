"use server";

import { revalidatePath } from "next/cache";

import { requireMember } from "@/lib/auth";
import { clipKeys, DISCIPLINES, MAX_BYTES, MAX_SECONDS, ROUNDS, SINGLES, VIDEO_TYPES } from "@/lib/clips";
import { deleteObjects, objectSize, signUpload } from "@/lib/r2";
import { createAdminClient } from "@/lib/supabase/admin";
import { createClient } from "@/lib/supabase/server";
import { queueJob } from "@/lib/worker";

type Result<T = object> = ({ ok: true } & T) | { ok: false, message: string };

export type StartUploadInput = { type: string; size: number; duration: number; width: number; height: number; thumbSize: number | null };

// Creates the clip in the "uploading" state and hands the browser signed links to put the video
// (and its thumbnail) straight into R2. The details form is filled in while the upload runs.
export async function startUpload(input: StartUploadInput): Promise<Result<{ clipId: string; uploadUrl: string; thumbUrl: string | null }>> {
  await requireMember();
  const ext = VIDEO_TYPES[input.type];
  if (!ext) return { ok: false, message: "Only MP4 and MOV videos can be uploaded." };
  if (!(input.size > 0) || input.size > MAX_BYTES) return { ok: false, message: "Videos must be 500 MB or smaller." };
  if (!(input.duration > 0) || input.duration > MAX_SECONDS) return { ok: false, message: "Videos must be 10 minutes or shorter." };

  const clipId = crypto.randomUUID();
  const keys = clipKeys(clipId, ext);
  const hasThumb = input.thumbSize !== null && input.thumbSize > 0 && input.thumbSize < 2_000_000;

  const supabase = await createClient();
  const { error } = await supabase.from("clips").insert({
    id: clipId,
    original_key: keys.original,
    thumb_key: hasThumb ? keys.thumb : null,
    size_bytes: input.size,
    duration_s: Math.round(input.duration * 1000) / 1000,
    width: Math.round(input.width) || null,
    height: Math.round(input.height) || null,
  });
  if (error) {
    console.error("startUpload insert failed", error);
    return { ok: false, message: "Couldn't start the upload. Try again." };
  }

  const uploadUrl = await signUpload(keys.original, input.type, input.size);
  const thumbUrl = hasThumb ? await signUpload(keys.thumb, "image/jpeg", input.thumbSize!) : null;
  return { ok: true, clipId, uploadUrl, thumbUrl };
}

const normaliseName = (s: string) => s.trim().replace(/\s+/g, " ");

// Finds a player by name (ignoring case and spacing) or adds them. Fills in handedness if the
// record doesn't have one yet; a known handedness only changes through the admin.
async function resolvePlayer(supabase: Awaited<ReturnType<typeof createClient>>, rawName: string, handedness: string | null) {
  const name = normaliseName(rawName);
  const key = name.toLowerCase();
  const find = () => supabase.from("players").select("id, handedness").eq("name_key", key).maybeSingle();

  let { data: player } = await find();
  if (!player) {
    const { data, error } = await supabase.from("players").insert({ name, handedness }).select("id, handedness").single();
    // Someone else added the same player a moment ago.
    if (error?.code === "23505") player = (await find()).data;
    else if (error) throw error;
    else player = data;
  }
  if (!player) throw new Error(`Couldn't find or add player ${name}`);
  if (!player.handedness && handedness) await supabase.from("players").update({ handedness }).eq("id", player.id);
  return player.id as string;
}

// Saves the details once the video is in R2, after checking the whole file arrived.
export async function finishUpload(clipId: string, formData: FormData): Promise<Result> {
  const member = await requireMember();
  const field = (name: string) => String(formData.get(name) ?? "").trim();

  const playerName = field("player");
  const handedness = field("handedness") || null;
  const opponentName = field("opponent");
  const discipline = field("discipline");
  const tournament = field("tournament");
  const round = field("round");
  const matchDate = field("match_date");
  const sourceUrl = field("source_url");
  const notes = field("notes");

  if (!playerName) return { ok: false, message: "Choose the player whose footwork this clip tracks." };
  if (handedness && !["L", "R"].includes(handedness)) return { ok: false, message: "Choose left- or right-handed." };
  if (!SINGLES.includes(discipline)) {
    const known = DISCIPLINES.some((d) => d.value === discipline);
    return { ok: false, message: known ? "Doubles is coming later. Choose men's or women's singles." : "Choose a discipline." };
  }
  if (round && !(ROUNDS as readonly string[]).includes(round)) return { ok: false, message: "Choose a round from the list." };
  if (matchDate && !/^\d{4}-\d{2}-\d{2}$/.test(matchDate)) return { ok: false, message: "Enter the match date as a date." };
  if (sourceUrl && !/^https?:\/\/\S+$/i.test(sourceUrl)) return { ok: false, message: "The source link must start with http:// or https://." };
  if (tournament.length > 120 || notes.length > 2000 || sourceUrl.length > 500) return { ok: false, message: "One of the fields is too long." };

  const supabase = await createClient();
  const { data: clip } = await supabase
    .from("clips")
    .select("id, owner_id, status, original_key, size_bytes")
    .eq("id", clipId)
    .maybeSingle();
  if (!clip || clip.owner_id !== member.id) return { ok: false, message: "This upload no longer exists. Start again." };
  if (clip.status !== "uploading") return { ok: true };

  const stored = await objectSize(clip.original_key);
  if (stored !== Number(clip.size_bytes)) return { ok: false, message: "The video didn't finish uploading. Try uploading it again." };

  const { data: player } = await supabase.from("players").select("handedness").eq("name_key", normaliseName(playerName).toLowerCase()).maybeSingle();
  if (!player?.handedness && !handedness) return { ok: false, message: `Choose whether ${normaliseName(playerName)} is left- or right-handed.` };

  try {
    const playerId = await resolvePlayer(supabase, playerName, handedness);
    const opponentId = opponentName ? await resolvePlayer(supabase, opponentName, null) : null;
    if (opponentId === playerId) return { ok: false, message: "The opponent can't be the same player." };

    const { error } = await supabase
      .from("clips")
      .update({
        player_id: playerId,
        opponent_id: opponentId,
        discipline,
        tournament: tournament || null,
        round: round || null,
        match_date: matchDate || null,
        source_url: sourceUrl || null,
        notes: notes || null,
      })
      .eq("id", clipId);
    if (error) throw error;

    // Status is server-owned, so it moves on with the secret key now the file is checked.
    const { error: statusError } = await createAdminClient()
      .from("clips")
      .update({ status: "uploaded", uploaded_at: new Date().toISOString() })
      .eq("id", clipId)
      .eq("status", "uploading");
    if (statusError) throw statusError;
  } catch (e) {
    console.error("finishUpload failed", e);
    return { ok: false, message: "Couldn't save the clip details. Try again." };
  }

  try {
    await queueJob(clipId, "prescan", member.id);
  } catch (e) {
    // The clip is saved; the admin can start the pre-scan with Retry.
    console.error("queueing prescan failed", e);
  }
  revalidatePath("/");
  return { ok: true };
}

// Starts the pre-scan again for a clip that failed (or never started).
export async function retryPrescan(clipId: string): Promise<Result> {
  const member = await requireMember();
  const supabase = await createClient();
  const { data: clip } = await supabase.from("clips").select("id, owner_id, status").eq("id", clipId).maybeSingle();
  if (!clip) return { ok: false, message: "That clip has gone." };
  if (clip.owner_id !== member.id && member.role !== "admin") return { ok: false, message: "Only the uploader or the admin can retry this clip." };
  if (!["failed", "uploaded"].includes(clip.status)) return { ok: false, message: "This clip is already being processed." };

  await createAdminClient().from("clips").update({ status: "uploaded", error: null }).eq("id", clipId);
  await queueJob(clipId, "prescan", member.id);
  revalidatePath("/");
  return { ok: true };
}

// Deletes the clip's files from R2, then the clip. Only its uploader or the admin may.
// training/ snippets are kept.
export async function deleteClip(clipId: string): Promise<Result> {
  const member = await requireMember();
  const supabase = await createClient();
  const { data: clip } = await supabase.from("clips").select("id, owner_id").eq("id", clipId).maybeSingle();
  if (!clip) return { ok: false, message: "That clip has already gone." };
  if (clip.owner_id !== member.id && member.role !== "admin") return { ok: false, message: "Only the uploader or the admin can delete this clip." };

  // Rally files are keyed by rally id; the rows go with the clip (on delete cascade).
  const { data: rallies } = await supabase.from("rallies").select("id").eq("clip_id", clipId);
  const rallyKeys = (rallies ?? []).flatMap((r) => [`thumbs/${r.id}.jpg`, `pose/${r.id}.npz`, `shuttle/${r.id}.npz`]);

  try {
    const mp4 = clipKeys(clipId, "mp4");
    await deleteObjects([mp4.original, clipKeys(clipId, "mov").original, mp4.playback, mp4.thumb, ...rallyKeys]);
  } catch (e) {
    console.error("deleteClip R2 failed", e);
    return { ok: false, message: "Couldn't delete the video files. Try again." };
  }

  const { error } = await supabase.from("clips").delete().eq("id", clipId);
  if (error) {
    console.error("deleteClip row failed", error);
    return { ok: false, message: "Couldn't delete the clip. Try again." };
  }
  revalidatePath("/");
  return { ok: true };
}
