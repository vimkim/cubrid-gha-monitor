alias run := serve

all_host := "0.0.0.0"
dev2_host := "192.168.4.2"
unit := "cubrid-gha-monitor.service"

[private]
default:
    @just --list

# Run the monitor server in the foreground.
[positional-arguments]
serve *args:
    python3 -m monitor.server "$@"

# Serve in the foreground bound to all IPv4 interfaces.
serve-for-all: (serve "--host" all_host)

# Serve in the foreground bound to Dev2 IP (192.168.4.2).
serve-dev2: (serve "--host" dev2_host)

# Run the monitor server with built-in demo data.
demo: (serve "--demo")

# Install the persistent systemd user unit without starting or restarting it.
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
    echo 'Monitor unit installed. Run just up (or just up-dev2 / just up-for-all) when ready to start it.'

# Start the background service (updates service.env and restarts when options are passed to an installed unit).
[positional-arguments]
up *args: && status
    #!/usr/bin/env bash
    set -euo pipefail
    unit_file="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/{{unit}}"
    env_file="${XDG_CONFIG_HOME:-$HOME/.config}/cubrid-gha-monitor/service.env"

    if [[ -f "$unit_file" ]]; then
        if [[ "$#" -eq 0 ]]; then
            if systemctl --user is-active --quiet -- {{quote(unit)}}; then
                exit 0
            fi
            systemctl --user start {{quote(unit)}}
            exit 0
        fi
        declare -A updates=()
        while [[ "$#" -gt 0 ]]; do
            case "$1" in
                --host)
                    [[ "$#" -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
                    updates[MONITOR_HOST]=$2; shift 2 ;;
                --port)
                    [[ "$#" -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
                    updates[MONITOR_PORT]=$2; shift 2 ;;
                --build-capacity)
                    [[ "$#" -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
                    updates[MONITOR_BUILD_CAPACITY]=$2; shift 2 ;;
                --general-capacity)
                    [[ "$#" -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
                    updates[MONITOR_GENERAL_CAPACITY]=$2; shift 2 ;;
                --light-capacity)
                    [[ "$#" -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
                    updates[MONITOR_LIGHT_CAPACITY]=$2; shift 2 ;;
                *)
                    echo "Unsupported option for installed unit: $1" >&2
                    echo "Supported: --host --port --build-capacity --general-capacity --light-capacity" >&2
                    exit 2 ;;
            esac
        done
        mkdir -p -m 700 "$(dirname "$env_file")"
        tmp=$(mktemp)
        if [[ -f "$env_file" ]]; then
            while IFS= read -r line || [[ -n "$line" ]]; do
                key=${line%%=*}
                if [[ -n "$key" && -v updates["$key"] ]]; then
                    continue
                fi
                printf '%s\n' "$line" >> "$tmp"
            done < "$env_file"
        fi
        for key in MONITOR_HOST MONITOR_PORT MONITOR_BUILD_CAPACITY MONITOR_GENERAL_CAPACITY MONITOR_LIGHT_CAPACITY; do
            if [[ -v updates["$key"] ]]; then
                printf '%s=%s\n' "$key" "${updates[$key]}" >> "$tmp"
            fi
        done
        install -m 600 "$tmp" "$env_file"
        rm -f "$tmp"
        systemctl --user restart {{quote(unit)}}
    elif systemctl --user is-active --quiet -- {{quote(unit)}}; then
        printf '%s is already running. Run just down before changing start options.\n' {{quote(unit)}} >&2
    else
        systemd-run --user --quiet --same-dir --collect --unit={{quote(unit)}} \
            --service-type=notify --property=TimeoutStartSec=30s \
            --property=KillSignal=SIGINT --property=TimeoutStopSec=10s \
            --setenv="PATH=$PATH" --setenv="XDG_CACHE_HOME=${XDG_CACHE_HOME-}" \
            --setenv="GH_CONFIG_DIR=${GH_CONFIG_DIR-}" --setenv="XDG_CONFIG_HOME=${XDG_CONFIG_HOME-}" \
            -- /usr/bin/env python3 -m monitor.server "$@"
    fi

# Start or switch the background service to Dev2 IP (192.168.4.2).
up-dev2: (up "--host" dev2_host)

# Start or switch the background service to all IPv4 interfaces.
up-for-all: (up "--host" all_host)

# Restart the installed background service.
restart: && status
    @if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/{{unit}}" ]; then \
        systemctl --user restart {{quote(unit)}}; \
    else \
        printf 'No installed %s found. Run just down && just up (or just install && just up).\n' {{quote(unit)}} >&2; \
        exit 1; \
    fi

# Stop the background service.
down:
    @if output=$(systemctl --user stop -- {{quote(unit)}} 2>&1); then \
        if [ -n "$output" ]; then printf '%s\n' "$output"; fi; \
    else \
        status=$?; \
        if state=$(systemctl --user show --property=LoadState --value -- {{quote(unit)}}) && [ "$state" = not-found ]; then exit 0; fi; \
        printf '%s\n' "$output" >&2; \
        exit "$status"; \
    fi

# Show service status and dashboard URLs.
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

# Show recent service logs.
logs:
    @journalctl --user -u {{quote(unit)}} -n 50 --no-pager

# Run Python unit tests.
test:
    python3 -m unittest discover -s tests -v

# Run headless Playwright smoke tests against demo mode.
smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -z "${PLAYWRIGHT_MODULE:-}" && ! -d node_modules/playwright ]]; then
        fallback="$HOME/.local/share/mermaid-validation/node_modules/playwright"
        if [[ -d "$fallback" ]]; then
            export PLAYWRIGHT_MODULE="$fallback"
        fi
    fi
    node tests/smoke.mjs
