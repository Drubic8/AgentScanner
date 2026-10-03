package io.github.drubic8.asicmonitor

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.unit.dp

internal fun matchesDevice(d: Device, query: String, state: String, led: String,
    model: String, firmware: String, errors: Boolean): Boolean =
    "${d.ip} ${d.model} ${d.firmware} ${d.version} ${d.id} ${d.errors}".contains(query.trim(), true) &&
    (model.isEmpty() || d.model == model) && (firmware.isEmpty() || d.firmware == firmware) &&
    (!errors || d.errors.isNotBlank()) &&
    (led.isEmpty() || !d.stale && when (led) { "on" -> d.led == true; "off" -> d.led == false; else -> d.led == null }) &&
    when (state) {
        "running" -> !d.stale && d.state in setOf("running", "mining")
        "stopped" -> !d.stale && d.state in setOf("stopped", "sleep", "paused")
        "transition" -> !d.stale && d.state in setOf("starting", "stopping")
        "unknown" -> !d.stale && d.state == "unknown"
        "stale" -> d.stale
        else -> true
    }

@Composable
internal fun ChoiceField(label: String, value: String, options: Map<String, String>, onChange: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Column {
        Text(label, style = MaterialTheme.typography.labelMedium, color = Muted)
        Box {
            OutlinedButton(onClick = { open = true }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                Text(options[value] ?: value, Modifier.weight(1f)); Icon(Icons.Outlined.ExpandMore, null)
            }
            DropdownMenu(open, { open = false }, modifier = Modifier.heightIn(max = 300.dp)) {
                options.forEach { (key, name) -> DropdownMenuItem(text = { Text(name) }, onClick = { onChange(key); open = false }) }
            }
        }
    }
}

@Composable
internal fun FleetFilters(devices: List<Device>, state: String, led: String, model: String,
    firmware: String, errorsOnly: Boolean, onState: (String) -> Unit, onLed: (String) -> Unit,
    onModel: (String) -> Unit, onFirmware: (String) -> Unit, onErrors: (Boolean) -> Unit,
    onReset: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Фильтры устройств") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            ChoiceField("Состояние", state, linkedMapOf("" to "Все состояния", "running" to "Майнинг", "stopped" to "Сон", "transition" to "Переход", "unknown" to "Неизвестно", "stale" to "Устарело"), onState)
            ChoiceField("LED", led, linkedMapOf("" to "Любой LED", "on" to "Включён", "off" to "Выключен", "unknown" to "Неизвестно"), onLed)
            ChoiceField("Model", model, linkedMapOf("" to "Все модели") + devices.map { it.model }.distinct().sorted().associateWith { it }, onModel)
            ChoiceField("Firmware", firmware, linkedMapOf("" to "Все прошивки") + devices.map { it.firmware }.distinct().sorted().associateWith { it }, onFirmware)
            Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(errorsOnly, onErrors); Text("С ошибками") }
        }
    }, confirmButton = { TextButton(onClick = onDismiss) { Text("Готово") } },
        dismissButton = { TextButton(onClick = onReset) { Text("Сбросить") } })
}

@Composable
internal fun LedIndicator(device: Device) {
    val enabled = if (device.stale) null else device.led
    Icon(if (enabled == true) Icons.Filled.Lightbulb else Icons.Outlined.Lightbulb,
        when (enabled) { true -> "LED включён"; false -> "LED выключен"; null -> "LED неизвестно" },
        tint = if (enabled == true) Amber else Muted, modifier = Modifier.size(18.dp))
}
