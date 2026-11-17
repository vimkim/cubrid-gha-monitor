run:
    python3 -m monitor.server

demo:
    python3 -m monitor.server --demo

test:
    python3 -m unittest discover -s tests -v

smoke:
    node tests/smoke.mjs
