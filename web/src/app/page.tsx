import { AppHeader } from "@/components/app-header";
import { requireMember } from "@/lib/auth";

export default async function HomePage() {
  const member = await requireMember();

  return (
    <>
      <AppHeader member={member} />
      <main className="page">
        <div className="eyebrow">Shared library</div>
        <h1>Pro clips</h1>
        <div className="panel empty">
          <p>No clips yet. Uploading arrives in milestone 2.</p>
        </div>
      </main>
    </>
  );
}
