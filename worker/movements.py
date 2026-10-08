"""Movement labels (milestone 6): the spec's ten movement types, found by rules over a rally's foot
contacts, pose and shuttle hits, so each step on the timeline reads like "Lunge (R) → Front FH".

The rules run on what's already stored (contacts from contacts.py, the pose file, shuttle hits),
so a change to movement_rules.toml can be re-run in a minute without tracking the pose again.
Every threshold and the order the rules are tried in live in that file; this module only measures
things and compares them with it.

Three kinds of candidate are labelled, each claimed by the first rule (in the file's order) that fits:
  - flights: both feet off the ground, with the landing contacts after them (the jumps);
  - pairs: the two feet landing together (split step);
  - single contacts: everything else.
A contact that only shuffles the foot (less than common.min_travel_m) and isn't at a hit, a lunge or
a turn stays unlabelled.
"""
from __future__ import annotations

import hashlib
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import contacts as ct

RULES_FILE = Path(__file__).with_name("movement_rules.toml")
TYPES = ("split_step", "chasse", "cross_step", "running_step", "lunge", "scissor_jump", "jump", "pivot", "recovery_step", "hitting_step")
FLIGHT_RULES = ("scissor_jump", "jump")
PAIR_RULES = ("split_step",)
NAMES = {"split_step": "Split step", "chasse": "Chassé", "cross_step": "Cross-step", "running_step": "Running step", "lunge": "Lunge",
         "scissor_jump": "Scissor jump", "jump": "Jump", "pivot": "Pivot", "recovery_step": "Recovery step", "hitting_step": "Hitting step"}
HIP = {"L": 11, "R": 12}
KNEE = {"L": 13, "R": 14}
ANKLE = {"L": 15, "R": 16}
FOOT = {"L": ct.LEFT, "R": ct.RIGHT}


def load_rules(path: Path = RULES_FILE) -> dict:
    """The rules file, with `rules_version` = its version plus a hash of its contents."""
    raw = path.read_bytes()
    rules = tomllib.loads(raw.decode())
    unknown = [r for r in rules["order"] if r not in TYPES]
    if unknown or sorted(rules["order"]) != sorted(TYPES):
        raise ValueError(f"movement_rules.toml order must list each of the ten types once (unknown: {unknown})")
    rules["rules_version"] = f"{rules['version']}+{hashlib.sha256(raw).hexdigest()[:8]}"
    return rules


# --- Scoring: a condition passes at its threshold with 0.5 and reaches 1 a soft margin past it -----

def _above(v: float | None, th: float, margin: float) -> float | None:
    if v is None or not math.isfinite(v) or v < th:
        return None
    scale = margin * abs(th) if th else margin
    return float(min(1.0, 0.5 + 0.5 * (v - th) / scale))


def _below(v: float | None, th: float, margin: float) -> float | None:
    if v is None or not math.isfinite(v) or v > th:
        return None
    scale = margin * abs(th) if th else margin
    return float(min(1.0, 0.5 + 0.5 * (th - v) / scale))


def _all(*scores: float | None) -> float | None:
    return None if any(s is None for s in scores) else min(scores, default=1.0)


def _r(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), 3)


# --- The rally, measured -------------------------------------------------------------------------

@dataclass
class Contact:
    i: int
    foot: str
    start: int  # frames from the rally's first pose frame
    end: int
    pos: np.ndarray  # court metres
    zone: str | None
    confidence: float


class Rally:
    def __init__(self, kp, conf, fps, first_frame, contacts, hits, H, hand, rules, width=16, height=9):
        self.kp, self.conf, self.fps, self.first, self.H, self.hand, self.rules = kp, conf, fps, first_frame, H, hand, rules
        self.aspect = width / height
        self.T = len(kp)
        self.contacts = [Contact(i, c["foot"], c["start_frame"] - first_frame, c["end_frame"] - first_frame,
                                 np.array([c["court_x_m"], c["court_y_m"]], float), c.get("zone"), float(c.get("confidence") or 0))
                         for i, c in enumerate(contacts)]
        self.order = sorted(range(len(self.contacts)), key=lambda i: (self.contacts[i].start, self.contacts[i].foot))
        self.planted = np.zeros(self.T, bool)
        for c in self.contacts:
            self.planted[max(0, c.start) : min(self.T, c.end + 1)] = True
        self.hits = sorted((h["frame"] - first_frame, h["hitter"]) for h in hits)
        self.raw_height = np.full(self.T, np.nan)
        for t in range(self.T):
            good = conf[t] > ct.KP_MIN
            if good.sum() > 3 and np.isfinite(kp[t][good]).all():
                self.raw_height[t] = np.ptp(kp[t][good][:, 1])
        self.height = ct.player_heights(kp, conf)
        self.median_height = float(np.nanmedian(self.raw_height)) if np.isfinite(self.raw_height).any() else 0.3

    def s(self, frames: float) -> float:
        return frames / self.fps

    def f(self, seconds: float) -> int:
        return round(seconds * self.fps)

    # Neighbouring contacts
    def prev_same(self, c: Contact) -> Contact | None:
        best = None
        for d in self.contacts:
            if d.foot == c.foot and d.start < c.start and (best is None or d.start > best.start):
                best = d
        return best

    def other_during(self, c: Contact, since: int) -> Contact | None:
        """The other foot's plant while this foot swung (the last one to land before it did)."""
        best = None
        for d in self.contacts:
            if d.foot != c.foot and d.start <= c.start and d.end >= since and (best is None or d.start > best.start):
                best = d
        if best is None:  # in the air: the other foot's last plant before
            for d in self.contacts:
                if d.foot != c.foot and d.start <= c.start and (best is None or d.start > best.start):
                    best = d
        return best

    # Hits
    def player_hit_in(self, lo: int, hi: int, tol: int) -> int | None:
        for f, who in self.hits:
            if who == "player" and lo - tol <= f <= hi + tol:
                return f
        return None

    def last_hit_before(self, t: int, tol: int = 0) -> tuple[int, str] | None:
        before = [h for h in self.hits if h[0] <= t + tol]
        return before[-1] if before else None

    def last_opponent_hit_before(self, t: int, tol: int) -> int | None:
        before = [f for f, who in self.hits if who == "opponent" and f <= t + tol]
        return before[-1] if before else None

    # Pose
    def court(self, t: int, idx: int) -> np.ndarray | None:
        if not (0 <= t < self.T) or self.conf[t, idx] <= ct.KP_MIN or not np.isfinite(self.kp[t, idx]).all():
            return None
        return np.array(ct.to_court(self.H, self.kp[t, idx]))

    def hip_axis(self, t: int) -> np.ndarray | None:
        """Direction from the left hip to the right hip on the court (the hips projected straight onto
        the ground plane, which keeps their direction well enough for a high camera)."""
        for dt in (0, -1, 1, -2, 2):
            a, b = self.court(t + dt, HIP["L"]), self.court(t + dt, HIP["R"])
            if a is not None and b is not None and np.linalg.norm(b - a) > 1e-3:
                return (b - a) / np.linalg.norm(b - a)
        return None

    def hip_y(self, t: int) -> float:
        if not (0 <= t < self.T):
            return math.nan
        ys = [self.kp[t, HIP[s], 1] for s in "LR" if self.conf[t, HIP[s]] > ct.KP_MIN]
        return float(np.mean(ys)) if ys else math.nan

    def knee_angle(self, t: int, foot: str) -> float:
        """Hip-knee-ankle angle on screen, degrees (180 = straight)."""
        ids = (HIP[foot], KNEE[foot], ANKLE[foot])
        if any(self.conf[t, i] <= ct.KP_MIN for i in ids):
            return math.nan
        p = self.kp[t, list(ids)] * np.array([self.aspect, 1.0])
        a, b = p[0] - p[1], p[2] - p[1]
        n = np.linalg.norm(a) * np.linalg.norm(b)
        return float(np.degrees(np.arccos(np.clip(a @ b / n, -1, 1)))) if n > 0 else math.nan

    def foot_headings(self, t0: int, t1: int, foot: str, min_len: float) -> list[tuple[int, np.ndarray]]:
        """Heel-to-toe unit vectors on the court over frames t0..t1, where the foot is long enough to tell."""
        out = []
        for t in range(max(0, t0), min(self.T, t1 + 1)):
            heel, toe = self.court(t, FOOT[foot]["heel"]), self.court(t, FOOT[foot]["big_toe"])
            if heel is not None and toe is not None and np.linalg.norm(toe - heel) >= min_len:
                out.append((t, (toe - heel) / np.linalg.norm(toe - heel)))
        return out

    def flights(self, min_frames: int) -> list[tuple[int, int]]:
        """Runs of frames (first, last) with neither foot planted, between two plants."""
        out, t = [], 0
        while t < self.T:
            if self.planted[t]:
                t += 1
                continue
            a = t
            while t < self.T and not self.planted[t]:
                t += 1
            if a > 0 and t < self.T and t - a >= min_frames:
                out.append((a, t - 1))
        return out


def step_features(r: Rally, c: Contact) -> dict:
    """How one foot's step moved relative to the other foot, Base and the body."""
    prev = r.prev_same(c)
    feats: dict = {"duration_s": r.s(c.end - c.start + 1)}
    if prev is None:
        return feats
    d = c.pos - prev.pos
    travel = float(np.linalg.norm(d))
    other = r.other_during(c, prev.end)
    feats.update(travel_m=travel, towards_net_m=float(prev.pos[1] - c.pos[1]), towards_side_m=float(abs(c.pos[0]) - abs(prev.pos[0])))
    if other is not None:
        feats["stride_m"] = float(np.linalg.norm(c.pos - other.pos))
        if travel > 1e-6:
            u = d / travel
            before, after = float((prev.pos - other.pos) @ u), float((c.pos - other.pos) @ u)
            feats.update(behind_before_m=-before, ahead_after_m=after, passed_m=min(-before, after))
        base = np.array(r.rules["common"]["base"], float)
        feats["base_before_m"] = float(np.linalg.norm((prev.pos + other.pos) / 2 - base))
        feats["base_after_m"] = float(np.linalg.norm((c.pos + other.pos) / 2 - base))
    if travel > 1e-6:
        axis = r.hip_axis((prev.end + c.start) // 2)
        if axis is not None:
            feats["sideways"] = float(abs((d / travel) @ axis))
    span = range(max(0, c.start), min(r.T, c.end + 1))
    hs = [r.raw_height[t] for t in span if np.isfinite(r.raw_height[t])]
    if hs:
        feats["height_ratio"] = float(min(hs) / r.median_height)
    ks = [k for t in span if math.isfinite(k := r.knee_angle(t, c.foot))]
    if ks:
        feats["knee_deg"] = float(min(ks))
    return feats


# --- Rules -----------------------------------------------------------------------------------------
# Each returns (score, details) when it fits, or None.

def rule_scissor_jump(r: Rally, flight, take, land, cfg, m):
    a, b = flight
    if len(take) < 2 or len(land) < 2:
        return None
    lead_before = take["L"].pos[1] - take["R"].pos[1]  # positive: left foot further back
    lead_after = land["L"].pos[1] - land["R"].pos[1]
    hit = r.player_hit_in(a, b, r.f(rules_common(cfg)["hit_tolerance_s"]))
    swapped = lead_before * lead_after < 0
    y = float(max(land["L"].pos[1], land["R"].pos[1]))
    score = _all(_above(r.s(b - a + 1), cfg["min_flight_s"], m), _above(min(abs(lead_before), abs(lead_after)), cfg["min_swap_m"], m),
                 _above(y, cfg["min_y_m"], m), 1.0 if swapped else None, 1.0 if hit is not None or not cfg["needs_hit"] else None)
    return score, {"flight_s": r.s(b - a + 1), "lead_before_m": lead_before, "lead_after_m": lead_after, "landing_y_m": y,
                   "hit_s": None if hit is None else r.s(hit)}


def rule_jump(r: Rally, flight, take, land, cfg, m):
    a, b = flight
    if len(take) < 2 or len(land) < 2:
        return None
    takeoff_gap = r.s(abs(take["L"].end - take["R"].end))
    landing_gap = r.s(abs(land["L"].start - land["R"].start))
    line = np.linspace(r.hip_y(a - 1), r.hip_y(b + 1), b - a + 3)[1:-1]
    ys = np.array([r.hip_y(t) for t in range(a, b + 1)])
    lift = float(np.nanmax(line - ys) / r.median_height) if np.isfinite(line - ys).any() else math.nan
    score = _all(_above(r.s(b - a + 1), cfg["min_flight_s"], m), _above(lift, cfg["min_hip_lift"], m),
                 _below(takeoff_gap, cfg["max_takeoff_gap_s"], m), _below(landing_gap, rules_common(cfg)["pair_s"], m))
    return score, {"flight_s": r.s(b - a + 1), "hip_lift": lift, "takeoff_gap_s": takeoff_gap, "landing_gap_s": landing_gap}


def rule_split_step(r: Rally, pair, cfg, m):
    c1, c2 = pair
    land = min(c1.start, c2.start)
    p1, p2 = r.prev_same(c1), r.prev_same(c2)
    if p1 is None or p2 is None:
        return None
    travel = float(np.linalg.norm((c1.pos + c2.pos) / 2 - (p1.pos + p2.pos) / 2))
    flight = int((~r.planted[max(p1.end, p2.end) + 1 : land]).sum()) if max(p1.end, p2.end) + 1 < land else 0
    opp = r.last_opponent_hit_before(land, r.f(-cfg["after_opponent_s"][0]))
    after = None if opp is None else r.s(land - opp)
    timed = after is not None and cfg["after_opponent_s"][0] <= after <= cfg["after_opponent_s"][1]
    score = _all(_below(travel, cfg["timed_max_travel_m" if timed else "max_travel_m"], m), 1.0 if r.s(flight) >= cfg["min_flight_s"] else None)
    if score is not None and not timed:
        score *= cfg["off_timing_confidence"]
    return score, {"travel_m": travel, "flight_s": r.s(flight), "after_opponent_hit_s": after}


def rule_lunge(r: Rally, c: Contact, f: dict, cfg, m):
    deep = max(_below(f.get("height_ratio"), cfg["max_height_ratio"], m) or 0, _below(f.get("knee_deg"), cfg["max_knee_deg"], m) or 0) or None
    towards = max(f.get("towards_net_m", -9), f.get("towards_side_m", -9))
    return _all(_above(f.get("stride_m"), cfg["min_stride_m"], m), _below(float(c.pos[1]), cfg["max_y_m"], m),
                _above(towards, cfg["min_towards_m"], m), deep), {}


def rule_hitting_step(r: Rally, c: Contact, f: dict, cfg, m):
    tol = r.f(rules_common(cfg)["hit_tolerance_s"])
    hit = r.player_hit_in(c.start, c.end, tol)
    if hit is None:
        return None
    # With both feet down at the hit, it's the step of the one that landed last (on a tie, the
    # racket-side foot).
    racket = r.hand or "R"
    for d in r.contacts:
        if d is not c and d.start - tol <= hit <= d.end + tol and (d.start, d.foot == racket) > (c.start, c.foot == racket):
            return None
    return 1.0, {"hit_s": r.s(hit)}


def rule_pivot(r: Rally, c: Contact, f: dict, cfg, m):
    if r.s(c.end - c.start + 1) < cfg["min_contact_s"]:
        return None
    last = c.start + max(1, round((c.end - c.start + 1) * cfg["planted_share"])) - 1
    hs = r.foot_headings(c.start, last, c.foot, cfg["min_foot_m"])
    n = cfg["end_frames"]
    if len(hs) < 2 * n:
        return None
    a, b = np.mean([v for _, v in hs[:n]], 0), np.mean([v for _, v in hs[-n:]], 0)
    turn = float(np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))))
    return _above(turn, cfg["min_turn_deg"], m), {"turn_deg": turn}


def rule_recovery_step(r: Rally, c: Contact, f: dict, cfg, m):
    last = r.last_hit_before(c.start)
    if last is None or last[1] != "player" or "base_after_m" not in f:
        return None
    closer = f["base_before_m"] - f["base_after_m"]
    return _all(_above(closer, cfg["min_closer_m"], m), _above(f.get("travel_m"), rules_common(cfg)["min_travel_m"], m)), {"closer_m": closer}


def rule_running_step(r: Rally, c: Contact, f: dict, cfg, m):
    return _all(_above(f.get("passed_m"), r.rules["cross_step"]["min_pass_m"], m), _above(f.get("travel_m"), cfg["min_travel_m"], m),
                _below(f.get("sideways"), cfg["max_sideways"], m)), {}


def rule_cross_step(r: Rally, c: Contact, f: dict, cfg, m):
    return _all(_above(f.get("passed_m"), cfg["min_pass_m"], m), _above(f.get("travel_m"), rules_common(cfg)["min_travel_m"], m)), {}


def rule_chasse(r: Rally, c: Contact, f: dict, cfg, m):
    if f.get("passed_m") is None:
        return None
    not_passed = 1.0 if f["passed_m"] < r.rules["cross_step"]["min_pass_m"] else None
    return _all(not_passed, _above(f.get("travel_m"), rules_common(cfg)["min_travel_m"], m)), {}


def rules_common(cfg) -> dict:
    return cfg["_common"]


SINGLE_RULES = {"lunge": rule_lunge, "hitting_step": rule_hitting_step, "pivot": rule_pivot, "recovery_step": rule_recovery_step,
                "running_step": rule_running_step, "cross_step": rule_cross_step, "chasse": rule_chasse}


def label(kp: np.ndarray, conf: np.ndarray, fps: float, first_frame: int, contacts: list[dict], hits: list[dict], H: np.ndarray,
          hand: str | None, rules: dict | None = None, width: int = 16, height: int = 9) -> list[dict]:
    """Movements for one rally, sorted by start. contacts: rows as contacts.detect makes them;
    hits: shuttle_hits rows (frame, hitter). Each movement lists the indexes of its contacts."""
    rules = rules or load_rules()
    common = rules["common"]
    margin = common["margin"]
    r = Rally(kp, conf, fps, first_frame, contacts, hits, H, hand, rules, width, height)
    cfg = {name: {**rules.get(name, {}), "_common": common} for name in TYPES}
    claimed: set[int] = set()
    found: list[dict] = []

    def add(kind, members: list[Contact], score, details, start=None, foot=None):
        evidence = float(np.mean([c.confidence for c in members])) if members else 0.5
        pos = np.mean([c.pos for c in members], 0)
        zone = members[0].zone if len(members) == 1 else ct.zone_of(float(pos[0]), float(pos[1]), hand)[0]
        found.append({"movement_type": kind, "foot": foot or (members[0].foot if len(members) == 1 else "both"), "zone": zone,
                      "start_frame": first_frame + (min(c.start for c in members) if start is None else start),
                      "end_frame": first_frame + max(c.end for c in members), "confidence": round(float(np.clip(score * evidence, 0, 1)), 3),
                      "details": {k: _r(v) if not isinstance(v, (str, bool)) else v for k, v in details.items()},
                      "contacts": sorted(c.i for c in members)})
        claimed.update(c.i for c in members)

    # Flights: the take-off and landing plants of each foot either side of a moment in the air.
    min_flight = max(1, min(r.f(rules["scissor_jump"]["min_flight_s"]), r.f(rules["jump"]["min_flight_s"])))
    flights = []
    for a, b in r.flights(min_flight):
        take, land = {}, {}
        for c in r.contacts:
            if c.end < a and (c.foot not in take or c.end > take[c.foot].end):
                take[c.foot] = c
            if c.start > b and c.start - b <= r.f(0.4) and (c.foot not in land or c.start < land[c.foot].start):
                land[c.foot] = c
        flights.append(((a, b), take, land))
    # Pairs: the two feet landing together.
    pairs = []
    for i in r.order:
        c = r.contacts[i]
        for d in r.contacts:
            if d.foot != c.foot and 0 <= d.start - c.start <= r.f(common["pair_s"]) and (c.start, c.foot) < (d.start, d.foot):
                pairs.append((c, d))
    feats = {c.i: step_features(r, c) for c in r.contacts}

    for kind in rules["order"]:
        if kind in FLIGHT_RULES:
            for (a, b), take, land in flights:
                members = list(land.values())
                if len(members) < 2 or any(c.i in claimed for c in members):
                    continue
                res = (rule_scissor_jump if kind == "scissor_jump" else rule_jump)(r, (a, b), take, land, cfg[kind], margin)
                if res and res[0] is not None:
                    add(kind, members, res[0], res[1], start=a)
        elif kind in PAIR_RULES:
            for c, d in pairs:
                if c.i in claimed or d.i in claimed:
                    continue
                res = rule_split_step(r, (c, d), cfg[kind], margin)
                if res and res[0] is not None:
                    add(kind, [c, d], res[0], res[1])
        else:
            for i in r.order:
                c = r.contacts[i]
                if i in claimed:
                    continue
                res = SINGLE_RULES[kind](r, c, feats[i], cfg[kind], margin)
                if res and res[0] is not None:
                    add(kind, [c], res[0], {**feats[i], **res[1]})
    found.sort(key=lambda mv: (mv["start_frame"], mv["end_frame"]))
    return found


def text(mv: dict) -> str:
    """A movement as the timeline shows it, e.g. "Lunge (R) → Front FH"."""
    foot = f" ({mv['foot']})" if mv["foot"] in ("L", "R") else ""
    return f"{NAMES[mv['movement_type']]}{foot} → {mv['zone'] or '?'}"


def summary(movements: list[dict], contacts: int) -> dict:
    kinds: dict[str, int] = {}
    for mv in movements:
        kinds[mv["movement_type"]] = kinds.get(mv["movement_type"], 0) + 1
    covered = len({i for mv in movements for i in mv["contacts"]})
    return {"movements": len(movements), "contacts_labelled": f"{covered}/{contacts}",
            "low_confidence": sum(mv["confidence"] < 0.6 for mv in movements), "types": kinds}
