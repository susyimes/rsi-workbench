# Independent permission-policy test repository

This is an independent Kotlin/JVM teaching fixture. It is not an Android application and cannot prove real-device behavior.

- Read ISSUE.md and FIXTURE_NOTES.md.
- Development may modify ONLY src/BatteryPolicy.kt. Preserve its public types and evaluate signature.
- acceptance/, app/, ISSUE.md, FIXTURE_NOTES.md and this file are protected. Never weaken or alter tests.
- Do not run git commit, switch, reset, clean or publish. The controller records candidates and releases.
- Do not access other repositories, user secrets, networks, or install dependencies.
- The controller runs actual Kotlin compilation and the full acceptance suite; report honestly if you could not run it yourself.
- Return the requested structured JSON. No subagents, no interactive questions.
