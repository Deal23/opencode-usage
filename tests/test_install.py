import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


class InstallerTests(unittest.TestCase):
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
