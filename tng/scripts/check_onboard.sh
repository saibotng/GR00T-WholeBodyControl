#!/bin/bash
# Read-only readiness check for running the C++ deploy ON the G1's onboard Jetson.
# No sudo, changes nothing. Runs in the robot's gear_sonic_deploy directory; straight from
# the workstation (the robot's checkout does not need this script):
#
#   ssh g1new 'cd ~/tng/GR00T-WholeBodyControl/gear_sonic_deploy && bash -s' \
#       < tng/scripts/check_onboard.sh

ok()   { echo "  [ok]   $*"; }
warn() { echo "  [warn] $*"; }
bad()  { echo "  [FAIL] $*"; FAIL=1; }
FAIL=0
# run as a file: work in its gear_sonic_deploy dir; via stdin (bash -s): stay in $PWD
if [[ -f "${BASH_SOURCE[0]:-}" ]]; then cd "$(dirname "${BASH_SOURCE[0]}")/../../gear_sonic_deploy" || exit 1; fi
[[ -f deploy.sh ]] || { echo "run from gear_sonic_deploy/ (deploy.sh not in $PWD)"; exit 1; }
# the deploy pane runs `bash -ic`, so take TensorRT_ROOT from ~/.bashrc like it does
TensorRT_ROOT=${TensorRT_ROOT:-$(bash -ic 'printf %s "$TensorRT_ROOT"' 2>/dev/null </dev/null)}

echo "== Platform"
[[ "$(uname -m)" == aarch64 ]] && ok "aarch64" || bad "not aarch64 ($(uname -m)) — run this on the robot"
if [[ -f /etc/nv_tegra_release ]]; then
    rel=$(head -1 /etc/nv_tegra_release)
    [[ "$rel" == *"R36"* ]] && ok "L4T: $rel (JetPack 6)" || bad "L4T: $rel — JetPack 6 required"
else
    bad "/etc/nv_tegra_release missing (not a Jetson?)"
fi
[[ -r /proc/device-tree/model ]] && ok "module: $(tr -d '\0' < /proc/device-tree/model)"
mode=$(nvpmodel -q 2>/dev/null | head -1)
[[ "$mode" == *MAXN* ]] && ok "power mode: $mode" \
    || warn "power mode: ${mode:-unknown} — upstream wants MAXN (sudo nvpmodel -m 0; sudo jetson_clocks)"
free -h | awk '/^Mem:/{print "  [info] RAM: "$2" total, "$7" available"}'
df -h . | awk 'NR==2{print "  [info] disk (repo): "$4" free of "$2}'

echo "== Toolchain"
for t in cmake clang just git rsync; do
    command -v "$t" >/dev/null && ok "$t: $(command -v "$t")" || bad "$t not found (scripts/install_deps.sh)"
done
if [[ -n "${TensorRT_ROOT:-}" && -d "$TensorRT_ROOT" ]]; then
    h=$(find -L "$TensorRT_ROOT" -name NvInferVersion.h 2>/dev/null | head -1)
    # 10.13 headers define NV_TENSORRT_MAJOR via TRT_MAJOR_ENTERPRISE; resolve one level
    v=$(awk '$1=="#define"{d[$2]=$3} END{for(i=0;i<3;i++){k="NV_TENSORRT_" (i==0?"MAJOR":i==1?"MINOR":"PATCH"); x=d[k]; if(x in d)x=d[x]; printf "%s%s", (i?".":""), x}}' "$h" 2>/dev/null)
    [[ "$v" == 10.7* ]] && ok "TensorRT $v at $TensorRT_ROOT" \
        || warn "TensorRT ${v:-?} at $TensorRT_ROOT — upstream requires 10.7 on Jetson"
elif [[ -d "$HOME/TensorRT" ]]; then
    warn "TensorRT_ROOT unset; deploy.sh will fall back to ~/TensorRT"
else
    bad "TensorRT not found (TensorRT_ROOT unset, no ~/TensorRT) — install the 10.7 aarch64 TAR"
fi
ls /usr/local/cuda/targets/aarch64-linux/lib/libcudla.so* >/dev/null 2>&1 \
    || ls /usr/lib/aarch64-linux-gnu/libcudla.so* >/dev/null 2>&1 \
    && ok "libcudla present" \
    || bad "libcudla missing (sudo apt-get install -y nvidia-l4t-dla-compiler libcudla-dev-12-6)"

echo "== Build"
[[ -x target/release/g1_deploy_onnx_ref ]] \
    && ok "binary built: target/release/g1_deploy_onnx_ref ($(date -r target/release/g1_deploy_onnx_ref '+%F %T'))" \
    || warn "not built yet (deploy.sh runs 'just build' on every start)"
echo "  [info] checkout: $(git rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git rev-parse --short HEAD 2>/dev/null), deploy tree $(git rev-parse --short HEAD:gear_sonic_deploy 2>/dev/null)"

echo "== Models (deploy.sh defaults + low_latency)"
for f in policy/release/model_decoder.onnx policy/release/model_encoder.onnx \
         policy/low_latency/model_decoder.onnx policy/low_latency/model_encoder.onnx \
         planner/target_vel/V2/planner_sonic.onnx; do
    [[ -f "$f" ]] && ok "$f" || warn "$f missing (tng/scripts/sync_models_to_robot.sh on the workstation)"
done

echo "== Network"
link=$(ip -4 -br addr | awk '$3 ~ /^192\.168\.123\./{print $1" "$3}')
[[ -n "$link" ]] && ok "robot network: $link" || warn "no 192.168.123.x address (deploy.sh real auto-detects it)"
command -v timedatectl >/dev/null && timedatectl show -p NTPSynchronized --value 2>/dev/null \
    | awk '{print "  [info] NTP synchronized: "$1}'

echo "== Other load"
systemctl is-active --quiet composed_camera_server 2>/dev/null \
    && echo "  [info] composed_camera_server running (shares the CPU with the deploy)"
pgrep -fa g1_deploy_onnx_ref >/dev/null && warn "a g1_deploy_onnx_ref is ALREADY running: $(pgrep -f g1_deploy_onnx_ref | tr '\n' ' ')"

echo
[[ $FAIL == 0 ]] && echo "No blocking problems found." || { echo "Blocking problems found (see FAIL)."; exit 1; }
