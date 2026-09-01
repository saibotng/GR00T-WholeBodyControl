"""Default initial poses for VLA inference.

These arrays are sent to the C++ control loop when the user presses 'i'
to move the robot to a known starting configuration before inference begins.

WARNING: The initial motion token below is specific to the SONIC checkpoint used
during training. Different SONIC checkpoints encode different latent spaces, so
this token will produce a different (and likely incorrect) pose if you switch to
a different SONIC checkpoint. When changing the SONIC checkpoint, you MUST point
the LATENT_INITIAL_MOTION_TOKEN alias at a token for the new checkpoint — see
the step-by-step instructions above the token definitions below.
"""

import numpy as np

# 64-dim motion tokens for a stable standing pose — ONE PER SONIC CHECKPOINT.
#
# Switching to a different SONIC checkpoint? Do this:
#   1. Record (or reuse) a demo that starts standing at rest, collected UNDER
#      THE NEW CHECKPOINT.
#   2. Copy `action.motion_token` of episode 0, frame 0 from the dataset
#      (e.g. data/chunk-000/episode_000000.parquet).
#   3. Add it below as LATENT_INITIAL_MOTION_TOKEN_<CHECKPOINT> and point the
#      LATENT_INITIAL_MOTION_TOKEN alias at it.
# Using a token from a different checkpoint's latent space produces an
# arbitrary (potentially unsafe) start pose.

# From lerobot_g1_wbc_kick_football_1 episode 0, frame 0 — standing start pose
# in the low_latency checkpoint's (policy/low_latency/model) latent space.
LATENT_INITIAL_MOTION_TOKEN_LOW_LATENCY = np.array(
    [
         0.0000,  0.0000, -0.0625, -0.0625,  0.3125,  0.1875, -0.0625,
         0.0625,  0.0625,  0.0625,  0.0625,  0.0625, -0.3750, -0.0625,
         0.0625,  0.1875, -0.1250,  0.3750,  0.1875, -0.0625,  0.1250,
        -0.0625,  0.0625,  0.1875,  0.0000,  0.0000,  0.0000,  0.1875,
        -0.0625, -0.0625,  0.1250, -0.1250,  0.0625, -0.2500,  0.0625,
         0.1250,  0.0000,  0.0000,  0.3125, -0.0625, -0.0625,  0.1250,
        -0.1875,  0.0625, -0.1875,  0.0000, -0.1250, -0.1875,  0.0000,
         0.0000, -0.0625,  0.0625, -0.0625, -0.0625, -0.1250, -0.1250,
        -0.2500,  0.0000,  0.0625, -0.1875, -0.1250,  0.0000, -0.1875,
        -0.1875,
    ],
    dtype=np.float32,
)

# Standing pose in the original release checkpoint's (policy/release/model,
# HF nvidia/GEAR-SONIC) latent space — the value this file shipped with.
LATENT_INITIAL_MOTION_TOKEN_RELEASE = np.array(
    [
        -0.0625,  0.0000, -0.0625, -0.1250, -0.1875, -0.0625,  0.1875,
         0.2500,  0.1875, -0.1250,  0.0625, -0.0625, -0.2500, -0.2500,
        -0.3125, -0.0625,  0.0000, -0.0625, -0.1250, -0.1875,  0.0000,
        -0.2500,  0.0000, -0.2500, -0.0625,  0.0625,  0.1250, -0.1250,
         0.2500,  0.1875,  0.2500, -0.1250,  0.1250,  0.1875, -0.0625,
         0.0000, -0.1875, -0.1875,  0.2500,  0.0000,  0.0000, -0.1250,
         0.0625,  0.0000, -0.0625, -0.0625,  0.1875, -0.0625,  0.0000,
         0.0625,  0.1250,  0.0625,  0.1250,  0.0625,  0.1250,  0.0000,
         0.1250,  0.1875,  0.0000,  0.0000,  0.0625,  0.0625,  0.1875,
         0.0625,
    ],
    dtype=np.float32,
)

# The token actually used at deploy time. MUST match the SONIC checkpoint
# passed to the controller (--cp / --deploy-checkpoint).
LATENT_INITIAL_MOTION_TOKEN = LATENT_INITIAL_MOTION_TOKEN_LOW_LATENCY
