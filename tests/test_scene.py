import unittest

try:
    from PIL import Image
except ImportError:   # Pillow comes with mediapipe; the scene is optional without it
    Image = None

from headtrack_pc import scene


def synthetic_pano(width=720, height=160):
    """Left half red, right half blue, sky (top) bright, road (bottom) dark: easy to tell where the view went."""
    img = Image.new("RGB", (width, height))
    for x in range(width):
        for y in range(height):
            base = (200, 40, 40) if x < width // 2 else (40, 40, 200)
            k = 1.0 if y < height // 2 else 0.4
            img.putpixel((x, y), tuple(int(c * k) for c in base))
    return img


@unittest.skipIf(Image is None, "Pillow not installed")
class SceneTest(unittest.TestCase):
    def setUp(self):
        self.s = scene.CockpitScene(220, 110, pano=synthetic_pano())
        self.s.cockpit = Image.new("RGBA", self.s.cockpit.size, (0, 0, 0, 0))   # look at the photo only

    def centre(self, img):
        return img.getpixel((img.width // 2, img.height // 2))

    def test_size_and_mode(self):
        img = self.s.render(0, 0, 0)
        self.assertEqual(img.size, (220, 110))
        self.assertEqual(img.mode, "RGB")

    def test_yaw_turns_the_view(self):
        # forward is at 47 % (red side); 40° right crosses into the blue half, 60° left stays red
        right, left = self.centre(self.s.render(40, 0, 0)), self.centre(self.s.render(-60, 0, 0))
        self.assertGreater(right[2], right[0])
        self.assertGreater(left[0], left[2])

    def test_pitch_up_shows_sky_and_down_shows_road(self):
        up, down = self.centre(self.s.render(0, 30, 0)), self.centre(self.s.render(0, -30, 0))
        self.assertGreater(sum(up), sum(down))

    def test_roll_tilts_the_horizon(self):
        # roll right = the world turns left (counter-clockwise): the left side drops into the sky's colour
        img = self.s.render(-60, 0, 25)
        left, right = img.getpixel((10, 62)), img.getpixel((210, 62))
        self.assertGreater(sum(left), sum(right))

    def test_wraps_past_the_seam(self):
        img = self.s.render(175, 0, 0)   # looking behind: the photo's two ends meet in the middle
        self.assertEqual(img.size, (220, 110))

    def test_cockpit_overlay_is_drawn(self):
        s = scene.CockpitScene(220, 110, pano=synthetic_pano())
        self.assertEqual(s.cockpit.mode, "RGBA")
        bottom = s.render(0, 0, 0).getpixel((110, 108))
        self.assertLess(sum(bottom), 200)   # dashboard, not the photo


if __name__ == "__main__":
    unittest.main()
