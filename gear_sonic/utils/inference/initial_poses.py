"""Default initial poses for VLA inference.

These arrays are sent to the C++ control loop when the user presses 'i'
to move the robot to a known starting configuration before inference begins.

WARNING: The initial motion token below is specific to the SONIC checkpoint used
during training. Different SONIC checkpoints encode different latent spaces, so
this token will produce a different (and likely incorrect) pose if you switch to
a different SONIC checkpoint. When changing the SONIC checkpoint, you MUST update
LATENT_INITIAL_MOTION_TOKEN to a value that corresponds to a known safe standing
pose in the new checkpoint's latent space.
"""

import numpy as np

# 64-dim motion token for a stable standing pose.
# CHECKPOINT-SPECIFIC: this value must be updated if the SONIC checkpoint changes.
# Taken from lerobot_g1_wbc_kick_football_1 episode 0, frame 0 (action.motion_token)
# — the standing start pose of the kick demos, in the low_latency checkpoint's
# latent space.
LATENT_INITIAL_MOTION_TOKEN = np.array(
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
