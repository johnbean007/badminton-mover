import "server-only";

import { after } from "next/server";

import { createAdminClient } from "@/lib/supabase/admin";

export type JobType = "prescan" | "sidecheck" | "analyse" | "reanalyse";

// Records a job, then nudges the Modal worker to start it once the response has gone back to the
// browser (waking the worker can take several seconds). If the nudge fails, the worker's
// 10-minute sweep starts the job anyway.
export async function queueJob(clipId: string, type: JobType, createdBy: string) {
  const { data, error } = await createAdminClient()
    .from("jobs")
    .insert({ clip_id: clipId, type, created_by: createdBy })
    .select("id")
    .single();
  if (error) throw error;

  after(async () => {
    try {
      const res = await fetch(`${process.env.WORKER_URL}/jobs/${data.id}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${process.env.WORKER_TOKEN}` },
        signal: AbortSignal.timeout(20_000),
      });
      if (!res.ok) console.error("worker trigger refused", res.status);
    } catch (e) {
      console.error("worker trigger failed; the sweep will start the job", e);
    }
  });
  return data.id as string;
}
