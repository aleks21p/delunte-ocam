#!/bin/sh
# Haffop launcher (installed as /usr/bin/haffop by the .deb/.rpm packages)
PYTHONPATH=/usr/share/haffop${PYTHONPATH:+:$PYTHONPATH} exec /usr/bin/python3 -m haffop "$@"
