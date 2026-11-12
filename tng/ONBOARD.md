# C++ deploy on the robot (onboard Jetson)

`launch_data_collection.py` and `launch_inference.py` take `--deploy-host <ssh-host>`. With it,
only the C++ deploy runs on the G1's onboard Orin. Pane 0 is then `ssh -t <host> … ./deploy.sh`.
Everything else stays on the workstation: Pico teleop, the VLA client and policy server, the data
exporter (datasets are written on the workstation) and the camera viewer.

```
workstation                                   robot (Orin)
  pico manager / VLA client  --:5556 pose/tokens-->  g1_deploy_onnx_ref  --DDS-->  G1
  data exporter, VLA client  <--:5557 state-------   (binds *:5557)
```

The launcher fills in the hosts. The deploy gets `--zmq-host <workstation IP towards the robot>`,
and exporter/clients get `--state-zmq-host <robot IP>`. The GR00T client binds its token PUB on
`*`. The robot must be able to reach the workstation on TCP 5556 (firewall).

## One-time robot setup (on the robot; needs sudo and the robot's WiFi for internet)

Upstream reference: `docs/source/getting_started/installation_deploy.md`, `docs/source/references/jetpack6.md`.

1. JetPack 6 (both G1 units: L4T R36.4.3, already fine).
2. `sudo nvpmodel -m 0 && sudo jetson_clocks` (MAXN).
3. `sudo apt-get install -y nvidia-l4t-dla-compiler libcudla-dev-12-6`
4. TensorRT **10.7** aarch64 TAR (JetPack 6) → `~/TensorRT`, and `export TensorRT_ROOT=$HOME/TensorRT`
   in `~/.bashrc` (the deploy pane runs `bash -ic`, so `.bashrc` is sourced).
5. Real LFS files in the robot checkout (`~/tng/GR00T-WholeBodyControl`, branch `tng`):
   `git lfs pull --include="gear_sonic_deploy/**"`. Check that
   `gear_sonic_deploy/thirdparty/unitree_sdk2/lib/aarch64/libunitree_sdk2.a` is ~28 MB, not
   133 bytes; with placeholders the build fails at link time (`file format not recognized`).
   Without internet on the robot, copy them from the workstation:
   `docs/WIRELESS_TELEOP_SETUP.md` B3 in the workspace.
6. In the robot checkout: `cd gear_sonic_deploy && bash scripts/install_deps.sh`.

## Before each session (workstation)

```bash
# models: copies *.onnx (never *.trt) into the robot checkout
tng/scripts/sync_models_to_robot.sh g1new
# readiness (read-only)
ssh g1new 'cd ~/tng/GR00T-WholeBodyControl/gear_sonic_deploy && bash -s' < tng/scripts/check_onboard.sh
# preflight + printed pane commands, starts nothing
python gear_sonic/scripts/launch_data_collection.py --deploy-host g1new --dry-run
```

The preflight runs on every launch with `--deploy-host`:
- It compares the decoder, encoder, obs config and planner on the workstation and the robot by
  sha256 and refuses to start on any difference. This is the decoder trap: the robot's copy is
  the one that runs.
- It warns if the robot's `gear_sonic_deploy` git tree differs from the workstation's, because
  the robot builds and runs its own checkout.

The first deploy start on the Orin builds its TensorRT engines (`*.trt` next to each ONNX). The
~770 MB planner alone may take many minutes; later starts reuse the cache.

## Safety: what changes

- **Keyboard e-stop.** `O`, `k`, `i`, `p` and the deploy.sh confirmation are typed into pane 0,
  i.e. through SSH. They only reach the deploy while the link is up.
- **Link drop.** The pane's ssh client gives up after about 3 s. The robot's sshd may take far
  longer to notice, and until then **the deploy keeps running on the robot**. What the deploy then
  does (from the code, not verified on the robot):
  - data collection (`zmq_manager`): planner timeout → IDLE after 1 s without messages;
  - VLA (`zmq_manager` / `zmq`): HOLDS the last token, unless `--deploy-token-stale-fallback-ms`
    (`SONIC_TOKEN_STALE_FALLBACK_MS`) makes it return to the reference motion.
- **Hard stop** from any workstation terminal: `ssh g1new pkill -9 -f g1_deploy_onnx_ref`
  (onboard equivalent of the local `pkill`).
- **Wireless remote.** The deploy can read the Unitree remote (`gamepad_manager` /
  `interface_manager`), which works without the network. The launchers' input types do not
  use it.

## Standalone teleop (no workstation)

PICO whole-body teleop with **everything on the robot**: PICO PC service, pico manager and deploy.
The headset connects straight to the robot over Wi-Fi, and a laptop only SSHes in to start and
stop. Teleop only: no exporter, no cameras (the camera server is stopped for the session).

```
PICO --WiFi :63901--> Orin [PICO service -> pico manager -:5556-> deploy] --DDS--> motors
laptop --WiFi ssh--> Orin (tmux session "teleop")
```

**One-time, on the robot** (on top of the onboard deploy setup above; needs internet, no sudo).
Fetch the real LFS files first. If the clone has placeholders, `install_pico.sh` skips building the
PICO SDK library and the binding fails with `libPXREARobotSDK.so: file format not recognized`:
```bash
cd ~/tng/GR00T-WholeBodyControl && git lfs pull --include="gear_sonic/**,external_dependencies/**"
```
```bash
bash install_scripts/install_pico.sh
```
The script's last step (`isaacteleop[cloudxr]`) is only for `--input-source isaac-teleop` and may
be skipped.
This creates `.venv_teleop` (CPU torch, xrobotoolkit_sdk built for aarch64). Then unpack the ARM
PICO service into the home directory. It runs headless, and no `dpkg -i` is needed:
```bash
mkdir -p /tmp/rs && dpkg-deb -x ~/tng/GR00T-WholeBodyControl/gear_sonic_deploy/thirdparty/roboticsservice_1.0.0.0_arm64.deb /tmp/rs && mv /tmp/rs/opt/apps/roboticsservice ~/roboticsservice && rm -rf /tmp/rs
```
The robot's checkout is on `tng`, which doesn't contain the script until this branch is merged.
Copy it from the workstation, in two commands:
```bash
ssh g1new mkdir -p tng/GR00T-WholeBodyControl/tng/scripts
```
```bash
scp /home/innovation-hacking/luebbet/g1_vla_workspace/external/wbc/tng/scripts/teleop_onboard.sh g1new:tng/GR00T-WholeBodyControl/tng/scripts/
```
Then add the aliases to the robot's `~/.bashrc`:
```bash
alias teleop='bash ~/tng/GR00T-WholeBodyControl/tng/scripts/teleop_onboard.sh'
alias teleop-stop='bash ~/tng/GR00T-WholeBodyControl/tng/scripts/teleop_onboard.sh --stop'
```

**Each session:**
1. Robot and router on. The robot's Wi-Fi must be on the router's network (see
   `docs/WIRELESS_TELEOP_SETUP.md` B9).
2. Laptop: cable to the robot → `ssh g1new` → read the robot's Wi-Fi IP
   (`ip -4 -br addr | grep wl`). Switch the laptop to the same Wi-Fi and `ssh unitree@<that IP>`
   (or an alias), then unplug the cable.
3. On the robot: `teleop`. It prints the IP for the PICO app and attaches to tmux. Pane 0 may ask
   for the sudo password (`jetson_clocks`), then Enter at `Proceed with deployment?`.
4. PICO: same Wi-Fi → XRoboToolkit app → the robot's Wi-Fi IP → WORKING → Calibrate →
   A+B+X+Y → A+X.
5. Stop: PICO **A+B+X+Y** or `O` in pane 0; hard stop `teleop-stop`. Detaching tmux (Ctrl+b d) or
   losing the laptop's SSH does **not** stop anything. The PICO stop keeps working without the
   laptop, because the headset talks to the robot directly.

Harmless in pane 1: `bash: /opt/apps/roboticsservice/runService.sh: No such file or directory`.
The pico manager tries to start the service itself, but pane 2 already runs it.
For predictable boots near the router, prefer its Wi-Fi:
`sudo nmcli con mod G1-data-collection-5G connection.autoconnect-priority 10`.

Options: `teleop --release` (release checkpoint instead of low-latency), `teleop --keep-cameras`.
