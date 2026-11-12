# Models and tiers

Shipped files never name a vendor model. Roles declare a capability tier
(`low`, `mid` or `high`) and agents use `model: inherit`. Evals pin the model
through the repository variable `vars.EVAL_MODEL`, so the workflow file stays
neutral.

Words that only look like model names are fine: a magnum opus, a sonnet of
fourteen lines, a haiku about deploys, a llama at the farm, the mistral wind,
and the gemini constellation.
