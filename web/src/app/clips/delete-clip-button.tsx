"use client";

import { useRef, useState, useTransition } from "react";

import { deleteClip } from "./actions";

export function DeleteClipButton({ clipId, label }: { clipId: string; label: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  return (
    <>
      <button type="button" className="btn ghost small danger" onClick={() => dialog.current?.showModal()}>
        Delete
      </button>
      <dialog ref={dialog} className="dialog" onClose={() => setMessage(null)}>
        <h2>Delete {label}?</h2>
        <p className="muted">The video and everything worked out from it are deleted for everyone. Corrections people made are kept as training data.</p>
        {message ? <p className="notice bad">{message}</p> : null}
        <div className="row end">
          <button type="button" className="btn" onClick={() => dialog.current?.close()} disabled={pending}>
            Keep it
          </button>
          <button
            type="button"
            className="btn primary danger-fill"
            disabled={pending}
            onClick={() =>
              startTransition(async () => {
                const res = await deleteClip(clipId);
                if (res.ok) dialog.current?.close();
                else setMessage(res.message);
              })
            }
          >
            {pending ? "Deleting…" : "Delete clip"}
          </button>
        </div>
      </dialog>
    </>
  );
}
