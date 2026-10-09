import math
import unittest

import numpy as np

from headtrack_pc.pose import (HeadPose, apply_relative, compose_matrices, from_transformation_matrix,
                               matrix_from_camera_angles, matrix_from_head_pose, relative_to, wrap_degrees)

EPS = 1e-3


class PoseMathTest(unittest.TestCase):
    def test_identity_matrix_is_neutral(self):
        p = from_transformation_matrix(matrix_from_camera_angles(0, 0, 0, 0, 0, -40))
        for v, want in zip(p.to_array(), [0, 0, 0, 0, 0, -40]):
            self.assertAlmostEqual(v, want, delta=EPS)

    def test_round_trip_over_range(self):
        for yaw in (-60.0, -20.0, 0.0, 15.0, 45.0):
            for pitch in (-40.0, -10.0, 0.0, 25.0):
                for roll in (-30.0, 0.0, 12.5):
                    p = HeadPose(yaw, pitch, roll, 3.0, -2.0, -50.0)
                    q = from_transformation_matrix(matrix_from_head_pose(p))
                    for a, b in zip(p.to_array(), q.to_array()):
                        self.assertAlmostEqual(a, b, delta=EPS)

    def test_numpy_4x4_row_major_matches_flat_column_major(self):
        p = HeadPose(30.0, -10.0, 5.0, 4.0, 1.0, -45.0)
        flat = matrix_from_head_pose(p)
        m = np.array(flat, dtype=np.float32).reshape(4, 4).T  # m[row][col], translation in column 3
        self.assertAlmostEqual(float(m[0][3]), -4.0, delta=EPS)
        q = from_transformation_matrix(m)
        for a, b in zip(p.to_array(), q.to_array()):
            self.assertAlmostEqual(a, b, delta=1e-2)

    def test_mirroring_flips_yaw_roll_x(self):
        p = HeadPose(30.0, -10.0, 5.0, 4.0, 1.0, -45.0)
        q = from_transformation_matrix(matrix_from_head_pose(p, mirrored=False), mirrored=True)
        self.assertAlmostEqual(q.yaw, -p.yaw, delta=EPS)
        self.assertAlmostEqual(q.pitch, p.pitch, delta=EPS)
        self.assertAlmostEqual(q.roll, -p.roll, delta=EPS)
        self.assertAlmostEqual(q.x, -p.x, delta=EPS)

    def test_camera_space_directions(self):
        self.assertLess(from_transformation_matrix(matrix_from_camera_angles(20, 0, 0, 0, 0, -40)).yaw, -19.0)
        self.assertLess(from_transformation_matrix(matrix_from_camera_angles(0, 15, 0, 0, 0, -40)).pitch, -14.0)
        self.assertGreater(from_transformation_matrix(matrix_from_camera_angles(0, 0, 10, 0, 0, -40)).roll, 9.0)
        self.assertAlmostEqual(from_transformation_matrix(matrix_from_camera_angles(0, 0, 0, 5, 0, -40)).x, -5.0, delta=EPS)

    def test_rejects_non_finite_and_wrong_size(self):
        m = matrix_from_camera_angles(0, 0, 0, 0, 0, -40)
        m[5] = float("nan")
        self.assertIsNone(from_transformation_matrix(m))
        self.assertIsNone(from_transformation_matrix([0.0] * 9))
        self.assertIsNone(from_transformation_matrix(np.zeros((3, 3))))
        inf = matrix_from_camera_angles(0, 0, 0, 0, 0, -40)
        inf[12] = float("inf")
        self.assertIsNone(from_transformation_matrix(inf))

    def test_gimbal_lock(self):
        p = from_transformation_matrix(matrix_from_camera_angles(10, 90, 0, 0, 0, -40))
        self.assertTrue(p.is_finite)
        self.assertAlmostEqual(p.pitch, -90.0, delta=0.5)

    def test_wrap(self):
        self.assertAlmostEqual(wrap_degrees(-190.0), 170.0, delta=EPS)
        self.assertAlmostEqual(wrap_degrees(190.0), -170.0, delta=EPS)
        self.assertAlmostEqual(wrap_degrees(180.0), 180.0, delta=EPS)
        self.assertAlmostEqual(wrap_degrees(-180.0), 180.0, delta=EPS)
        self.assertAlmostEqual(wrap_degrees(720.0), 0.0, delta=EPS)
        self.assertTrue(math.isnan(wrap_degrees(float("nan"))))
        self.assertAlmostEqual((HeadPose(-175, 0, 0) - HeadPose(175, 0, 0)).yaw, 10.0, delta=EPS)

    def test_relative_to_itself(self):
        n = HeadPose(12.0, -38.0, -6.0, 1.0, 2.0, -40.0)
        r = relative_to(HeadPose(12.0, -38.0, -6.0, 1.0, 2.0, -35.0, timestamp_nanos=7), n)
        self.assertAlmostEqual(r.yaw, 0, delta=EPS)
        self.assertAlmostEqual(r.roll, 0, delta=EPS)
        self.assertAlmostEqual(r.z, 5.0, delta=EPS)
        self.assertEqual(r.timestamp_nanos, 7)

    def test_relative_to_removes_camera_offset(self):
        for neutral in (HeadPose(0, -39, 0), HeadPose(20, -30, -6), HeadPose(-15, 25, 10)):
            for motion in (HeadPose(30, 0, 0), HeadPose(-45, 0, 0), HeadPose(0, 15, 0), HeadPose(0, 0, -20), HeadPose(25, -10, 8)):
                m = compose_matrices(matrix_from_head_pose(neutral), matrix_from_head_pose(motion))
                raw = from_transformation_matrix(m)
                r = relative_to(raw, neutral)
                self.assertAlmostEqual(r.yaw, motion.yaw, delta=EPS)
                self.assertAlmostEqual(r.pitch, motion.pitch, delta=EPS)
                self.assertAlmostEqual(r.roll, motion.roll, delta=EPS)
                back = apply_relative(neutral, r)
                self.assertAlmostEqual(back.yaw, raw.yaw, delta=EPS)

    def test_euler_subtraction_leaks_but_relative_does_not(self):
        neutral = HeadPose(0, -39, 0)
        raw = from_transformation_matrix(compose_matrices(matrix_from_head_pose(neutral), matrix_from_head_pose(HeadPose(40, 0, 0))))
        self.assertGreater(abs((raw - neutral).roll), 10.0)
        self.assertAlmostEqual(relative_to(raw, neutral).roll, 0.0, delta=EPS)
