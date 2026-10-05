import "server-only";

import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

export type Member = { id: string; email: string; displayName: string | null; role: "admin" | "member" };

// The signed-in member, or a redirect to /login. Checks the profile row on every call, so a member
// the admin removes loses access on their next request rather than when their session expires.
export async function requireMember(): Promise<Member> {
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  if (!userId) redirect("/login");

  const { data: profile } = await supabase
    .from("profiles")
    .select("id, email, display_name, role")
    .eq("id", userId)
    .maybeSingle();

  if (!profile) {
    await supabase.auth.signOut();
    redirect("/login?removed=1");
  }

  return { id: profile.id, email: profile.email, displayName: profile.display_name, role: profile.role };
}

export async function requireAdmin(): Promise<Member> {
  const member = await requireMember();
  if (member.role !== "admin") redirect("/");
  return member;
}
