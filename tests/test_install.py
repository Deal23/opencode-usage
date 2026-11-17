import importlib.util
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import shutil
import re
import zipfile
from unittest.mock import patch
from types import SimpleNamespace


class InstallerTests(unittest.TestCase):
    def test_release_version_matches_readme_and_package(self):
        source = Path(__file__).resolve().parents[1]
        metadata = (source/'pyproject.toml').read_text(encoding='utf-8')
        version = re.search(r'^version\s*=\s*"(\d+\.\d+\.\d+)"', metadata, re.M)
        self.assertIsNotNone(version)
        number = version.group(1)
        self.assertIn(f'__version__ = "{number}"', (source/'src/opencode_usage/__init__.py').read_text(encoding='utf-8'))
        self.assertIn(f'当前版本 **{number}**', (source/'README.md').read_text(encoding='utf-8'))

    def test_wheel_ignores_stale_private_build_files(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)/'project'
            project.mkdir()
            for name in ('pyproject.toml', 'setup.py', 'README.md', 'LICENSE', 'MANIFEST.in'):
                shutil.copy(source/name, project/name)
            shutil.copytree(source/'src/opencode_usage', project/'src/opencode_usage', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            stale = project/'build/lib/opencode_usage'
            stale.mkdir(parents=True)
            for name in ('model-prices.json', 'reverix-prices.json'):
                (stale/name).write_text('private marker', encoding='utf-8')
            output = Path(folder)/'wheels'
            result = subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--wheel-dir', str(output), str(project)], capture_output=True, text=True, errors='replace')
            self.assertEqual(result.returncode, 0, f'wheel build failed:\n{result.stdout}\n{result.stderr}')
            with zipfile.ZipFile(next(output.glob('*.whl'))) as wheel:
                names = wheel.namelist()
                self.assertFalse(any(n.endswith(('model-prices.json', 'reverix-prices.json')) for n in names))
                self.assertTrue(any(n.endswith('dashboard.html') for n in names))
            self.assertEqual((stale/'model-prices.json').read_text(encoding='utf-8'), 'private marker')

    def test_conflicting_second_launcher_stops_before_venv_or_pip(self):
        spec = importlib.util.spec_from_file_location('ocu_installer', Path(__file__).resolve().parents[1]/'install.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            commands = home/'.local/bin'
            commands.mkdir(parents=True)
            target = commands/'ocusage.cmd'
            target.write_text('@echo off\nother.exe %*\n', encoding='utf-8')
            with patch.object(installer.Path, 'home', return_value=home), patch.object(installer, 'os', SimpleNamespace(name='nt')), patch('sys.argv', ['install.py']), patch.object(installer.venv, 'EnvBuilder') as builder, patch.object(installer.subprocess, 'run') as run:
                with self.assertRaises(RuntimeError):
                    installer.main()
            builder.assert_not_called()
            run.assert_not_called()
            self.assertFalse((commands/'ocu.cmd').exists())
            self.assertIn('other.exe', target.read_text(encoding='utf-8'))

    def test_legacy_double_carriage_return_launcher_is_upgraded_safely(self):
        spec = importlib.util.spec_from_file_location('ocu_installer', Path(__file__).resolve().parents[1]/'install.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'ocu.cmd'
            executable = Path(folder)/'ocu.exe'
            target.write_bytes(f'@echo off\r\r\n"{executable}" %*\r\r\n'.encode('utf-8'))
            installer.write_windows_launcher(target, executable, 'ocu')
            self.assertNotIn(b'\r\r\n', target.read_bytes())
            self.assertIn(b'%~dp0', target.read_bytes())
            installer.write_windows_launcher(target, executable, 'ocu')
            target.write_text('@echo off\nother.exe %*\n', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                installer.write_windows_launcher(target, executable, 'ocu')
