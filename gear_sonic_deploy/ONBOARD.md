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
5. In the robot checkout (`~/tng/GR00T-WholeBodyControl`, branch `tng`): `cd gear_sonic_deploy && bash scripts/install_deps.sh`.

## Before each session (workstation)

```bash
# models: copies *.onnx (never *.trt) into the robot checkout
gear_sonic_deploy/scripts/sync_models_to_robot.sh g1new
# readiness (read-only)
ssh g1new 'cd ~/tng/GR00T-WholeBodyControl/gear_sonic_deploy && bash -s' < gear_sonic_deploy/scripts/check_onboard.sh
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
