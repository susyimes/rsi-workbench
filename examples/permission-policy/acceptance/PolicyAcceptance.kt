package rsi.lab

private data class Case(val name: String, val input: BatteryInput, val granted: Boolean, val review: Boolean)

fun main() {
    val normal = BatteryInput(Entry.MANAGE, true, true, true, false, true, false, null)
    val cases = listOf(
        Case("policy_authorized_management_does_not_reprompt", normal, true, false),
        Case("policy_management_with_vpn_unknown_still_respects_system_exemption", normal.copy(ownVpnConnected=null), true, false),
        Case("explicit_authorization_requires_dual_evidence", normal.copy(entry=Entry.AUTHORIZE), false, true),
        Case("first_authorization_requires_dual_evidence", normal.copy(firstAuthorization=true), false, true),
        Case("known_oem_restriction_overrides_positive_system", normal.copy(oemUnrestricted=false), false, true),
        Case("unknown_system_is_not_granted", normal.copy(systemExempt=null), false, true),
        Case("established_own_vpn_compatibility_retained", normal.copy(systemExempt=false), true, false),
        Case("history_alone_cannot_grant", normal.copy(systemExempt=false,ownVpnConnected=false), false, true),
        Case("unknown_vpn_cannot_grant", normal.copy(systemExempt=false,ownVpnConnected=null), false, true),
        Case("first_authorization_cannot_use_vpn", normal.copy(systemExempt=false,firstAuthorization=true), false, true),
        Case("explicit_authorization_cannot_use_vpn", normal.copy(systemExempt=false,entry=Entry.AUTHORIZE), false, true),
        Case("background_restricted_cannot_use_vpn", normal.copy(systemExempt=false,backgroundRestricted=true), false, true),
        Case("background_unknown_cannot_use_vpn", normal.copy(systemExempt=false,backgroundRestricted=null), false, true),
        Case("no_history_cannot_use_vpn", normal.copy(systemExempt=false,previouslyAuthorized=false), false, true),
        Case("non_xiaomi_granted_stays_granted", normal.copy(xiaomiFamily=false), true, false),
        Case("non_xiaomi_denied_stays_denied", normal.copy(xiaomiFamily=false,systemExempt=false), false, false),
        Case("dual_evidence_authorization_passes", normal.copy(entry=Entry.AUTHORIZE,oemUnrestricted=true), true, false),
        Case("known_oem_restriction_survives_vpn", normal.copy(systemExempt=false,oemUnrestricted=false), false, true),
        Case("unknown_system_even_with_oem_positive_denied", normal.copy(systemExempt=null,oemUnrestricted=true), false, true),
        Case("non_xiaomi_unknown_does_not_invent_manual_review", normal.copy(xiaomiFamily=false,systemExempt=null), false, false)
    )
    var failed = 0
    cases.forEach { case ->
        val actual = evaluate(case.input)
        if (actual.granted == case.granted && actual.manualReview == case.review) {
            println("PASS ${case.name}")
        } else {
            failed++
            println("FAIL ${case.name}: expected granted=${case.granted} review=${case.review}; actual=$actual")
        }
    }
    println("RESULT total=${cases.size} passed=${cases.size-failed} failed=$failed")
    if (failed > 0) kotlin.system.exitProcess(1)
}
