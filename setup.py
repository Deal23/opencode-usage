"""Keep setuptools build output isolated from stale local artifacts."""
import tempfile

from setuptools import setup
from setuptools.command.build import build


_build_dir = tempfile.TemporaryDirectory(prefix='ocusage-build-')


class IsolatedBuild(build):
    def initialize_options(self):
        super().initialize_options()
        self.build_base = _build_dir.name


setup(cmdclass={'build': IsolatedBuild})
