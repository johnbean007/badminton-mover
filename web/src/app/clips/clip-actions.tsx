"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";

import { retryClip } from "./actions";

// Review or Watch link, or Retry button, on a library card.
export function ClipNextStep({ clipId, status, canEdit }: { clipId: string; status: string; canEdit: boolean }) {
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);
  if (status === "ready") {
    return (
      <Link href={`/clips/${clipId}`} className="btn small primary">
        Watch
      </Link>
    );
  }
  if (status === "review") {
    return (
      <Link href={`/clips/${clipId}/review`} className="btn small primary">
        {canEdit ? "Review rallies" : "View segments"}
      </Link>
    );
  }
  if (status === "failed" && canEdit) {
    return (
      <>
        <button
          type="button"
          className="btn small"
          disabled={pending}
          onClick={() =>
            startTransition(async () => {
              const res = await retryClip(clipId);
              setMessage(res.ok ? null : res.message);
            })
          }
        >
          {pending ? "Retrying…" : "Retry"}
        </button>
        {message ? <span className="small bad-text">{message}</span> : null}
      </>
    );
  }
  return null;
}

// Refreshes the library every few seconds while any clip is being processed.
export function AutoRefresh({ active }: { active: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => router.refresh(), 5000);
    return () => clearInterval(t);
  }, [active, router]);
  return null;
}
