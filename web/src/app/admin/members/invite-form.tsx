"use client";

import { useActionState } from "react";

import type { FormState } from "@/app/auth/actions";

import { inviteMember } from "./actions";

export function InviteForm() {
  const [state, action, pending] = useActionState<FormState, FormData>(inviteMember, null);

  return (
    <form action={action} className="row-form">
      <label htmlFor="invite-email" className="label">
        Invite by email
      </label>
      <div className="row">
        <input id="invite-email" name="email" type="email" required className="input" placeholder="clubmate@example.com" />
        <button type="submit" className="btn primary" disabled={pending}>
          {pending ? "Sending…" : "Send invite"}
        </button>
      </div>
      {state ? (
        <p role="status" className={`notice ${state.ok ? "good" : "bad"}`}>
          {state.message}
        </p>
      ) : null}
    </form>
  );
}
