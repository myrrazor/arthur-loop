#!/usr/bin/env python3
import sys

from arthur_loop.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["status", *sys.argv[1:]]))
