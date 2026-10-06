-- How well the rally's court calibration matches its camera view: the share of the calibrated court
-- lines that sit on white line pixels in sampled frames (0-1; about 0.95 when it fits). Set by the
-- worker; the review page and viewer warn when it's low (the camera zoomed or panned).
alter table public.rallies add column court_fit real check (court_fit between 0 and 1);
