"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

export type FormState = { ok: boolean; message: string } | null;

async function siteOrigin() {
  const h = await headers();
  return h.get("origin") ?? process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";
}

export async function sendMagicLink(_prev: FormState, formData: FormData): Promise<FormState> {
  const email = String(formData.get("email") ?? "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return { ok: false, message: "Enter the email address your invite was sent to." };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { shouldCreateUser: false, emailRedirectTo: `${await siteOrigin()}/auth/confirm` },
  });

  if (error) {
    console.error("signInWithOtp failed", { code: error.code, status: error.status, message: error.message });
    if (error.status === 429 || /rate limit|security purposes/i.test(error.message)) {
      return { ok: false, message: "Too many sign-in emails just now. Wait a minute, then try again." };
    }
    // With sign-ups turned off, an email that isn't a member is refused.
    if (error.code === "otp_disabled" || /signups not allowed/i.test(error.message)) {
      return { ok: false, message: "That email isn't on the members list. Ask John for an invite." };
    }
    return { ok: false, message: "Couldn't send a sign-in link. Try again in a minute." };
  }
  return { ok: true, message: `Check ${email} for a sign-in link. It works once and expires in an hour.` };
}

export async function signOut() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}
