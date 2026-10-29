package rsi.lab

enum class Entry { MANAGE, AUTHORIZE }

data class BatteryInput(
    val entry: Entry,
    val xiaomiFamily: Boolean,
    val systemExempt: Boolean?,
    val previouslyAuthorized: Boolean,
    val firstAuthorization: Boolean,
    val ownVpnConnected: Boolean?,
    val backgroundRestricted: Boolean?,
    val oemUnrestricted: Boolean?,
)

data class BatteryDecision(val granted: Boolean, val manualReview: Boolean, val reason: String)

fun evaluate(input: BatteryInput): BatteryDecision {
    // Reproduction: entering a supported Xiaomi management page creates a review.
    val reviewRequired = input.xiaomiFamily
    if (input.oemUnrestricted == false) return BatteryDecision(false, reviewRequired, "oem_restricted")
    if (input.systemExempt == null) return BatteryDecision(false, reviewRequired, "system_unknown")
    val compatibility = input.xiaomiFamily && input.previouslyAuthorized && !input.firstAuthorization &&
        !reviewRequired && input.backgroundRestricted == false && input.ownVpnConnected == true
    val granted = if (input.firstAuthorization || reviewRequired) {
        input.systemExempt == true && (!reviewRequired || input.oemUnrestricted == true)
    } else input.systemExempt == true || compatibility
    return BatteryDecision(granted, reviewRequired && !granted, if (granted) "authorized" else "review_required")
}
