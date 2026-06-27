# examples/

Committed fixtures so each lane can build against **real `Trajectory` data** without running the live agent or holding a key.

## `sample_trajectory.json`
A representative successful trajectory for the hero workflow — *dispute the unpaid Acme Corp invoice, add a note, export the receipt* — conforming to `app/schemas.py:Trajectory`.

**For lane B (`compile_skill`):** this is your input. Develop and test against it directly:
```python
from app.trace import load_trajectory
traj = load_trajectory("examples/sample_trajectory.json")
skill = compile_skill(traj)   # -> a Skill whose steps[].target_desc come from each step's intent;
                              #    "Acme Corp" and "duplicate charge" become params
```
Each step's `intent` is the semantic anchor a skill is built from (re-grounded visually at replay).
The variable values (`customer`, `note`) are what `compile_skill` should parameterize.
