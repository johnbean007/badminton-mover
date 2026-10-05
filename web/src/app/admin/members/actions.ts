"use server";

import { revalidatePath } from "next/cache";
import { headers } from "next/headers";

import type { FormState } from "@/app/auth/actions";
import { requireAdmin } from "@/lib/auth";
import { createAdminClient } from "@/lib/supabase/admin";

export async function inviteMember(_prev: FormState, formData: FormData): Promise<FormState> {
  await requireAdmin();
  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return { ok: false, message: "Enter a valid email address." };

  const origin = (await headers()).get("origin") ?? process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
  // The default invite email signs people in through a URL fragment the server can't read, so the
  // invite lands on the sign-in page, where they ask for a normal sign-in link.
  const { error } = await createAdminClient().auth.admin.inviteUserByEmail(email, { redirectTo: `${origin}/login?invited=1` });
  if (error) {
    const already = /already/i.test(error.message);
    return { ok: false, message: already ? `${email} is already a member.` : `Couldn't send the invite: ${error.message}` };
  }
  revalidatePath("/admin/members");
  return { ok: true, message: `Invite sent to ${email}.` };
}

export async function removeMember(formData: FormData) {
  const admin = await requireAdmin();
  const id = String(formData.get("id") ?? "");
  if (!id || id === admin.id) return; // the admin can't remove themselves here
  // Deleting the auth user cascades to their profile, so requireMember() rejects them on their next request.
  await createAdminClient().auth.admin.deleteUser(id);
  revalidatePath("/admin/members");
}
