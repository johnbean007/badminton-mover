import { AppHeader } from "@/components/app-header";
import { requireMember } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

import { UploadForm } from "./upload-form";

export default async function NewClipPage() {
  const member = await requireMember();
  const supabase = await createClient();
  const [{ data: players }, { data: tournaments }] = await Promise.all([
    supabase.from("players").select("name, handedness").order("name"),
    supabase.from("clips").select("tournament").not("tournament", "is", null).order("tournament"),
  ]);

  return (
    <>
      <AppHeader member={member} />
      <main className="page narrow">
        <div className="eyebrow">Shared library</div>
        <h1>Upload a clip</h1>
        <UploadForm
          players={(players ?? []).map((p) => ({ name: p.name, handedness: p.handedness }))}
          tournaments={[...new Set((tournaments ?? []).map((t) => t.tournament as string))]}
        />
      </main>
    </>
  );
}
