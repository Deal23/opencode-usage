"""Standalone launcher: interactive with no arguments, CLI otherwise."""
import sys

if __name__ == '__main__':
    if len(sys.argv) == 1:
        from opencode_usage.menu import main
    else:
        from opencode_usage.core import main
    main()
