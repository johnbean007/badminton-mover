-- Milestone 1: one profile per member. Sign-ups are off in Supabase Auth, so the only way in is an invite.

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null,
  display_name text,
  role text not null default 'member' check (role in ('admin', 'member')),
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- Every member can see who else is in the group.
create policy "Members can read profiles"
  on public.profiles for select to authenticated
  using (true);

-- Members may change only their own display name. Roles change only through the admin's server actions,
-- which use the secret key after checking the caller is the admin.
revoke update on public.profiles from authenticated;
grant update (display_name) on public.profiles to authenticated;
create policy "Members can rename themselves"
  on public.profiles for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

-- Create the profile when Supabase Auth creates the user (on invite).
create function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email);
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
