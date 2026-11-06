"""Replay the latent motion tokens of a recorded LeRobot episode into the C++ deploy.

Same wire protocol and same 50 Hz cadence as ``run_vla_inference.py``, but the tokens
come from ``action.motion_token`` in the dataset instead of from a VLA. No policy
server and no cameras are involved.

The point is to see what a token sequence *decodes to*, which makes it the tool for
checking an offline token conversion: replay the same episode from the source dataset
against its own SONIC decoder and from the converted dataset against the target
decoder, and the motion should match.

Intended for MuJoCo sim. The decoder is chosen when the C++ deploy starts, so
switching checkpoints means restarting the deploy (terminal 2), not this script.

The sim's virtual elastic band is ON by default (``ENABLE_ELASTIC_BAND: True``) and
pins the pelvis toward [0, 0, 1] with an upright-orientation term, so the robot can
neither translate nor turn. Any locomotion in the replayed episode is silently
cancelled, so the band has to be off before the episode starts. Either run the sim
with ``--no-enable-elastic-band``, or keep it on for a clean stand-up and use
``--hold-initial-pose`` here: SONIC balances against the initial-pose token while you
release the band with '9' in the MuJoCo window, and the episode starts on ENTER.

Virtual environment setup (run from repo root):
    source .venv_inference/bin/activate

Usage:
    # terminal 1: MuJoCo
    python gear_sonic/scripts/run_sim_loop.py
    # terminal 2: deploy with the checkpoint that matches the dataset
    cd gear_sonic_deploy && ./deploy.sh --input-type zmq_manager --zmq-host localhost \
        --cp policy/low_latency/model \
        --obs-config policy/low_latency/observation_config.yaml sim
    # terminal 3: this script
    python gear_sonic/scripts/replay_tokens.py \
        --dataset-path /path/to/g1_dribble_low_2 --episode 3

Control flow mirrors the deploy pane keys: start control (k) -> stand in PLANNER mode ->
switch to POSE mode with the initial-pose token (i) -> blend into the episode's first
token -> stream. Ctrl+C stops the control loop on the way out.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time

import numpy as np
import pandas as pd
import tyro
import zmq

from gear_sonic.utils.inference.initial_poses import LATENT_INITIAL_MOTION_TOKEN
from gear_sonic.utils.teleop.zmq.zmq_planner_sender import (
    build_command_message,
    pack_latent_action_message,
)

TOKEN_COLUMN = "action.motion_token"
LEFT_HAND_COLUMN = "teleop.left_hand_joints"
RIGHT_HAND_COLUMN = "teleop.right_hand_joints"


def print_green(x):
    print(f"\033[92m{x}\033[0m")


@dataclass
class ReplayConfig:
    """CLI config for the dataset token replay."""

    dataset_path: str
    """Root of the LeRobot dataset (the directory holding meta/ and data/)."""

    episode: int = 0
    """Episode index to replay."""

    start_frame: int = 0
    """First frame of the episode to send."""

    end_frame: int = -1
    """Last frame (exclusive) to send. -1 means to the end of the episode."""

    fps: float = 0.0
    """Streaming rate (Hz). 0 means take it from meta/info.json (the recording rate).
    Anything other than the recording rate changes both playback speed and the
    velocity history the decoder observes."""

    loop: bool = False
    """Replay the episode repeatedly until Ctrl+C."""

    send_hands: bool = True
    """Send the recorded hand joints alongside each token."""

    # ZMQ: action output (latent tokens to the C++ control loop)
    action_zmq_host: str = "localhost"
    """ZMQ host to bind the action PUB socket on."""

    action_zmq_port: int = 5556
    """ZMQ port for action output."""

    # Startup sequence
    auto_start: bool = True
    """Send the start/stand/POSE-mode commands. Disable to drive the deploy by hand
    and have this script only stream tokens."""

    stand_settle: float = 4.0
    """Seconds to wait after starting the control loop, for the robot to stand."""

    initial_pose_settle: float = 1.5
    """Seconds to hold the initial-pose token before blending into the episode."""

    hold_initial_pose: bool = False
    """Keep streaming the initial-pose token until ENTER is pressed, instead of holding
    it for --initial-pose-settle seconds. Use this to let SONIC take over balancing
    while the elastic band is still on, release the band with '9' in the MuJoCo window,
    check the robot stands by itself, and only then start the episode."""

    blend_duration: float = 0.5
    """Seconds to linearly blend from the initial-pose token into the episode's first
    token. 0 snaps straight to it."""

    yes: bool = False
    """Skip the confirmation prompt."""


def _load_episode(config: ReplayConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Read one episode's tokens (and hand joints) plus the dataset's recording rate."""
    root = Path(config.dataset_path).expanduser().resolve()

    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        raise SystemExit(f"ERROR: {info_path} not found — is --dataset-path a LeRobot root?")
    info = json.loads(info_path.read_text())
    dataset_fps = float(info["fps"])

    # Resolve the episode parquet through the dataset's own path template.
    template = info.get("data_path", "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet")
    chunks_size = int(info.get("chunks_size", 1000))
    parquet = root / template.format(
        episode_chunk=config.episode // chunks_size, episode_index=config.episode
    )
    if not parquet.is_file():
        raise SystemExit(f"ERROR: episode file not found: {parquet}")

    df = pd.read_parquet(parquet)
    if TOKEN_COLUMN not in df.columns:
        raise SystemExit(f"ERROR: {parquet} has no '{TOKEN_COLUMN}' column")

    end = len(df) if config.end_frame < 0 else min(config.end_frame, len(df))
    if not 0 <= config.start_frame < end:
        raise SystemExit(
            f"ERROR: empty frame range [{config.start_frame}, {end}) for an episode of {len(df)} frames"
        )
    df = df.iloc[config.start_frame:end]

    tokens = np.stack(df[TOKEN_COLUMN].to_numpy()).astype(np.float32)
    if tokens.shape[1] != LATENT_INITIAL_MOTION_TOKEN.shape[-1]:
        raise SystemExit(
            f"ERROR: dataset tokens are {tokens.shape[1]}-d but the initial-pose token is "
            f"{LATENT_INITIAL_MOTION_TOKEN.shape[-1]}-d — mismatched checkpoint?"
        )

    if config.send_hands and LEFT_HAND_COLUMN in df.columns and RIGHT_HAND_COLUMN in df.columns:
        left = np.stack(df[LEFT_HAND_COLUMN].to_numpy()).astype(np.float32)
        right = np.stack(df[RIGHT_HAND_COLUMN].to_numpy()).astype(np.float32)
    else:
        if config.send_hands:
            print(f"Note: no hand columns in {parquet.name} — sending zeros.")
        left = np.zeros((len(tokens), 7), dtype=np.float32)
        right = np.zeros((len(tokens), 7), dtype=np.float32)

    return tokens, left, right, dataset_fps


def _describe_conversion(dataset_path: str) -> None:
    """Print the token_conversion provenance block, if the dataset carries one."""
    info_path = Path(dataset_path).expanduser().resolve() / "meta" / "info.json"
    try:
        conversion = json.loads(info_path.read_text()).get("token_conversion")
    except (OSError, ValueError):
        return
    if conversion:
        print("  Tokens:          CONVERTED")
        for key, value in conversion.items():
            print(f"    {key}: {value}")
    else:
        print("  Tokens:          as recorded")


def main(config: ReplayConfig):
    tokens, left_hands, right_hands, dataset_fps = _load_episode(config)
    fps = config.fps if config.fps > 0 else dataset_fps

    print("=" * 60)
    print("  SONIC dataset token replay")
    print("=" * 60)
    print(f"  Dataset:         {config.dataset_path}")
    print(f"  Episode:         {config.episode}  ({len(tokens)} frames)")
    _describe_conversion(config.dataset_path)
    print(f"  Rate:            {fps:g} Hz  (dataset: {dataset_fps:g} Hz)")
    if abs(fps - dataset_fps) > 1e-6:
        print("    WARNING: replaying off the recording rate — motion speed and the")
        print("             decoder's velocity history will both be wrong.")
    print(f"  Duration:        {len(tokens) / fps:.1f} s{' (looping)' if config.loop else ''}")
    print(f"  Hands:           {'recorded' if config.send_hands else 'zeros'}")
    print(f"  Action socket:   tcp://{config.action_zmq_host}:{config.action_zmq_port}")
    print(
        f"  Initial token:   {os.environ.get('SONIC_INITIAL_TOKEN', 'low_latency')}"
        "  (SONIC_INITIAL_TOKEN — must match the deploy's checkpoint)"
    )
    print(f"  Auto start:      {'yes' if config.auto_start else 'no (drive the deploy yourself)'}")
    if config.hold_initial_pose:
        print("  Band handover:   yes — holds the initial-pose token until ENTER")
    print("=" * 60)
    print("The decoder is whichever checkpoint the C++ deploy was started with — it must")
    print("match the latent space these tokens live in, or the motion is meaningless.")

    if not config.yes:
        try:
            if input("Start replay? [y/N] ").strip().lower() not in ("y", "yes"):
                raise SystemExit("Aborted.")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("\nAborted.")

    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind(f"tcp://{config.action_zmq_host}:{config.action_zmq_port}")
    time.sleep(0.2)  # let subscribers attach before the first send
    print_green(f"ZMQ action socket bound to tcp://{config.action_zmq_host}:{config.action_zmq_port}")

    def send_token(token: np.ndarray, frame_index: int, left: np.ndarray, right: np.ndarray):
        socket.send(
            pack_latent_action_message(
                motion_token=token,
                frame_index=np.array([frame_index], dtype=np.int64),
                left_hand_joints=left,
                right_hand_joints=right,
            )
        )

    initial_token = np.asarray(LATENT_INITIAL_MOTION_TOKEN, dtype=np.float32).reshape(-1)
    zero_hands = np.zeros(7, dtype=np.float32)
    control_started = False

    try:
        if config.auto_start:
            # 'k': start the control loop in PLANNER mode so the robot stands up.
            socket.send(build_command_message(start=True, stop=False, planner=True))
            control_started = True
            print_green(f"Started control loop (PLANNER) — standing for {config.stand_settle:g}s")
            time.sleep(config.stand_settle)

            # 'i': switch to POSE mode and hold the initial-pose token.
            socket.send(build_command_message(start=True, stop=False, planner=False))
            time.sleep(0.05)
            print_green("Switched to POSE mode")

        frame_index = 0
        step_period = 1.0 / fps

        if config.hold_initial_pose:
            # The token has to keep flowing throughout: a gap over ~500 ms trips the
            # deploy's staleness hold, so ENTER is read on a side thread.
            print_green("Holding the initial-pose token — SONIC is balancing the robot now.")
            print("  Release the elastic band with '9' in the MuJoCo window, check that")
            print("  the robot keeps standing, then press ENTER here to start the episode.")
            go = threading.Event()
            threading.Thread(
                target=lambda: (input(), go.set()), daemon=True
            ).start()
            while not go.is_set():
                send_token(initial_token, frame_index, zero_hands, zero_hands)
                frame_index += 1
                time.sleep(step_period)
        else:
            for _ in range(int(config.initial_pose_settle * fps)):
                send_token(initial_token, frame_index, zero_hands, zero_hands)
                frame_index += 1
                time.sleep(step_period)

        # Ease into the episode instead of snapping from the standing pose.
        blend_steps = int(config.blend_duration * fps)
        for step in range(blend_steps):
            ratio = (step + 1) / blend_steps
            send_token(
                (1.0 - ratio) * initial_token + ratio * tokens[0],
                frame_index,
                zero_hands,
                zero_hands,
            )
            frame_index += 1
            time.sleep(step_period)
        if blend_steps:
            print_green(f"Blended into token[0] over {config.blend_duration:g}s")

        pass_number = 0
        while True:
            pass_number += 1
            print_green(f"Replaying episode {config.episode} (pass {pass_number})")
            t_next = time.monotonic()
            for i, token in enumerate(tokens):
                send_token(token, frame_index, left_hands[i], right_hands[i])
                frame_index += 1

                # Absolute schedule: sleeping a fixed period would drift, and a gap
                # over ~500 ms trips the deploy's token-staleness hold.
                t_next += step_period
                remaining = t_next - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                elif remaining < -0.1:
                    print(f"  behind schedule by {-remaining * 1000:.0f} ms at frame {i}")
                    t_next = time.monotonic()

            print(f"Episode done ({len(tokens)} frames, {len(tokens) / fps:.1f} s)")
            if not config.loop:
                break

        # Park on the initial pose rather than freezing on the last token.
        for _ in range(int(1.0 * fps)):
            send_token(initial_token, frame_index, zero_hands, zero_hands)
            frame_index += 1
            time.sleep(step_period)
        print_green("Parked on the initial-pose token")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if control_started:
            socket.send(build_command_message(start=False, stop=True, planner=False))
            time.sleep(0.1)
            print_green("Stopped control loop")
        socket.close()
        context.term()


if __name__ == "__main__":
    main(tyro.cli(ReplayConfig))
