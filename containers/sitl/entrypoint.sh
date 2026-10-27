#!/bin/sh
# MRRSimulator SITL entrypoint: turn environment variables into arducopter flags.
# The env contract is documented in containers/README.md. Every variable has a default,
# so `podman run --network host mrrsim-sitl` on its own starts instance 0.
set -eu

: "${INSTANCE:=0}"                    # -I: shifts every SITL port by 10*INSTANCE
: "${SYSID:=$((INSTANCE + 1))}"       # MAVLink system id
: "${MODEL:=airsim-copter}"           # SITL physics model (external AirSim backend)
: "${HOME_LOC:=37.9490953,-91.7848293,0,0}"   # lat,lon,alt,heading; match the scene's home
: "${SPEEDUP:=1}"
: "${SIM_ADDRESS:=127.0.0.1}"         # where the simulator (Unreal) lives
: "${GCS_OUT:=127.0.0.1:14550}"       # SERIAL0 udpclient target (QGC / Mission Planner)
: "${DEFAULTS:=/opt/ardupilot/params/copter.parm,/opt/ardupilot/params/airsim-quadX.parm}"
: "${WIPE:=1}"                        # 1 = wipe eeprom each start so DEFAULTS always apply
: "${SYNTHETIC_CLOCK:=0}"             # -S: a no-op in 4.7.0 ("stale command-line parameter"); kept for older refs
: "${SIM_PORT_IN:=}"                  # optional overrides; normally -I applies the +10*i offset
: "${SIM_PORT_OUT:=}"
: "${EXTRA_ARGS:=}"                   # space separated; appended verbatim
: "${PARAMS:=}"                       # NAME=VALUE,NAME=VALUE: ArduPilot parameter overrides

# Parameter overrides become one more defaults file, loaded after the frame's own defaults, so they
# win. (SITL is started with -w, so they apply on every start.)
if [ -n "$PARAMS" ]; then
    mkdir -p /work
    echo "$PARAMS" | tr ',' '\n' | sed 's/=/ /' > /work/mrrsim-overrides.parm
    DEFAULTS="$DEFAULTS,/work/mrrsim-overrides.parm"
fi

# SERIAL0 must be a client-style link: the default tcp:5760 server BLOCKS startup until
# something connects. SERIAL1 (tcp 5762+10i) and SERIAL2 (tcp 5763+10i) are listening
# servers by default and do not wait.
set -- \
    -I "$INSTANCE" \
    --model="$MODEL" \
    --home="$HOME_LOC" \
    --speedup="$SPEEDUP" \
    --sim-address="$SIM_ADDRESS" \
    --defaults="$DEFAULTS" \
    --sysid="$SYSID" \
    --serial0="udpclient:$GCS_OUT"

if [ "$WIPE" = "1" ]; then set -- "$@" -w; fi
if [ "$SYNTHETIC_CLOCK" = "1" ]; then set -- "$@" -S; fi
if [ -n "$SIM_PORT_IN" ]; then set -- "$@" --sim-port-in="$SIM_PORT_IN"; fi
if [ -n "$SIM_PORT_OUT" ]; then set -- "$@" --sim-port-out="$SIM_PORT_OUT"; fi
if [ -n "$EXTRA_ARGS" ]; then
    # Unquoted on purpose: EXTRA_ARGS is a space-separated list of flags.
    # shellcheck disable=SC2086
    set -- "$@" $EXTRA_ARGS
fi

# A wrong filename here would otherwise surface as a confusing SITL startup problem.
for f in $(echo "$DEFAULTS" | tr ',' ' '); do
    if [ ! -f "$f" ]; then
        echo "mrrsim-sitl: defaults file not found: $f" >&2
        echo "mrrsim-sitl: available: $(ls /opt/ardupilot/params | grep -i -E 'airsim|^copter\.parm' | tr '\n' ' ')" >&2
        exit 1
    fi
done

mkdir -p /work && cd /work
echo "mrrsim-sitl: ardupilot $(cat /opt/ardupilot/COMMIT 2>/dev/null || echo unknown)"
echo "mrrsim-sitl: arducopter $*"
exec /opt/ardupilot/arducopter "$@"
