import Link from "next/link";

export default function AuthErrorPage() {
  return (
    <main className="auth-shell">
      <div className="auth-card">
        <h1>That link didn&apos;t work</h1>
        <p className="muted">
          Sign-in links work once and expire after an hour. Request a new one and use the latest email.
        </p>
        <Link className="btn primary" href="/login">
          Get a new link
        </Link>
      </div>
    </main>
  );
}
