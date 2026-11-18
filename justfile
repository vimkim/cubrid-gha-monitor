alias run := serve

dev2_host := "192.168.4.2"
unit := "cubrid-gha-monitor.service"

[positional-arguments]
serve *args:
    python3 -m monitor.server "$@"

serve-for-all: (serve "--host" "0.0.0.0")

serve-dev2: (serve "--host" dev2_host)

demo: (serve "--demo")

[positional-arguments]
up *args:
    @if systemctl --user is-active --quiet -- {{quote(unit)}}; then \
        printf '%s is already running. Run just down before changing start options.\n' {{quote(unit)}} >&2; \
    else \
        systemd-run --user --same-dir --collect --unit={{quote(unit)}} \
            --service-type=notify --property=TimeoutStartSec=30s \
            --property=KillSignal=SIGINT --property=TimeoutStopSec=10s \
            --setenv="PATH=$PATH" --setenv="XDG_CACHE_HOME=${XDG_CACHE_HOME-}" \
            --setenv="GH_CONFIG_DIR=${GH_CONFIG_DIR-}" --setenv="XDG_CONFIG_HOME=${XDG_CONFIG_HOME-}" \
            -- /usr/bin/env python3 -m monitor.server "$@"; \
    fi

up-dev2: (up "--host" dev2_host)

down:
    @if output=$(systemctl --user stop -- {{quote(unit)}} 2>&1); then \
        if [ -n "$output" ]; then printf '%s\n' "$output"; fi; \
    else \
        status=$?; \
        if state=$(systemctl --user show --property=LoadState --value -- {{quote(unit)}}) && [ "$state" = not-found ]; then exit 0; fi; \
        printf '%s\n' "$output" >&2; \
        exit "$status"; \
    fi

test:
    python3 -m unittest discover -s tests -v

smoke:
    node tests/smoke.mjs
