# Standalone fixture and limits

This is a small Kotlin/JVM teaching fixture for exercising the development controller.
It models a distinction between viewing permission status (MANAGE) and explicitly
performing authorization (AUTHORIZE), with live system state and optional OEM evidence.

The initial policy intentionally contains a regression. The fixed acceptance suite
describes the intended behavior before the development role starts. A separate,
explicitly logged fault injection can exercise the repair loop after a passing candidate.

These simplified rules are for the fixture only. They are not Android framework code,
device measurements, or a production permissions specification. Passing the tests does
not establish real-device correctness. A production adapter needs its own reviewed
contract, build commands, allowed changes, and runtime acceptance.
