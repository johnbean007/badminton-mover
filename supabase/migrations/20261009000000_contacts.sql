-- Milestone 5: foot contacts (one row per foot plant) with court position and zone.
-- The worker writes them with its own secret key from the stored pose files. Members only read
-- them for now; corrections (milestone 7) add member edits, which re-runs keep.

create table public.contacts (
  id uuid primary key default gen_random_uuid(),
  subject_id uuid not null references public.rally_subjects (id) on delete cascade,
  foot text not null check (foot in ('L', 'R')),
  start_frame int not null check (start_frame >= 0), -- landing, playback-copy frame number
  end_frame int not null, -- lift-off, inclusive
  -- Ground point (between heel and toe) in court metres: x across the court (positive to the
  -- player's right), y from the net (0) towards the near baseline (+6.7).
  court_x_m real,
  court_y_m real,
  zone text check (zone in ('Front FH', 'Front C', 'Front BH', 'Mid FH', 'Base', 'Mid BH', 'Rear FH', 'Rear C', 'Rear BH')),
  out_of_court boolean not null default false, -- landed outside the singles court; zone is the nearest one
  confidence real check (confidence between 0 and 1),
  source text not null default 'rules' check (source in ('rules', 'user', 'model')),
  rules_version text,
  deleted boolean not null default false,
  created_by uuid references public.profiles (id) on delete set null,
  created_at timestamptz not null default now(),
  constraint contact_frames_in_order check (end_frame >= start_frame)
);

create index contacts_subject_idx on public.contacts (subject_id, start_frame);

alter table public.contacts enable row level security;

create policy "Members can read contacts"
  on public.contacts for select to authenticated
  using (true);
revoke insert, update, delete on public.contacts from authenticated;
