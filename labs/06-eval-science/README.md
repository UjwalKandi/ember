# Lab 06: Evaluation science (coming next)

**Status:** outline. To be built after Lab 05.

**Goal:** know how much to trust a number. Turn "model B scored 0.82 vs 0.79" into a statement with uncertainty, and find out *where* models fail.

## Concepts
- **Variance:** sampling temperature, episode seeds and small eval sets make scores noisy. Re-run, and report a mean ± spread.
- **Confidence intervals:** bootstrap over episodes; a difference smaller than the interval isn't a result.
- **Held-out compositions:** `training/gen_data.py` keeps `wildfire_plus_stuck` and `wildfire_in_fog` out of training (`HOLDOUT`), and `training/eval.py` reports `unseen_composition_acc`. This measures generalization vs memorization.
- **Error analysis:** the failure taxonomy (`ok`, `wrong_decision`, `turn_limit`, `bad_tool_calls`, `no_assessment`, `crash/timeout`) plus per-scenario scores show which situations to fix next.
- **Metric choice:** accuracy vs false-alarm rate vs misses. Which error is worse for a wildfire system?

## Planned steps
1. Run the same model 3× with different seeds/temperatures and compute the spread.
2. Add a bootstrap confidence-interval helper to the eval report.
3. Compare in-distribution vs unseen-composition accuracy for base vs SFT.
4. Build a confusion matrix of verdict/cause from `results/*.json` rows.

## Interview questions
- How many eval episodes do you need to detect a 3-point improvement?
- What's the difference between a held-out split and an out-of-distribution test?
