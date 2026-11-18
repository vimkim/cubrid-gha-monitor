alias run := serve

[positional-arguments]
serve *args:
    python3 -m monitor.server "$@"

serve-for-all: (serve "--host" "0.0.0.0")

serve-dev2: (serve "--host" "192.168.4.2")

demo: (serve "--demo")

test:
    python3 -m unittest discover -s tests -v

smoke:
    node tests/smoke.mjs
