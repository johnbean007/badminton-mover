# Badminton Mover web app

Next.js 16 (App Router) on Vercel, with Supabase for sign-in and data.

```bash
cp .env.example .env.local   # then fill in the Supabase keys (see ../supabase/README.md)
npm install
npm run dev                  # http://localhost:3000
```

- Sign-in is by emailed link, invite-only. The admin invites and removes members at `/admin/members`.
- `src/proxy.ts` refreshes the session on every request and sends signed-out visitors to `/login`.
- `src/lib/auth.ts` (`requireMember`, `requireAdmin`) is the real access check on each page.
