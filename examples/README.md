# examples/

Committed fixture so each lane can build against **real `Trajectory` data** without running the live agent or holding a key.

## `sample_trajectory.json`
A representative successful trajectory for the hero workflow — *dispute the unpaid Acme Corp invoice, add a note, export the receipt* — conforming to `app/schemas.py:Trajectory`.

**For lane B (`compile_skill`):** this is your input. Develop and test against it directly:
```python
from app.trace import load_trajectory
from app.skill_compiler import compile_skill

traj = load_trajectory("examples/sample_trajectory.json")
skill = compile_skill(traj)   # success-gated: requires traj.success is True (raises ValueError
                              #   otherwise; this fixture has success=true, so it passes). Returns a
                              #   Skill whose every step keeps action + intent (as target_desc) + a
                              #   cached target-crop of the pre-action screenshot (crop_b64). No value
                              #   parameterization: params=[], goal_template="(compiled from trajectory)".
```
Each step's `intent` is the semantic anchor a skill is built from (re-grounded visually at replay).

**Parameterization is not implemented in `compile_skill`.** Lifting the variable values (`customer`,
`note`) into `Skill.params` is a TODO — today `compile_skill` returns `params=[]` and leaves each
step's value baked into its cached `action`/`args`. The parameterizing version exists only in
`stub_compile_skill` (`app/skill_inject_check.py`), a test stub.

> **Caveat — the crop/replay path can't be exercised from this fixture as-is.** The fixture's
> `screenshot_path` values (`traces/fixture-dispute-001_t*.png`) are **not committed** (`traces/` is
> gitignored), so `_crop_b64` reads nothing and every step's `crop_b64` compiles to `None`. The
> compile itself succeeds, but to exercise the crop-based replay you need real pre-action screenshots
> present under `traces/`.
