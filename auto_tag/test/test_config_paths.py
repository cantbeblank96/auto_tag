"""设置页写盘路径必须与热加载使用的配置路径一致。"""
import unittest
from unittest import mock

from auto_tag.backend.routers.health import utils_paths


class TestConfigPaths(unittest.TestCase):
    def test_paths_reports_active_config_file(self) -> None:
        with mock.patch("auto_tag.core.config.config_json_path", "/custom/config.json"):
            self.assertEqual(utils_paths()["config_path"], "/custom/config.json")
