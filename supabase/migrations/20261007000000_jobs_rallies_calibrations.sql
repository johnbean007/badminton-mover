-- Milestone 3: worker jobs, proposed rally segments and court calibration.
-- The worker writes with its own secret key; members edit segments and calibration through the app,
-- and only on clips they uploaded (or any clip, for the admin).

create function public.can_edit_clip(target uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.clips c
    where c.id = target and (c.owner_id = (select auth.uid()) or public.is_admin())
  );
$$;

-- Shirt colour is sampled during review.
grant update (shirt_colour) on public.clips to authenticated;

create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid not null references public.clips (id) on delete cascade,
  type text not null check (type in ('prescan', 'sidecheck', 'analyse', 'reanalyse')),
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
  progress real not null default 0 check (progress between 0 and 1),
  attempts int not null default 0,
  error text,
  versions jsonb, -- tool and model versions the worker used
  created_by uuid references public.profiles (id) on delete set null,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index jobs_clip_idx on public.jobs (clip_id, created_at desc);
create index jobs_open_idx on public.jobs (status) where status in ('queued', 'running');

alter table public.jobs enable row level security;

-- Members watch progress; only the server and the worker create or change jobs.
create policy "Members can read jobs"
  on public.jobs for select to authenticated
  using (true);
revoke insert, update, delete on public.jobs from authenticated;

create table public.calibrations (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid not null references public.clips (id) on delete cascade,
  -- Outer court corners (doubles sidelines × baselines) as fractions of the frame (0–1), so they hold for any resolution:
  -- near-left, near-right, far-right, far-left.
  corners jsonb not null check (jsonb_typeof(corners) = 'array' and jsonb_array_length(corners) = 4),
  homography jsonb not null, -- 3×3, frame fractions → court metres
  court text not null default 'singles' check (court in ('singles')),
  frame int, -- the frame the corners were clicked on
  created_by uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at timestamptz not null default now()
);

create index calibrations_clip_idx on public.calibrations (clip_id);

alter table public.calibrations enable row level security;

create policy "Members can read calibrations"
  on public.calibrations for select to authenticated
  using (true);
revoke insert, update, delete on public.calibrations from authenticated;
grant insert (clip_id, corners, homography, court, frame) on public.calibrations to authenticated;
create policy "Clip editors can add calibrations"
  on public.calibrations for insert to authenticated
  with check (created_by = (select auth.uid()) and (select public.can_edit_clip(clip_id)));

create table public.rallies (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid not null references public.clips (id) on delete cascade,
  index int not null,
  start_frame int not null check (start_frame >= 0),
  end_frame int not null, -- inclusive
  included boolean not null default true,
  calibration_id uuid references public.calibrations (id) on delete set null,
  thumb_key text,
  -- The near-side check: which side the tracked player is on in this segment.
  near_side text check (near_side in ('near', 'far', 'unclear')),
  pose_key text,
  shuttle_key text,
  is_gold boolean not null default false,
  status text not null default 'proposed' check (status in ('proposed', 'queued', 'analysing', 'ready', 'failed')),
  created_at timestamptz not null default now(),
  constraint rally_frames_in_order check (end_frame > start_frame)
);

create index rallies_clip_idx on public.rallies (clip_id, start_frame);

alter table public.rallies enable row level security;

create policy "Members can read rallies"
  on public.rallies for select to authenticated
  using (true);

-- Review edits: delete, merge, split and trim segments, and point them at a calibration.
revoke insert, update, delete on public.rallies from authenticated;
grant insert (clip_id, index, start_frame, end_frame, included, calibration_id, thumb_key) on public.rallies to authenticated;
grant update (index, start_frame, end_frame, included, calibration_id) on public.rallies to authenticated;
grant delete on public.rallies to authenticated;
create policy "Clip editors can add segments"
  on public.rallies for insert to authenticated
  with check ((select public.can_edit_clip(clip_id)));
create policy "Clip editors can edit segments"
  on public.rallies for update to authenticated
  using ((select public.can_edit_clip(clip_id)))
  with check ((select public.can_edit_clip(clip_id)));
create policy "Clip editors can remove segments"
  on public.rallies for delete to authenticated
  using ((select public.can_edit_clip(clip_id)));
