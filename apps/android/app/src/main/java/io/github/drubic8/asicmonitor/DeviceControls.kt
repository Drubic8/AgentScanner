package io.github.drubic8.asicmonitor

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

internal val commandNames = linkedMapOf(
    "identify_on" to "Включить подсветку", "identify_off" to "Выключить подсветку",
    "identify_toggle" to "Переключить подсветку", "mining_stop" to "Сон / остановить майнинг",
    "mining_start" to "Пробудить / возобновить майнинг", "low" to "Режим Low Power",
    "normal_power" to "Обычный режим питания", "hem" to "Режим HEM", "reboot" to "Перезагрузить")

internal fun Device.selectionKey() = "$ip/$id"
internal fun Device.supports(action: String) = !stale && action in actions

internal fun commandWarning(devices: List<Device>, action: String): String {
    val available = devices.count { it.supports(action) }
    val skipped = devices.size - available
    return buildString {
        append("Устройств: ${devices.size}. Поддерживают команду: $available.")
        if (skipped > 0) append("\nБудут пропущены: $skipped — нет поддержки или данные устарели.")
        if (action == "mining_start" && devices.any { it.supports(action) && it.profile == "canaan.avalon" })
            append("\nAvalon пробуждается через перезагрузку. Майнинг возобновится после запуска устройства.")
        if (action == "reboot") append("\nМайнинг прервётся на время перезагрузки.")
        append("\nКоманда отправляется один раз. Полный разгон ASIC может занять несколько минут.")
    }
}

internal fun resultLabel(status: String) = when (status) {
    "succeeded" -> "Подтверждено"
    "unconfirmed" -> "Не подтверждено"
    "unsupported" -> "Не поддерживается"
    "skipped" -> "Пропущено"
    "cancelled" -> "Отменено"
    "failed" -> "Ошибка"
    else -> status
}

@Composable
internal fun DeviceDetails(device: Device, busy: Boolean, onCommand: (String) -> Unit, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(device.model) }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(device.ip, style = MaterialTheme.typography.titleMedium, color = Blue)
            Text("${device.firmware} ${device.version}")
            Text("Хешрейт: ${device.rate}\nТемпература: ${device.temperature}")
            Text("Обновлено (UTC): ${device.observed}", style = MaterialTheme.typography.bodySmall)
            if (device.stale) Text("Данные устарели. Повторите сканирование.", color = Amber)
            HorizontalDivider()
            Text("Управление", fontWeight = FontWeight.Bold)
            val available = commandNames.filterKeys { it in device.actions }
            if (available.isEmpty()) Text("Для этого интерфейса команды пока не подтверждены.")
            available.forEach { (action, label) ->
                OutlinedButton(onClick = { onCommand(action) }, enabled = !busy && !device.stale,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) { Text(label) }
            }
            Text("Команды определяются по совместимости API устройства. Если нужного режима нет в списке, его поддержка не подтверждена.",
                style = MaterialTheme.typography.bodySmall, color = Muted)
        }
    }, confirmButton = { TextButton(onClick = onDismiss) { Text("Закрыть") } })
}

@Composable
internal fun CommandPicker(devices: List<Device>, onCommand: (String) -> Unit, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Управление · ${devices.size}") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Команда применяется к выбранным устройствам с поддерживаемым API.", color = Muted)
            commandNames.filterKeys { action -> devices.any { it.supports(action) } }.forEach { (action, label) ->
                OutlinedButton(onClick = { onCommand(action) }, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                    Text("$label · ${devices.count { it.supports(action) }}/${devices.size}")
                }
            }
            if (devices.none { device -> commandNames.keys.any { device.supports(it) } })
                Text("Доступных команд нет. Обновите сканирование и проверьте доступ к ASIC.")
        }
    }, confirmButton = { TextButton(onClick = onDismiss) { Text("Закрыть") } })
}

@Composable
internal fun CommandConfirmation(devices: List<Device>, action: String, enabled: Boolean,
    onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(commandNames[action].orEmpty()) }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(devices.take(6).joinToString("\n") { "${it.ip} · ${it.model}" })
            if (devices.size > 6) Text("И ещё ${devices.size - 6} устройств", color = Muted)
            Text(commandWarning(devices, action))
        }
    }, confirmButton = { Button(enabled = enabled, onClick = onConfirm) { Text("Отправить") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } })
}

@Composable
internal fun CommandJournal(results: List<DeviceCommandResult>, busy: Boolean, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text("Журнал команд") }, text = {
        // Lazy layout keeps large group results bounded; each outcome belongs to one IP.
        androidx.compose.foundation.lazy.LazyColumn(Modifier.heightIn(max = 420.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (results.isEmpty()) item { Text(if (busy) "Ожидаем результаты…" else "Команды ещё не отправлялись") }
            items(results.size, key = { results[it].ip }) { index ->
                val result = results[index]
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(result.ip, color = Blue, fontWeight = FontWeight.SemiBold)
                    Text("${commandNames[result.action]} · ${resultLabel(result.status)}", fontWeight = FontWeight.Medium)
                    Text(result.message, style = MaterialTheme.typography.bodySmall)
                    HorizontalDivider()
                }
            }
        }
    }, confirmButton = { TextButton(onClick = onDismiss) { Text("Закрыть") } })
}
