import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import Project
from upscaler import model_available, model_availability


class ModelSelectionTests(unittest.TestCase):
    def test_models_have_independent_availability(self):
        with tempfile.TemporaryDirectory() as directory, patch('upscaler.WEIGHT_ROOT', Path(directory)):
            self.assertFalse(model_available())
            (Path(directory) / 'realesr-animevideov3.pth').touch()
            self.assertEqual(model_availability(), {'realesrgan': False, 'realesrgan-animevideo': True})
            self.assertFalse(model_available('unknown'))
            (Path(directory) / 'realesr-general-x4v3.pth').touch()
            self.assertFalse(model_available())
            (Path(directory) / 'realesr-general-wdn-x4v3.pth').touch()
            self.assertTrue(model_available())

    def test_export_selects_and_persists_model(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Project(directory)
            project.meta = dict(width=48, height=32, image_width=48, image_height=32)
            with patch('engine.model_available', return_value=True) as available:
                options = project.export_options({'upscale': 'realesrgan-animevideo', 'size_mode': '2x'})
                self.assertEqual((options['width'], options['height']), (96, 64))
                self.assertEqual(options['upscale'], 'realesrgan-animevideo')
                available.assert_called_once_with('realesrgan-animevideo')
                project.export_preferences = options
                self.assertEqual(project.export_options()['upscale'], 'realesrgan-animevideo')
            with patch('engine.model_available', return_value=False):
                with self.assertRaisesRegex(ValueError, 'realesr-animevideov3'):
                    project.export_options()
                self.assertEqual(project.export_options({'upscale': 'lanczos'})['upscale'], 'lanczos')
            with self.assertRaisesRegex(ValueError, '放大方式'):
                project.export_options({'upscale': 'arbitrary-model'})


if __name__ == '__main__':
    unittest.main()
