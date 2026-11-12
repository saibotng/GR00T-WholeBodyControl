#!/bin/bash
# Standalone PICO whole-body teleop, everything ON THE G1 (no workstation): PICO PC service,
# pico manager and the C++ deploy run on the onboard Orin. The headset connects straight to the
# robot's Wi-Fi IP. A laptop only SSHes in to start/stop. Teleop only: no exporter, no cameras.
#
# Run ON THE ROBOT (alias "teleop", see tng/ONBOARD.md "Standalone teleop"):
#   bash ~/tng/GR00T-WholeBodyControl/tng/scripts/teleop_onboard.sh [--keep-cameras] [--release]
#   bash ~/tng/GR00T-WholeBodyControl/tng/scripts/teleop_onboard.sh --stop   (alias "teleop-stop")
#
# tmux session "teleop": pane 0 = deploy, pane 1 = pico manager, pane 2 = PICO service.
# Detach with Ctrl+b d (keeps running), re-attach with: tmux attach -t teleop
set -u
REPO="$HOME/tng/GR00T-WholeBodyControl"
RS_DIR="$HOME/roboticsservice"       # unpacked roboticsservice_1.0.0.0_arm64.deb (no install needed)
SESSION=teleop
KEEP_CAMERAS=0
POLICY=low_latency
STOP=0
for a in "$@"; do
    case "$a" in
        --keep-cameras) KEEP_CAMERAS=1 ;;
        --release) POLICY=release ;;
        --stop) STOP=1 ;;
        -h|--help) sed -n 2,12p "$0"; exit 0 ;;
        *) echo "unknown option: $a"; exit 1 ;;
    esac
done

die() { echo "ERROR: $*"; exit 1; }

if [[ $STOP == 1 ]]; then
    # 1) the deploy's own software stop ('o' in pane 0), 2) kill whatever is left
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        tmux send-keys -t "$SESSION:0.0" o
        sleep 2
    fi
    pkill -9 -f g1_deploy_onnx_ref && echo "deploy killed" || echo "no deploy running"
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "session '$SESSION' closed"
    pkill -x RoboticsService 2>/dev/null && echo "PICO service stopped"
    pkill -f pico_manager_thread_server 2>/dev/null
    exit 0
fi
[[ -x "$RS_DIR/RoboticsServiceProcess" ]] || die "PICO service missing in $RS_DIR (see tng/ONBOARD.md)"
[[ -f "$REPO/.venv_teleop/bin/activate" ]] || die "$REPO/.venv_teleop missing (bash install_scripts/install_pico.sh)"
[[ -x "$REPO/gear_sonic_deploy/target/release/g1_deploy_onnx_ref" ]] || die "deploy not built (just build)"
command -v tmux >/dev/null || die "tmux missing (sudo apt-get install -y tmux)"
if tmux has-session -t "$SESSION" 2>/dev/null; then
    die "session '$SESSION' already exists: attach with 'tmux attach -t $SESSION' or stop it with 'teleop-stop'"
fi
pgrep -f g1_deploy_onnx_ref >/dev/null && die "a deploy is already running (teleop-stop)"

# The camera server competes with the deploy for CPU; teleop doesn't need it.
if [[ $KEEP_CAMERAS == 0 ]] && systemctl is-active --quiet composed_camera_server; then
    echo "Stopping the camera server for this session (sudo; it starts again at the next boot)..."
    sudo systemctl stop composed_camera_server || echo "  (could not stop it; continuing)"
fi

# Fresh PICO service every session (a stale one keeps dead headset sockets).
pkill -x RoboticsService 2>/dev/null && sleep 1

WIFI_IF=$(nmcli -t -f DEVICE,TYPE dev | awk -F: '$2=="wifi"{print $1; exit}')
WIFI_SSID=$(nmcli -t -f GENERAL.CONNECTION dev show "$WIFI_IF" 2>/dev/null | sed -n 's/^GENERAL.CONNECTION://p')
WIFI_IP=$(ip -4 -o addr show "$WIFI_IF" 2>/dev/null | awk '/inet /{split($4,a,"/"); print a[1]; exit}')

tmux new-session -d -s "$SESSION" -x 220 -y 50
tmux split-window -h -t "$SESSION:0"
tmux split-window -v -t "$SESSION:0.1"
tmux select-pane -t "$SESSION:0.0" -T deploy
tmux select-pane -t "$SESSION:0.1" -T pico-manager
tmux select-pane -t "$SESSION:0.2" -T pico-service

# pane 2: PICO PC service (foreground, so Ctrl+C / kill-session stops it)
tmux send-keys -t "$SESSION:0.2" \
    "cd $RS_DIR && LD_LIBRARY_PATH=$RS_DIR:$RS_DIR/lib:$RS_DIR/SDK/arm64 QT_PLUGIN_PATH=$RS_DIR/plugins/ ./RoboticsServiceProcess" C-m
sleep 3
# pane 1: pico manager (torch on CPU; few threads so it doesn't starve the deploy)
tmux send-keys -t "$SESSION:0.1" \
    "cd $REPO && source .venv_teleop/bin/activate && OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python gear_sonic/scripts/pico_manager_thread_server.py --input-source xrt --manager" C-m
sleep 2
# pane 0: deploy, input from the pico manager on this machine
tmux send-keys -t "$SESSION:0.0" \
    "cd $REPO/gear_sonic_deploy && ./deploy.sh --input-type zmq_manager --zmq-host localhost --cp policy/$POLICY/model --obs-config policy/$POLICY/observation_config.yaml real" C-m
tmux select-pane -t "$SESSION:0.0"

cat <<EOF

==============================================================================
 Standalone teleop started (tmux session '$SESSION', policy $POLICY)
 PICO headset: Wi-Fi '${WIFI_SSID:-?}'  ->  XRoboToolkit app, PC IP: ${WIFI_IP:-?}

 Pane 0 (left): deploy. It may ask for the sudo password (jetson_clocks), then
   "Proceed with deployment? [Y/n]" -> Enter once the robot is on the gantry.
 Calibration pose -> A+B+X+Y (start) -> A+X (whole body) ... A+B+X+Y (stop)

 Stop:       PICO A+B+X+Y  |  'O' in pane 0
 Hard stop:  teleop-stop   (kills deploy + session; robot loses its controller)
 Detach: Ctrl+b d (keeps running)   Re-attach: tmux attach -t $SESSION
==============================================================================
EOF
sleep 2
[[ -n "${TMUX:-}" ]] || tmux attach -t "$SESSION"
