import { AppHeader } from "@/components/app-header";
import { requireAdmin } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

import { removeMember } from "./actions";
import { InviteForm } from "./invite-form";

export default async function MembersPage() {
  const admin = await requireAdmin();
  const supabase = await createClient();
  const { data: members } = await supabase
    .from("profiles")
    .select("id, email, role, created_at")
    .order("created_at", { ascending: true });

  return (
    <>
      <AppHeader member={admin} />
      <main className="page">
        <div className="eyebrow">Admin</div>
        <h1>Members</h1>
        <div className="panel pad">
          <InviteForm />
        </div>
        <div className="panel">
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Joined</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {(members ?? []).map((m) => (
                <tr key={m.id}>
                  <td>{m.email}</td>
                  <td>{m.role === "admin" ? "Admin" : "Member"}</td>
                  <td className="mono">{new Date(m.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</td>
                  <td className="right">
                    {m.id !== admin.id ? (
                      <form action={removeMember}>
                        <input type="hidden" name="id" value={m.id} />
                        <button type="submit" className="btn danger small">
                          Remove
                        </button>
                      </form>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </>
  );
}
