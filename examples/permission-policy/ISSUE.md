# Permission policy: viewing status must not start a new authorization flow

This standalone educational task demonstrates a policy regression with a fixed test contract.
It does not reproduce a private issue or require access to any business repository.

Merely viewing permissions (MANAGE) and explicitly authorizing them (AUTHORIZE) must
follow different rules. No Android framework or real device is involved.

Expected contract:

- Entering MANAGE on an already authorized Redmi/Xiaomi phone must not create a new OEM review requirement when the live system exemption is true and there is no known negative OEM observation.
- AUTHORIZE and first authorization retain strict dual confirmation for supported Xiaomi devices: live system exemption and an observed unrestricted OEM policy.
- An observed OEM restriction remains a negative even when the system exemption is true.
- An unknown system exemption must never be treated as permission granted.
- Established Xiaomi own-VPN compatibility remains available only on ordinary management entry, with authorization history, a connected own VPN, a known unrestricted background state and no known negative OEM observation.
- History alone, unknown/disconnected VPN, background restrictions and first authorization must not create a compatibility grant.
- Non-Xiaomi behavior uses the live system exemption and must not acquire Xiaomi-only manual review requirements.

The acceptance suite is fixed before the development agent runs. The controller may inject one explicitly recorded regression after the first candidate to exercise real failure detection and repair.
