alias run := serve

all_host := "0.0.0.0"
dev2_host := "192.168.4.2"
unit := "cubrid-gha-monitor.service"

[positional-arguments]
serve *args:
    python3 -m monitor.server "$@"

serve-for-all: (serve "--host" all_host)

serve-dev2: (serve "--host" dev2_host)

demo: (serve "--demo")

# Install the persistent unit without starting or restarting the monitor.
install:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v python3 >/dev/null || { echo 'Install Python 3.11 or newer.' >&2; exit 1; }
    command -v gh >/dev/null || { echo 'Install the authenticated gh CLI and add it to PATH.' >&2; exit 1; }
    target="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/{{unit}}"
    if [[ -L "$target" ]] || { [[ -e "$target" ]] && [[ "$(head -n 1 "$target")" != '# Managed by cubrid-gha-monitor' ]]; }; then
        echo "Refusing to replace unmanaged unit $target" >&2
        exit 1
    fi
    escape_unit_value() {
        local value=$1
        value=${value//\\/\\\\}
        value=${value//\"/\\\"}
        printf '%s' "${value//%/%%}"
    }
    repo_dir=$(pwd -P)
    repo_dir=${repo_dir//%/%%}
    service_path=$(escape_unit_value "$PATH")
    service=$(<systemd/{{unit}})
    service=${service//@REPO_DIR@/"$repo_dir"}
    printf '%s\n' "${service//@PATH@/"$service_path"}" | install -Dm644 /dev/stdin "$target"
    systemctl --user daemon-reload
    echo 'Monitor unit installed. Run just up when ready to start it.'

[positional-arguments]
up *args: && status
    @if systemctl --user is-active --quiet -- {{quote(unit)}}; then \
        printf '%s is already running. Run just down before changing start options.\n' {{quote(unit)}} >&2; \
    elif [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/{{unit}}" ]; then \
        if [ "$#" -ne 0 ]; then \
            printf 'Set the host and port in cubrid-gha-monitor/service.env under your user config directory, then run just up.\n' >&2; \
            exit 1; \
        fi; \
        systemctl --user start {{quote(unit)}}; \
    else \
        systemd-run --user --quiet --same-dir --collect --unit={{quote(unit)}} \
            --service-type=notify --property=TimeoutStartSec=30s \
            --property=KillSignal=SIGINT --property=TimeoutStopSec=10s \
            --setenv="PATH=$PATH" --setenv="XDG_CACHE_HOME=${XDG_CACHE_HOME-}" \
            --setenv="GH_CONFIG_DIR=${GH_CONFIG_DIR-}" --setenv="XDG_CONFIG_HOME=${XDG_CONFIG_HOME-}" \
            -- /usr/bin/env python3 -m monitor.server "$@"; \
    fi

up-dev2: (up "--host" dev2_host)

up-for-all: (up "--host" all_host)

# Show recent service logs.
logs:
    @journalctl --user -u {{quote(unit)}} -n 50 --no-pager

status:
    #!/bin/sh
    set -eu
    properties=$(systemctl --user show -p ActiveState,StatusText,InvocationID -- {{quote(unit)}})
    state= url= invocation_id=
    while IFS='=' read -r property value; do
        case "$property" in
            ActiveState) state=$value ;;
            StatusText) url=$value ;;
            InvocationID) invocation_id=$value ;;
        esac
    done <<EOF
    $properties
    EOF

    case "$state" in
        active) ;;
        inactive) printf 'Stopped\n'; exit 0 ;;
        *) printf 'Service is %s\n' "$state"; exit 0 ;;
    esac

    if [ -z "$url" ] && [ -n "$invocation_id" ]; then
        url=$(journalctl --user -u {{quote(unit)}} "_SYSTEMD_INVOCATION_ID=$invocation_id" \
            --grep='^(LIVE|DEMO) http://' -o cat --no-pager --reverse 2>/dev/null | sed -n '1s/^[^ ]* //p')
    fi

    case "$url" in
        http://0.0.0.0:*)
            port=${url##*:}
            printf 'Running on all IPv4 interfaces\n  http://127.0.0.1:%s\n' "$port"
            ip -4 -o address show up scope global 2>/dev/null |
                awk -v port="$port" '$3 == "inet" { sub(/\/.*/, "", $4); printf "  http://%s:%s\n", $4, port }'
            ;;
        http://*) printf 'Running at %s\n' "$url" ;;
        *) printf 'Running\n' ;;
    esac

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
