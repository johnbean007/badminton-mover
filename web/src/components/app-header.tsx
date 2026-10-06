import Link from "next/link";

import { signOut } from "@/app/auth/actions";
import type { Member } from "@/lib/auth";

export function AppHeader({ member }: { member: Member }) {
  return (
    <header className="topbar">
      <div className="topbar-in">
        <Link href="/" className="brand">
          Badminton Mover
        </Link>
        <nav className="nav" aria-label="Main">
          <Link href="/">Clips</Link>
          <Link href="/clips/new">Upload</Link>
          {member.role === "admin" ? <Link href="/admin/members">Members</Link> : null}
        </nav>
        <div className="spacer" />
        <span className="muted small">{member.email}</span>
        <form action={signOut}>
          <button type="submit" className="btn ghost small">
            Sign out
          </button>
        </form>
      </div>
    </header>
  );
}
