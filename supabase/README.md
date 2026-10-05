# Supabase setup (milestone 1)

One-off steps in the Supabase dashboard. Do these yourself; never paste keys into chat or commit them.

1. **Create the project.** Name `badminton-mover`, region West EU (London). Save the database password in your password manager.
2. **Run the migration.** SQL Editor → New query → paste `migrations/20261005000000_profiles.sql` → Run.
3. **Turn off open sign-ups.** Authentication → Sign In / Providers → turn off "Allow new users to sign up". Email provider stays on.
4. **URLs.** Authentication → URL Configuration:
   - Site URL: `http://localhost:3000` (change to the Vercel URL after deploying)
   - Redirect URLs: add `http://localhost:3000/auth/confirm` (and later `https://<vercel-url>/auth/confirm`)
5. **Email templates.** Authentication → Emails. Make the links go through the app's `/auth/confirm` route:
   - Magic link: `<a href="{{ .RedirectTo }}?token_hash={{ .TokenHash }}&type=email">Sign in to Badminton Mover</a>`
   - Invite user: `<a href="{{ .RedirectTo }}?token_hash={{ .TokenHash }}&type=invite">Accept your invite to Badminton Mover</a>`
6. **Keys.** Copy `web/.env.example` to `web/.env.local` and fill in the URL, publishable key and secret key from Project Settings → API Keys.
7. **Make yourself admin.** Authentication → Users → Invite user → your email. Then in the SQL Editor:
   ```sql
   update public.profiles set role = 'admin' where email = 'john.s.bean@gmail.com';
   ```

Supabase's built-in email sender is rate-limited (a few emails an hour). That's fine for a small club; add a custom SMTP sender later if invites start failing.
