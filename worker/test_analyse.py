"""Offline checks for the analysis helpers (no models or video needed).

    uv run python -m unittest test_analyse
"""
from __future__ import annotations

import unittest

import numpy as np

import analyse as an
import contacts as ct
import courtfit as cf
import movements as mv


class GapFilling(unittest.TestCase):
    def test_short_gaps_filled_long_gaps_left(self):
        xy = np.array([[float(t), 2.0 * t] for t in range(20)], np.float32)
        xy[3:6] = np.nan  # 3 missing: filled
        xy[10:17] = np.nan  # 7 missing: left empty
        out, filled = an.dedupe_and_fill(xy)
        np.testing.assert_allclose(out[3:6], [[3, 6], [4, 8], [5, 10]], atol=1e-5)
        self.assertTrue(np.isnan(out[10:17]).all())
        self.assertEqual(filled.sum(), 3)

    def test_repeated_frame_counts_as_missing(self):
        xy = np.array([[0, 0], [10, 10], [10, 10], [30, 30]], np.float32)
        out, filled = an.dedupe_and_fill(xy)
        np.testing.assert_allclose(out[2], [20, 20])
        self.assertTrue(filled[2])


class LeftRight(unittest.TestCase):
    def test_one_frame_leg_swap_is_undone(self):
        T = 10
        kp = np.zeros((T, an.N_KP, 2), np.float32)
        for t in range(T):
            for i in range(an.N_KP):
                kp[t, i] = (100 + i * 3 + t, 200 + i * 5)
            for a, b in an.LOWER_PAIRS:  # legs well apart: left at x≈100, right at x≈200
                kp[t, a, 0], kp[t, b, 0] = 100 + t, 200 + t
        conf = np.ones((T, an.N_KP), np.float32)
        heights = np.full(T, 300, np.float32)
        bad = kp.copy()
        for a, b in an.LOWER_PAIRS:
            bad[5, [a, b]] = bad[5, [b, a]]
        swaps = an.fix_left_right(bad, conf, heights)
        self.assertEqual(swaps, 1)
        np.testing.assert_allclose(bad, kp)

    def test_gradual_crossing_is_kept(self):
        T = 30
        kp = np.zeros((T, an.N_KP, 2), np.float32)
        for t in range(T):  # feet cross over slowly, as in a crossover step
            for a, b in an.LOWER_PAIRS:
                kp[t, a, 0], kp[t, b, 0] = 100 + 4 * t, 200 - 4 * t
        before = kp.copy()
        self.assertEqual(an.fix_left_right(kp, np.ones((T, an.N_KP), np.float32), np.full(T, 300, np.float32)), 0)
        np.testing.assert_allclose(kp, before)


class Smoothing(unittest.TestCase):
    def test_still_point_steadied_and_low_confidence_left_alone(self):
        rng = np.random.default_rng(0)
        T = 120
        kp = np.full((T, an.N_KP, 2), 500, np.float32) + rng.normal(0, 3, (T, an.N_KP, 2)).astype(np.float32)
        conf = np.ones((T, an.N_KP), np.float32)
        conf[:, 0] = 0.1
        out = an.one_euro(kp, conf, 30, scale=720)
        self.assertLess(out[30:, 1:].std(), 0.5 * kp[30:, 1:].std())
        np.testing.assert_allclose(out[:, 0], kp[:, 0])


class Hits(unittest.TestCase):
    def test_alternating_arcs(self):
        """Four shots as screen arcs bottoming out at frames 30, 60, 90, 120: the near player swings at 30 and 90."""
        fps, T = 30.0, 150
        xy = np.full((T, 2), np.nan, np.float32)
        for t in range(10, 141):
            k = (t - 30) % 30
            xy[t] = (100 + 8 * t, 700 - 0.8 * (k * (30 - k)))  # y largest (lowest on screen) at each hit
        near_kp = np.zeros((T, an.N_KP, 2), np.float32)
        near_kp[:] = np.linspace([380, 500], [420, 800], an.N_KP)
        far_kp = near_kp * 0.4
        for hit, kp in ((30, near_kp), (90, near_kp), (60, far_kp), (120, far_kp)):
            kp[hit, 9:11] += 60  # a racket-arm swing
        conf = np.ones((T, an.N_KP), np.float32)
        far_box = np.tile(np.float32([150, 200, 170, 320]), (T, 1))
        hits = an.find_hits(near_kp, conf, np.ones(T, bool), far_box, far_kp, conf.copy(), xy, fps, px=720 / an.TUNED_HEIGHT)
        self.assertEqual([h["index"] for h in hits], [30, 60, 90, 120])
        self.assertEqual([h["hitter"] for h in hits], ["player", "opponent", "player", "opponent"])


class Contacts(unittest.TestCase):
    def test_zones_follow_handedness_and_flag_out_of_court(self):
        self.assertEqual(ct.zone_of(1.5, 1.0, "R"), ("Front FH", False))
        self.assertEqual(ct.zone_of(1.5, 1.0, "L"), ("Front BH", False))
        self.assertEqual(ct.zone_of(0.0, 3.0, "L"), ("Base", False))
        self.assertEqual(ct.zone_of(-1.5, 6.0, "L"), ("Rear FH", False))
        self.assertEqual(ct.zone_of(-3.0, 6.0, "R"), ("Rear BH", True))  # beyond the singles sideline

    def test_plants_found_between_strides(self):
        """The left foot is on one spot for frames 9-24 and 39-54 and moves in between; the right never stops."""
        fps, T = 30.0, 70
        kp = np.zeros((T, an.N_KP, 2))
        kp[:, :, 1] = np.linspace(0.3, 0.6, an.N_KP)  # a standing figure 0.3 of the frame tall
        kp[:, :, 0] = 0.5
        x = np.zeros(T)
        for t in range(1, T):
            x[t] = x[t - 1] + (0 if 10 <= t <= 24 or 40 <= t <= 54 else 0.02)
        for i in (15, 17, 19):
            kp[:, i, 0] += x
        for i in (16, 20, 22):
            kp[:, i, 0] += 0.015 * np.arange(T)
        conf = np.ones((T, an.N_KP))
        H = np.eye(3)
        found = ct.detect(kp, conf, fps, 100, H, "R")
        self.assertEqual([(c["foot"], c["start_frame"], c["end_frame"]) for c in found], [("L", 109, 124), ("L", 139, 154)])


class CourtFit(unittest.TestCase):
    def test_fits_drawn_court_and_rejects_shifted_one(self):
        """A court drawn in white on green: its own calibration fits, a zoomed-out one doesn't."""
        W, Hh = 1280, 720
        corners = np.float32([[0.15, 0.93], [0.85, 0.93], [0.66, 0.47], [0.34, 0.47]])
        court = np.float32([[-3.05, 6.7], [3.05, 6.7], [3.05, -6.7], [-3.05, -6.7]])
        import cv2

        H = cv2.getPerspectiveTransform(corners, court).astype(float)
        frame = np.zeros((Hh, W, 3), np.uint8)
        frame[:] = (90, 160, 60)
        inv = np.linalg.inv(H)
        for (x0, y0), (x1, y1) in cf.LINES:
            pts = []
            for x, y in ((x0, y0), (x1, y1)):
                p = inv @ [x, y, 1]
                pts.append((int(p[0] / p[2] * W), int(p[1] / p[2] * Hh)))
            cv2.line(frame, pts[0], pts[1], (255, 255, 255), 3)
        self.assertGreater(cf.line_fit(frame, H), 0.95)
        zoomed = cv2.getPerspectiveTransform((corners - 0.5) * 0.85 + 0.5, court).astype(float)
        self.assertLess(cf.line_fit(frame, zoomed), cf.FIT_OK)

        # Roughly clicked corners (a few pixels to ~25 px out) snap back onto the drawn lines.
        rough = corners + np.float32([[0.01, 0.03], [-0.008, 0.035], [0.004, -0.008], [-0.003, 0.006]])
        snapped = cf.snap([frame], rough)
        self.assertIsNotNone(snapped)
        self.assertGreater(snapped[1], 0.95)
        np.testing.assert_allclose(snapped[0], corners, atol=0.004)


class Movements(unittest.TestCase):
    """Rules over hand-made contact sequences. The pose is a still figure (hips across the court, so
    sideways = along x) with court metres = 10 × frame fractions."""

    fps, T = 30.0, 120
    H = np.diag([10.0, 10.0, 1.0])

    def figure(self):
        kp = np.zeros((self.T, an.N_KP, 2))
        kp[:, :, 0] = 0.5
        kp[:, :, 1] = np.linspace(0.2, 0.5, an.N_KP)  # 0.3 of the frame tall
        kp[:, 11], kp[:, 12] = (0.45, 0.4), (0.55, 0.4)  # left hip, right hip
        return kp, np.ones((self.T, an.N_KP))

    @staticmethod
    def contact(foot, start, end, x, y, zone=None):
        return {"foot": foot, "start_frame": start, "end_frame": end, "court_x_m": x, "court_y_m": y, "zone": zone or "Base", "confidence": 0.9}

    def label(self, contacts, hits=(), kp=None):
        k, c = self.figure() if kp is None else (kp, np.ones((self.T, an.N_KP)))
        return mv.label(k, c, self.fps, 0, contacts, list(hits), self.H, "R")

    def test_rules_file_lists_every_type_once(self):
        rules = mv.load_rules()
        self.assertEqual(sorted(rules["order"]), sorted(mv.TYPES))
        self.assertRegex(rules["rules_version"], r"^movements-\d+\+[0-9a-f]{8}$")

    def test_split_step_after_opponent_hit(self):
        found = self.label([self.contact("L", 0, 20, -0.3, 3.3), self.contact("R", 0, 20, 0.3, 3.3),
                            self.contact("L", 24, 40, -0.35, 3.3), self.contact("R", 25, 40, 0.35, 3.3)],
                           hits=[{"frame": 21, "hitter": "opponent"}])
        self.assertEqual([(m["movement_type"], m["foot"], m["contacts"]) for m in found], [("split_step", "both", [2, 3])])
        self.assertGreater(found[0]["confidence"], 0.6)

    def test_lunge_beats_hitting_step_and_hitting_step_takes_the_latest_landing(self):
        kp, _ = self.figure()
        kp[50:70, :, 1] = np.linspace(0.27, 0.5, an.N_KP)  # hips drop: 0.23 tall instead of 0.3
        found = self.label([self.contact("L", 0, 70, 0.0, 3.3), self.contact("R", 0, 30, 0.3, 3.3),
                            self.contact("R", 45, 70, 0.6, 1.5, "Front FH")],
                           hits=[{"frame": 50, "hitter": "player"}], kp=kp)
        self.assertEqual([(m["movement_type"], m["foot"], mv.text(m)) for m in found], [("lunge", "R", "Lunge (R) → Front FH")])

        found = self.label([self.contact("L", 0, 70, 0.0, 3.3), self.contact("R", 0, 30, 0.3, 3.3),
                            self.contact("R", 45, 70, 0.5, 3.0)], hits=[{"frame": 50, "hitter": "player"}])
        self.assertEqual([(m["movement_type"], m["foot"]) for m in found], [("hitting_step", "R")])

    def test_cross_step_passes_the_other_foot_and_chasse_does_not(self):
        # Moving to the player's right (along the hip line), the right foot planted at x = 0.3.
        cross = self.label([self.contact("L", 0, 10, -0.3, 3.3), self.contact("R", 0, 40, 0.3, 3.3), self.contact("L", 20, 40, 0.8, 3.3)])
        self.assertEqual([m["movement_type"] for m in cross], ["cross_step"])
        chasse = self.label([self.contact("L", 0, 10, -0.3, 3.3), self.contact("R", 0, 40, 0.3, 3.3), self.contact("L", 20, 40, 0.0, 3.3)])
        self.assertEqual([m["movement_type"] for m in chasse], ["chasse"])

    def test_scissor_jump_swaps_the_feet_in_the_air(self):
        found = self.label([self.contact("L", 0, 30, 0.0, 5.8), self.contact("R", 0, 30, 0.4, 5.0),
                            self.contact("R", 40, 60, 0.4, 6.2, "Rear FH"), self.contact("L", 43, 60, 0.0, 5.2, "Rear C")],
                           hits=[{"frame": 35, "hitter": "player"}])
        self.assertEqual([(m["movement_type"], m["foot"], m["start_frame"]) for m in found], [("scissor_jump", "both", 31)])


if __name__ == "__main__":
    unittest.main()
