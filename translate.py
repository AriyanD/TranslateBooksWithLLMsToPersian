"""CLI:  python translate.py mybook.epub -p providers.json"""
import sys

from ptranslator.cli import main

if __name__ == "__main__":
    sys.exit(main())
