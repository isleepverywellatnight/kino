"""Rendu HDR: the native player leaves HDR conversion to mpv's defaults, which
were measured to convert HDR10 well on a standard screen (docs/playback.md,
« Rendu HDR »). Any image option added here needs a new measurement."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE_OPTIONS = {"tone-mapping", "tone-mapping-param", "hdr-compute-peak", "target-trc", "target-prim", "target-peak",
                 "target-colorspace-hint", "gamma", "brightness", "contrast", "saturation", "vf"}


def literal(path, name):
    tree = ast.parse((ROOT / "src" / path).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == name for t in n.targets))
    return ast.literal_eval(node.value)


class NativeHdrTests(unittest.TestCase):
    def test_the_player_sets_no_image_option_when_it_opens(self):
        options = dict(literal("native_view.py", "OPTIONS"))
        source = (ROOT / "src" / "native_player.py").read_text(encoding="utf-8")
        defaults = source[source.index("defaults = {"):source.index("}", source.index("defaults = {")) + 1]
        options.update(ast.literal_eval(defaults.split("=", 1)[1].strip()))
        self.assertEqual(set(options) & IMAGE_OPTIONS, set())
        self.assertEqual(options["vo"], "libmpv")

    def test_the_measurement_is_written_down(self):
        doc = (ROOT / "docs" / "dev" / "playback.md").read_text(encoding="utf-8")
        self.assertIn("| Lecteur natif tel quel | 66 | 182 | 225 | 255 | 254 |", doc)
        self.assertIn("| Mêmes valeurs non converties (SDR) | 76 | 127 | 145 | 189 | 227 |", doc)


if __name__ == "__main__":
    unittest.main()
