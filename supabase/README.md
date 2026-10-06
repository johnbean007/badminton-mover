# Supabase setup

One-off steps in the Supabase dashboard. Do these yourself; never paste keys into chat or commit them.

1. **Create the project.** Name `badminton-mover`, region West EU (London). Save the database password in your password manager.
2. **Run the migration.** SQL Editor → New query → paste `migrations/20261005000000_profiles.sql` → Run.
3. **Turn off open sign-ups.** Authentication → Sign In / Providers → turn off "Allow new users to sign up". Email provider stays on.
4. **URLs.** Authentication → URL Configuration:
   - Site URL: `http://localhost:3000` (change to the Vercel URL after deploying)
   - Redirect URLs: add `http://localhost:3000/auth/confirm` (and later `https://<vercel-url>/auth/confirm`)
5. **Email templates.** Leave the defaults. Custom templates need a custom SMTP sender; `/auth/confirm` handles the default sign-in links.
6. **Keys.** Copy `web/.env.example` to `web/.env.local` and fill in the URL, publishable key and secret key from Project Settings → API Keys.
7. **Make yourself admin.** Authentication → Users → Invite user → your email. Then in the SQL Editor:
   ```sql
   update public.profiles set role = 'admin' where email = 'john.s.bean@gmail.com';
   ```

Supabase's built-in email sender is rate-limited (a few emails an hour). That's fine for a small club; add a custom SMTP sender later if invites start failing.

## Later migrations

Run each new file in `migrations/` once, in date order: SQL Editor → New query → paste → Run.

- `20261006000000_players_clips.sql` (milestone 2): players and clips. Applied 2026-10-06.
