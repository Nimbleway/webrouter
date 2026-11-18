---
name: running-webrouter-benchmarks
description: Use when you run, resume or combine web-router's provider or agent benchmarks (benchmarks/providers, benchmarks/agent) in the webrouter repository.
---
Check a vendor's rate tier before a run; on a free tier, run the scan slowly, throttled to its limit.
The agent benchmark has no resume, and one dropped connection, such as the laptop sleeping, ends the whole run: say so before a long run, and finish an interrupted one by running `benchmarks/agent/run.py --exclude <interrupted run folder>`, which asks only the questions that run did not finish.
A newer scan whose run.json records `withheld` will not `--combine` with an older run that lacks it; compare those two by hand.
