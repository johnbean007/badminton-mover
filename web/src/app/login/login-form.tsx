"use client";

import { useActionState } from "react";

import { sendMagicLink, type FormState } from "@/app/auth/actions";

export function LoginForm() {
  const [state, action, pending] = useActionState<FormState, FormData>(sendMagicLink, null);

  return (
    <form action={action} className="stack">
      <label htmlFor="email" className="label">
        Email
      </label>
      <input id="email" name="email" type="email" autoComplete="email" required className="input" placeholder="you@example.com" />
      <button type="submit" className="btn primary" disabled={pending}>
        {pending ? "Sending…" : "Email me a sign-in link"}
      </button>
      {state ? (
        <p role="status" className={`notice ${state.ok ? "good" : "bad"}`}>
          {state.message}
        </p>
      ) : null}
    </form>
  );
}
