package io.github.drubic8.asicmonitor

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONObject

/** Shared catalog with the desktop app; identifiers and user/device data are never translated. */
internal object AppLanguage {
    var language by mutableStateOf("ru")
        private set
    private var english: Map<String, String> = emptyMap()
    private var russian: Map<String, String> = emptyMap()

    fun initialize(context: Context) {
        val catalog = JSONObject(context.assets.open("en.json").bufferedReader(Charsets.UTF_8).use { it.readText() })
        english = catalog.keys().asSequence().associateWith { catalog.getString(it) }
        russian = english.entries.associate { it.value to it.key }
        set(context.getSharedPreferences("monitor", 0).getString("language", "ru") ?: "ru")
    }

    fun set(value: String) { language = if (value == "en") "en" else "ru" }

    fun text(source: String, values: Array<out Pair<String, Any?>>): String {
        val original = russian[source] ?: source
        var result = if (language == "en") english[original] ?: original else original
        // A single substitution pass keeps user-supplied braces and dollar signs literal.
        val arguments = values.toMap()
        result = Regex("\\{(p\\d+)\\}").replace(result) { match ->
            if (arguments.containsKey(match.groupValues[1])) arguments[match.groupValues[1]].toString() else match.value
        }
        return result
    }
}

internal fun tr(source: String, vararg values: Pair<String, Any?>): String = AppLanguage.text(source, values)
