alias run := serve

[positional-arguments]
serve *args:
    python3 -m monitor.server "$@"

demo: (serve "--demo")

test:
    python3 -m unittest discover -s tests -v

smoke:
    node tests/smoke.mjs
