-- Milestone 2: players and clips. Every member sees the whole shared library; only a clip's uploader
-- or the admin can change or delete it.

create function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from public.profiles where id = (select auth.uid()) and role = 'admin');
$$;

create table public.players (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(trim(name)) between 1 and 80),
  -- "Lin  Dan", "lin dan" and "Lin Dan" are the same player.
  name_key text generated always as (lower(regexp_replace(trim(name), '\s+', ' ', 'g'))) stored unique,
  handedness text check (handedness in ('L', 'R')),
  created_by uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at timestamptz not null default now()
);

alter table public.players enable row level security;

create policy "Members can read players"
  on public.players for select to authenticated
  using (true);

create policy "Members can add players"
  on public.players for insert to authenticated
  with check (created_by = (select auth.uid()));

-- Any member can fill in a missing handedness (an opponent added without one, say); once it is
-- known, only the admin changes it.
revoke update on public.players from authenticated;
grant update (handedness) on public.players to authenticated;
create policy "Members fill in handedness, admin edits"
  on public.players for update to authenticated
  using (handedness is null or (select public.is_admin()))
  with check (handedness is not null or (select public.is_admin()));

create table public.clips (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles (id) on delete cascade default auth.uid(),
  visibility text not null default 'shared' check (visibility in ('shared', 'private')),
  player_id uuid references public.players (id) on delete restrict,
  opponent_id uuid references public.players (id) on delete restrict,
  discipline text check (discipline in ('MS', 'WS', 'MD', 'WD', 'XD')),
  tournament text check (length(tournament) <= 120),
  round text check (round in ('R64', 'R32', 'R16', 'QF', 'SF', 'F', 'Other')),
  match_date date,
  source_url text check (length(source_url) <= 500),
  notes text check (length(notes) <= 2000),
  shirt_colour text, -- sampled in rally review (milestone 3)
  fps numeric(6, 3), -- read by the worker's pre-scan (milestone 3)
  width int,
  height int,
  duration_s numeric(8, 3),
  size_bytes bigint,
  original_key text not null,
  playback_key text,
  thumb_key text,
  -- uploading: file on its way to R2 · uploaded: waiting for pre-scan · later milestones add
  -- prescanning / review / queued / analysing / ready.
  status text not null default 'uploading'
    check (status in ('uploading', 'uploaded', 'prescanning', 'review', 'queued', 'analysing', 'ready', 'failed')),
  error text,
  created_at timestamptz not null default now(),
  uploaded_at timestamptz,
  -- A finished upload must say whose footwork it is.
  constraint uploaded_clip_has_details check (status = 'uploading' or (player_id is not null and discipline is not null))
);

create index clips_created_at_idx on public.clips (created_at desc);
create index clips_player_idx on public.clips (player_id);
create index clips_opponent_idx on public.clips (opponent_id);

alter table public.clips enable row level security;

create policy "Members can read clips"
  on public.clips for select to authenticated
  using (visibility = 'shared' or owner_id = (select auth.uid()));

-- Members create a clip in the uploading state; status, playback copy and video facts are set by the
-- server and the worker, which use the secret key.
revoke insert on public.clips from authenticated;
grant insert (id, player_id, opponent_id, discipline, tournament, round, match_date, source_url, notes,
  width, height, duration_s, size_bytes, original_key, thumb_key) on public.clips to authenticated;
create policy "Members can add their own clips"
  on public.clips for insert to authenticated
  with check (owner_id = (select auth.uid()) and status = 'uploading');

-- After upload, members edit only the match details.
revoke update on public.clips from authenticated;
grant update (player_id, opponent_id, discipline, tournament, round, match_date, source_url, notes) on public.clips to authenticated;
create policy "Uploader or admin can edit clips"
  on public.clips for update to authenticated
  using (owner_id = (select auth.uid()) or (select public.is_admin()))
  with check (owner_id = (select auth.uid()) or (select public.is_admin()));

create policy "Uploader or admin can delete clips"
  on public.clips for delete to authenticated
  using (owner_id = (select auth.uid()) or (select public.is_admin()));
