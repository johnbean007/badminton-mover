-- Milestone 4: who is tracked in each analysed rally, and the shuttle hits the worker finds.
-- The worker writes both with its own secret key. Members only read them for now; editing hits
-- arrives with corrections (milestone 7).

create table public.rally_subjects (
  id uuid primary key default gen_random_uuid(),
  rally_id uuid not null references public.rallies (id) on delete cascade,
  player_id uuid not null references public.players (id) on delete restrict,
  -- Phase 1 tracks the near-side player only; doubles will add up to four rows.
  side text not null check (side in ('near', 'far')),
  created_at timestamptz not null default now(),
  unique (rally_id, player_id)
);

create index rally_subjects_player_idx on public.rally_subjects (player_id);

alter table public.rally_subjects enable row level security;

create policy "Members can read rally subjects"
  on public.rally_subjects for select to authenticated
  using (true);
revoke insert, update, delete on public.rally_subjects from authenticated;

create table public.shuttle_hits (
  id uuid primary key default gen_random_uuid(),
  rally_id uuid not null references public.rallies (id) on delete cascade,
  frame int not null check (frame >= 0), -- playback-copy frame number, like rallies.start_frame
  hitter text not null check (hitter in ('player', 'opponent')),
  confidence real check (confidence between 0 and 1),
  -- tracker: found by the worker, replaced when the rally is analysed again · user: added by a member
  source text not null default 'tracker' check (source in ('tracker', 'user')),
  deleted boolean not null default false,
  created_by uuid references public.profiles (id) on delete set null,
  created_at timestamptz not null default now()
);

create index shuttle_hits_rally_idx on public.shuttle_hits (rally_id, frame);

alter table public.shuttle_hits enable row level security;

create policy "Members can read shuttle hits"
  on public.shuttle_hits for select to authenticated
  using (true);
revoke insert, update, delete on public.shuttle_hits from authenticated;
