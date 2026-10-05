import { LoginForm } from "./login-form";

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const { removed, invited } = await searchParams;

  return (
    <main className="auth-shell">
      <div className="auth-card">
        <div className="eyebrow">Members only</div>
        <h1>Badminton Mover</h1>
        <p className="muted">Footwork and shuttle tracking from pro match clips. Sign in with the email your invite went to.</p>
        {invited ? <p className="notice good">Your invite is accepted. Enter your email below to get a sign-in link.</p> : null}
        {removed ?<p className="notice warn">Your membership has ended. Ask John if you think that&apos;s a mistake.</p> : null}
        <LoginForm />
      </div>
    </main>
  );
}
