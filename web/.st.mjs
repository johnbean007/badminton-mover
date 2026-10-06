import { createClient } from "@supabase/supabase-js";
const db = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL, process.env.SUPABASE_SECRET_KEY, { auth: { persistSession: false } });
const id = "5d6d4ffd-38ac-4877-82be-cea98cb9a9fa";
const { data: c } = await db.from("clips").select("status,error,shirt_colour").eq("id", id).single();
console.log("clip", JSON.stringify(c));
const { data: r } = await db.from("rallies").select("index,status,near_side,included,calibration_id").eq("clip_id", id).order("index");
console.log(r.map((x) => `${x.index}:${x.status}/${x.near_side ?? "-"}${x.included ? "" : "(off)"}${x.calibration_id ? "" : "(nocal)"}`).join(" "));
const { data: j } = await db.from("jobs").select("type,status,progress,error,created_at,finished_at").eq("clip_id", id).order("created_at");
for (const x of j) console.log(x.type, x.status, x.progress, x.error ?? "", x.created_at.slice(11, 19), x.finished_at?.slice(11, 19) ?? "");
