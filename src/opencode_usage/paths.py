"""Translate shared Windows/WSL paths for the current runtime."""
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess


def local_path(value):
    text = os.path.expandvars(str(value).strip().strip('\"\''))
    text = re.sub(r'%([^%]+)%', lambda m: os.environ.get(m[1], m[0]), text)
    if os.name == 'posix' and 'microsoft' in os.uname().release.lower():
        if PureWindowsPath(text).drive:
            result = subprocess.run(['wslpath', '-u', text], check=True,
                                    capture_output=True, text=True)
            text = result.stdout.strip()
    elif os.name == 'nt':
        match = re.match(r'^/mnt/([a-zA-Z])/(.*)$', text)
        if match:
            text = match[1].upper() + ':/' + match[2]
    return Path(text).expanduser()
