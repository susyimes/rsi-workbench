package rsi.lab

private fun nullableBoolean(value: String): Boolean? = when(value) {
    "true" -> true
    "false" -> false
    "null" -> null
    else -> error("Expected true, false or null")
}

fun main(args: Array<String>) {
    require(args.size == 8)
    val result = evaluate(BatteryInput(Entry.valueOf(args[0]),args[1].toBooleanStrict(),
        nullableBoolean(args[2]),args[3].toBooleanStrict(),args[4].toBooleanStrict(),
        nullableBoolean(args[5]),nullableBoolean(args[6]),nullableBoolean(args[7])))
    require(result.reason.matches(Regex("[a-z_]+")))
    println("{\"granted\":${result.granted},\"manual_review\":${result.manualReview},\"reason\":\"${result.reason}\"}")
}
