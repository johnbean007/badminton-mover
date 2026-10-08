-- Milestone 6: movement labels (the "steps" on the viewer timeline), made by rules from the foot
-- contacts, pose and shuttle hits. The worker writes them with its own secret key; members only
-- read them for now. Corrections (milestone 7) add member edits, which re-runs keep.

create table public.movements (
  id uuid primary key default gen_random_uuid(),
  subject_id uuid not null references public.rally_subjects (id) on delete cascade,
  movement_type text not null check (movement_type in (
    'split_step', 'chasse', 'cross_step', 'running_step', 'lunge',
    'scissor_jump', 'jump', 'pivot', 'recovery_step', 'hitting_step')),
  foot text not null check (foot in ('L', 'R', 'both')),
  zone text check (zone in ('Front FH', 'Front C', 'Front BH', 'Mid FH', 'Base', 'Mid BH', 'Rear FH', 'Rear C', 'Rear BH')),
  start_frame int not null check (start_frame >= 0), -- playback-copy frame numbers, inclusive
  end_frame int not null,
  confidence real check (confidence between 0 and 1),
  source text not null default 'rules' check (source in ('rules', 'user', 'model')),
  rules_version text,
  -- What the rule measured (stride, hip lift, turn angle, ...), for tuning and the step panel.
  details jsonb,
  verified boolean not null default false,
  unsure boolean not null default false,
  deleted boolean not null default false,
  created_by uuid references public.profiles (id) on delete set null,
  created_at timestamptz not null default now(),
  constraint movement_frames_in_order check (end_frame >= start_frame)
);

create index movements_subject_idx on public.movements (subject_id, start_frame);

-- Which contacts make up a movement.
create table public.movement_contacts (
  movement_id uuid not null references public.movements (id) on delete cascade,
  contact_id uuid not null references public.contacts (id) on delete cascade,
  primary key (movement_id, contact_id)
);

create index movement_contacts_contact_idx on public.movement_contacts (contact_id);

alter table public.movements enable row level security;
alter table public.movement_contacts enable row level security;

create policy "Members can read movements"
  on public.movements for select to authenticated
  using (true);
create policy "Members can read movement contacts"
  on public.movement_contacts for select to authenticated
  using (true);
revoke insert, update, delete on public.movements from authenticated;
revoke insert, update, delete on public.movement_contacts from authenticated;
