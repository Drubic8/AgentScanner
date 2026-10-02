package io.github.drubic8.asicmonitor

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.unit.dp

@Composable
@OptIn(ExperimentalLayoutApi::class)
internal fun DevicesScreen(state: MonitorState, onScan: () -> Unit, onCancel: () -> Unit,
    onNetworks: () -> Unit, onExport: () -> Unit, onDevice: (Device) -> Unit,
    onCompact: (Boolean) -> Unit, onCommand: (List<Device>) -> Unit, onJournal: () -> Unit) {
    var query by rememberSaveable { mutableStateOf("") }
    val largeText = LocalDensity.current.fontScale > 1.3f
    var selectedKeys by rememberSaveable { mutableStateOf(emptyList<String>()) }
    val filtered = remember(state.devices, query) {
        state.devices.filter { "${it.ip} ${it.model} ${it.firmware} ${it.id}".contains(query.trim(), ignoreCase = true) }
    }
    val selected = filtered.filter { it.selectionKey() in selectedKeys }
    LaunchedEffect(filtered.map { it.selectionKey() }) {
        val visible = filtered.map { it.selectionKey() }.toSet()
        selectedKeys = selectedKeys.filter { it in visible }
    }
    Column(Modifier.fillMaxSize()) {
        // Selection controls remain visible while scrolling through a subnet.
        if (state.devices.isNotEmpty()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val allSelected = selected.isNotEmpty() && selected.size == filtered.size
                TriStateCheckbox(enabled = !state.busy && filtered.isNotEmpty(), state = when {
                    allSelected -> ToggleableState.On
                    selected.isEmpty() -> ToggleableState.Off
                    else -> ToggleableState.Indeterminate
                }, onClick = {
                    selectedKeys = if (selected.size == filtered.size) emptyList() else filtered.map { it.selectionKey() }
                }, modifier = Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp).semantics {
                    contentDescription = if (allSelected) "Снять выбор" else "Выбрать найденные"
                })
                FilledTonalButton(onClick = { onCommand(selected) }, enabled = selected.isNotEmpty() && !state.busy,
                    modifier = Modifier.weight(1f).heightIn(min = 48.dp)) {
                    Icon(Icons.Outlined.Tune, null); Spacer(Modifier.width(6.dp)); Text("Команды · ${selected.size}")
                }
            }
        }
        LazyColumn(Modifier.testTag("device-list"), contentPadding = PaddingValues(14.dp),
            verticalArrangement = Arrangement.spacedBy(if (state.compact) 6.dp else 12.dp)) {
            item {
                Surface(color = Ink, shape = MaterialTheme.shapes.large) {
                    Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (largeText) {
                            Text("${state.devices.size} ASIC", color = Color.White,
                                style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                            Text(state.status, color = Color(0xFFCFD9EB), style = MaterialTheme.typography.bodySmall)
                            Button(onClick = { if (state.busy) onCancel() else { selectedKeys = emptyList(); onScan() } },
                                enabled = state.ready && !(state.stopping && state.busy), modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                                Icon(if (state.busy) Icons.Outlined.Stop else Icons.Outlined.Search, null)
                                Spacer(Modifier.width(6.dp)); Text(if (state.busy) "Стоп" else "Сканировать")
                            }
                        } else Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("${state.devices.size} ASIC", color = Color.White,
                                    style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                                Text(state.status, color = Color(0xFFCFD9EB), style = MaterialTheme.typography.bodySmall)
                            }
                            Button(onClick = { if (state.busy) onCancel() else { selectedKeys = emptyList(); onScan() } },
                                enabled = state.ready && !(state.stopping && state.busy), modifier = Modifier.heightIn(min = 48.dp)) {
                                Icon(if (state.busy) Icons.Outlined.Stop else Icons.Outlined.Search, null)
                                Spacer(Modifier.width(6.dp)); Text(if (state.busy) "Стоп" else "Сканировать")
                            }
                        }
                        val done = if (state.commanding) state.results.size else state.processed
                        val total = if (state.commanding) state.commandTotal else state.total
                        if (total > 0) {
                            LinearProgressIndicator(progress = { (done.toFloat() / total).coerceIn(0f, 1f) },
                                modifier = Modifier.fillMaxWidth(), color = Color(0xFF83ABFF), trackColor = Color(0xFF3C4B63))
                            Text(if (state.commanding) "Команды: $done из $total" else "Проверено $done из $total IP · ошибок: ${state.errors}",
                                color = Color(0xFFCFD9EB), style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
            item {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = onNetworks, modifier = Modifier.weight(1f)) {
                        Icon(Icons.Outlined.Lan, null); Spacer(Modifier.width(6.dp)); Text("Сети: ${state.groups.count { it.selected }}")
                    }
                    IconButton(onClick = onJournal) { Icon(Icons.Outlined.History, "Журнал команд") }
                    IconButton(onClick = onExport, enabled = state.devices.isNotEmpty() && !state.busy) {
                        Icon(Icons.Outlined.FileDownload, "Сохранить CSV")
                    }
                }
            }
            item {
                FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    FilterChip(selected = state.compact, onClick = { onCompact(true) }, label = { Text("Компактно") },
                        leadingIcon = { Icon(Icons.Outlined.ViewList, null, Modifier.size(18.dp)) }, modifier = Modifier.heightIn(min = 48.dp))
                    FilterChip(selected = !state.compact, onClick = { onCompact(false) }, label = { Text("Карточки") },
                        leadingIcon = { Icon(Icons.Outlined.ViewAgenda, null, Modifier.size(18.dp)) }, modifier = Modifier.heightIn(min = 48.dp))
                }
            }
            if (state.devices.isNotEmpty()) item {
                OutlinedTextField(value = query, onValueChange = { query = it }, modifier = Modifier.fillMaxWidth(),
                    label = { Text("IP, модель, прошивка") }, leadingIcon = { Icon(Icons.Outlined.Search, null) }, singleLine = true)
            }
            if (state.devices.isEmpty()) item {
                Column(Modifier.fillMaxWidth().padding(vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(if (state.total == 0) "Ваши ASIC — под рукой" else "Устройства пока не найдены", style = MaterialTheme.typography.titleLarge)
                    Text("Подключитесь к Wi-Fi, Ethernet или VPN площадки. Добавьте IP-адреса во вкладке «Сети».", color = Muted)
                    Text("Нажмите на устройство для просмотра подробностей и управления. Для группы отметьте устройства галочками.", color = Muted)
                }
            }
            items(filtered, key = { it.selectionKey() }, contentType = { if (state.compact) "compact" else "card" }) { device ->
                DeviceItem(device, compact = state.compact, selected = device.selectionKey() in selectedKeys, enabled = !state.busy,
                    onSelect = { checked -> selectedKeys = if (checked) selectedKeys + device.selectionKey() else selectedKeys - device.selectionKey() },
                    onClick = { onDevice(device) })
            }
            if (state.devices.isNotEmpty() && filtered.isEmpty()) item { Text("По этому запросу ничего не найдено") }
        }
    }
}

internal fun deviceStateLabel(device: Device) = if (device.stale) "Устарело" else when (device.state) {
    "running", "mining" -> "Майнинг"
    "stopped", "sleep", "paused" -> "Сон"
    "stopping" -> "Засыпает"
    "starting" -> "Запускается"
    else -> "Неизвестно"
}

@Composable
@OptIn(ExperimentalLayoutApi::class)
internal fun DeviceItem(device: Device, compact: Boolean, selected: Boolean, enabled: Boolean,
    onSelect: (Boolean) -> Unit, onClick: () -> Unit) {
    val statusColor = when {
        device.stale || device.state in setOf("stopping", "starting") -> Amber
        device.state in setOf("running", "mining") -> Positive
        else -> Muted
    }
    ElevatedCard(onClick = onClick, modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.elevatedCardColors(containerColor = if (selected) Color(0xFFE2EBFF) else Color.White),
        elevation = CardDefaults.elevatedCardElevation(if (compact) 0.dp else 1.dp)) {
        Row(Modifier.fillMaxWidth().padding(end = 12.dp, top = if (compact) 8.dp else 16.dp,
            bottom = if (compact) 8.dp else 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = selected, onCheckedChange = onSelect, enabled = enabled,
                modifier = Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp)
                    .semantics { contentDescription = "Выбрать ${device.ip}" })
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(if (compact) 3.dp else 8.dp)) {
                FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(device.ip, color = Blue, style = MaterialTheme.typography.labelLarge)
                    Text(deviceStateLabel(device), color = statusColor, style = MaterialTheme.typography.labelMedium)
                }
                Text(device.model, style = if (compact) MaterialTheme.typography.bodyMedium else MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.SemiBold, maxLines = if (compact) 1 else 3, overflow = TextOverflow.Ellipsis)
                if (!compact) {
                    Text("${device.firmware} ${device.version}", color = Muted, style = MaterialTheme.typography.bodySmall)
                    HorizontalDivider(color = Color(0xFFEBEFF5))
                }
                FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(device.rate, fontWeight = FontWeight.Medium, style = MaterialTheme.typography.bodyMedium)
                    Text(device.temperature, color = Muted, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}
