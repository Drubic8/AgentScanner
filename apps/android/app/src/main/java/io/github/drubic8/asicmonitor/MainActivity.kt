package io.github.drubic8.asicmonitor

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.viewModels
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch

private val Blue = Color(0xFF245EEA)
private val Ink = Color(0xFF17243B)
private val Muted = Color(0xFF526178)
private val Positive = Color(0xFF12694F)
private val Amber = Color(0xFF88520A)
private val Palette = lightColorScheme(primary = Blue, onPrimary = Color.White,
    background = Color(0xFFF3F6FA), surface = Color.White, onSurface = Ink,
    onSurfaceVariant = Muted, secondaryContainer = Color(0xFFE2EBFF), onSecondaryContainer = Blue)

class MainActivity : ComponentActivity() {
    private val model: MonitorViewModel by viewModels()
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { MaterialTheme(colorScheme = Palette) { MonitorApp(model) } }
    }
    override fun onStart() { super.onStart(); model.setForeground(true) }
    override fun onStop() {
        if (!isChangingConfigurations) model.setForeground(false)
        super.onStop()
    }
}

private val commandNames = linkedMapOf("identify_on" to "Включить подсветку", "identify_off" to "Выключить подсветку",
    "reboot" to "Перезагрузить", "mining_stop" to "Остановить майнинг", "mining_start" to "Возобновить майнинг")

@Composable
private fun MonitorApp(model: MonitorViewModel) {
    val state by model.state.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    // Intentionally not rememberSaveable: credentials never enter saved instance state.
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var detail by remember { mutableStateOf<Device?>(null) }
    var confirmation by remember { mutableStateOf<Pair<Device, String>?>(null) }
    val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { uri ->
        if (uri != null) model.export(uri)
    }
    val snackbar = remember { SnackbarHostState() }
    LaunchedEffect(state.notice) {
        if (state.notice.isNotBlank()) { snackbar.showSnackbar(state.notice); model.notice("") }
    }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, bottomBar = {
        NavigationBar(containerColor = Color.White) {
            listOf("Устройства" to Icons.Outlined.Dns, "Сети" to Icons.Outlined.Lan, "Настройки" to Icons.Outlined.Tune)
                .forEachIndexed { index, item ->
                    NavigationBarItem(selected = tab == index, onClick = { tab = index },
                        icon = { Icon(item.second, contentDescription = null) }, label = { Text(item.first) })
                }
        }
    }) { padding ->
        Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.TopCenter) {
        Column(Modifier.widthIn(max = 840.dp).fillMaxSize()) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Memory, null, tint = Blue, modifier = Modifier.size(30.dp))
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text("ASIC Monitor", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("Локальная сеть", color = Muted, style = MaterialTheme.typography.bodySmall)
                }
                Text("ALPHA", style = MaterialTheme.typography.labelSmall, color = Muted)
            }
            when (tab) {
                0 -> DevicesScreen(state, onScan = { model.scan(username, password) }, onCancel = model::cancel,
                    onNetworks = { tab = 1 }, onExport = { exporter.launch("ASIC_Monitor.csv") }, onDevice = { detail = it })
                1 -> NetworksScreen(model, state)
                2 -> SettingsScreen(state, username, password, { username = it }, { password = it }, model::settings)
            }
        }
        }
    }
    detail?.let { device ->
        AlertDialog(onDismissRequest = { detail = null }, title = { Text(device.model) }, text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(device.ip, style = MaterialTheme.typography.titleMedium, color = Blue)
                Text("ID: ${device.id}")
                Text("${device.firmware} ${device.version}")
                Text("Профиль: ${device.profile}")
                Text("Обновлено (UTC): ${device.observed}")
                Text("Хешрейт: ${device.rate}\nТемпература: ${device.temperature}")
                if (device.stale) Text("Данные устарели. Повторите сканирование.", color = Amber)
                HorizontalDivider()
                Text("Управление", fontWeight = FontWeight.Bold)
                commandNames.forEach { (action, label) ->
                    OutlinedButton(onClick = { confirmation = device to action; detail = null },
                        enabled = action in device.actions && !state.busy && !device.stale, modifier = Modifier.fillMaxWidth()) { Text(label) }
                }
                Text("Доступны только команды, проверенные для точной модели и версии прошивки.", style = MaterialTheme.typography.bodySmall)
            }
        }, confirmButton = { TextButton(onClick = { detail = null }) { Text("Закрыть") } })
    }
    confirmation?.let { (device, action) ->
        AlertDialog(onDismissRequest = { confirmation = null }, title = { Text(commandNames[action].orEmpty()) },
            text = { Text("${device.model}\n${device.ip}\nКоманда будет отправлена один раз. После выполнения обновите сканирование.") },
            confirmButton = { Button(onClick = { confirmation = null; model.command(device, action) }) { Text("Отправить") } },
            dismissButton = { TextButton(onClick = { confirmation = null }) { Text("Отмена") } })
    }
}

@Composable
private fun DevicesScreen(state: MonitorState, onScan: () -> Unit, onCancel: () -> Unit,
    onNetworks: () -> Unit, onExport: () -> Unit, onDevice: (Device) -> Unit) {
    var query by rememberSaveable { mutableStateOf("") }
    val filtered = remember(state.devices, query) { state.devices.filter {
        "${it.ip} ${it.model} ${it.firmware} ${it.id}".contains(query.trim(), true)
    } }
    LazyColumn(contentPadding = PaddingValues(start = 20.dp, end = 20.dp, bottom = 20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Surface(color = Ink, shape = MaterialTheme.shapes.large) {
                Column(Modifier.fillMaxWidth().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(state.status, color = Color.White, style = MaterialTheme.typography.titleMedium)
                    Row(verticalAlignment = Alignment.Bottom) {
                        Text("${state.devices.size}", color = Color.White, style = MaterialTheme.typography.displayMedium, fontWeight = FontWeight.SemiBold)
                        Text("  устройств найдено", color = Color(0xFFCFD9EB), modifier = Modifier.padding(bottom = 8.dp))
                    }
                    if (state.total > 0) {
                        LinearProgressIndicator(progress = { state.processed.toFloat() / state.total },
                            modifier = Modifier.fillMaxWidth(), color = Color(0xFF83ABFF), trackColor = Color(0xFF3C4B63))
                        Text("Проверено ${state.processed} из ${state.total} IP · ошибок: ${state.errors}",
                            color = Color(0xFFCFD9EB), style = MaterialTheme.typography.bodySmall)
                    } else Text("Выберите сети и начните поиск ASIC", color = Color(0xFFCFD9EB))
                    Button(onClick = if (state.busy) onCancel else onScan,
                        enabled = state.ready && !(state.stopping && state.busy), modifier = Modifier.fillMaxWidth()) {
                        Icon(if (state.busy) Icons.Outlined.Stop else Icons.Outlined.Search, null)
                        Spacer(Modifier.width(8.dp)); Text(if (state.busy) "Остановить" else "Сканировать")
                    }
                }
            }
        }
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onNetworks, modifier = Modifier.weight(1f)) {
                    Icon(Icons.Outlined.Lan, null); Spacer(Modifier.width(6.dp))
                    Text("Выбрано сетей: ${state.groups.count { it.selected }}")
                }
                IconButton(onClick = onExport, enabled = state.devices.isNotEmpty() && !state.busy) { Icon(Icons.Outlined.FileDownload, "Сохранить CSV") }
            }
        }
        if (state.devices.isNotEmpty()) item {
            OutlinedTextField(value = query, onValueChange = { query = it }, modifier = Modifier.fillMaxWidth(),
                label = { Text("IP, модель, прошивка") }, leadingIcon = { Icon(Icons.Outlined.Search, null) }, singleLine = true)
        }
        if (state.devices.isEmpty()) item {
            Column(Modifier.fillMaxWidth().padding(vertical = 24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(if (state.total == 0) "Ваши ASIC — под рукой" else "Устройства пока не найдены", style = MaterialTheme.typography.titleLarge)
                Text("Телефон должен быть в сети оборудования или подключён через VPN. Добавьте IP-адреса во вкладке «Сети».", color = Muted)
                Text("Для закрытых API укажите логин и пароль в настройках.", color = Muted)
            }
        }
        items(filtered, key = { it.ip }) { device ->
            ElevatedCard(onClick = { onDevice(device) }, colors = CardDefaults.elevatedCardColors(containerColor = Color.White),
                elevation = CardDefaults.elevatedCardElevation(1.dp)) {
                Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(device.ip, color = Blue, fontWeight = FontWeight.Medium)
                        Text(if (device.stale) "Устарело" else when (device.state) {
                            "running", "mining" -> "Майнинг"; "stopped", "sleep", "paused" -> "Остановлен"; else -> "Обнаружен"
                        }, color = if (device.stale) Amber else Positive, style = MaterialTheme.typography.labelMedium)
                    }
                    Text(device.model, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                    Text("${device.firmware} ${device.version}", color = Muted, style = MaterialTheme.typography.bodySmall)
                    HorizontalDivider(color = Color(0xFFEBEFF5))
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(device.rate, fontWeight = FontWeight.SemiBold)
                        Text(device.temperature, color = Muted)
                    }
                }
            }
        }
        if (state.devices.isNotEmpty() && filtered.isEmpty()) item { Text("По этому запросу ничего не найдено") }
    }
}

@Composable
private fun NetworksScreen(model: MonitorViewModel, state: MonitorState) {
    var editing by remember { mutableStateOf<NetworkGroup?>(null) }
    var editorOpen by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<NetworkGroup?>(null) }
    LazyColumn(contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Text("Сети оборудования", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold) }
        item { Text("Сохраните площадки и выбирайте, где искать ASIC. Общий лимит одного сканирования — 4096 IP.", color = Muted) }
        item { Button(onClick = { editing = null; editorOpen = true }, enabled = state.ready && !state.busy) {
            Icon(Icons.Outlined.Add, null); Spacer(Modifier.width(8.dp)); Text("Добавить сеть")
        } }
        items(state.groups, key = { it.id }) { group ->
            Surface(shape = MaterialTheme.shapes.medium, color = Color.White) {
                Column(Modifier.padding(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = group.selected, enabled = !state.busy, onCheckedChange = { model.selectGroup(group.id, it) })
                        Column(Modifier.weight(1f).clickable(enabled = !state.busy) { editing = group; editorOpen = true }) {
                            Text(group.name, fontWeight = FontWeight.SemiBold)
                            Text(group.ranges, color = Muted, style = MaterialTheme.typography.bodySmall)
                        }
                        IconButton(enabled = !state.busy, onClick = { editing = group; editorOpen = true }) { Icon(Icons.Outlined.Edit, "Изменить ${group.name}") }
                        IconButton(enabled = !state.busy, onClick = { removing = group }) { Icon(Icons.Outlined.DeleteOutline, "Удалить ${group.name}") }
                    }
                }
            }
        }
        if (state.groups.isEmpty()) item { Text("Например: «Площадка 1» с диапазоном 192.168.1.1–192.168.1.254. Автоматическое сканирование чужих сетей не запускается.", color = Muted) }
    }
    if (editorOpen) NetworkEditor(editing, model) { editorOpen = false }
    removing?.let { group -> AlertDialog(onDismissRequest = { removing = null }, title = { Text("Удалить сеть?") },
        text = { Text(group.name) }, confirmButton = { TextButton(onClick = { model.deleteGroup(group.id); removing = null }) { Text("Удалить") } },
        dismissButton = { TextButton(onClick = { removing = null }) { Text("Отмена") } }) }
}

@Composable
private fun NetworkEditor(group: NetworkGroup?, model: MonitorViewModel, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf(group?.name.orEmpty()) }
    var ranges by remember { mutableStateOf(group?.ranges.orEmpty()) }
    var error by remember { mutableStateOf("") }
    var saving by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    AlertDialog(onDismissRequest = { if (!saving) onDismiss() }, title = { Text(if (group == null) "Новая сеть" else "Изменить сеть") }, text = {
        Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(name, { name = it }, label = { Text("Название") }, singleLine = true)
            OutlinedTextField(ranges, { ranges = it }, label = { Text("IP-адреса и подсети") }, minLines = 3,
                placeholder = { Text("192.168.1.0/24\n192.168.2.10-30") })
            Text("IPv4, CIDR или диапазон. Несколько записей — с новой строки.", style = MaterialTheme.typography.bodySmall)
            if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        }
    }, confirmButton = { Button(enabled = !saving, onClick = {
        saving = true
        scope.launch {
            try {
                val result = model.saveGroup(group?.id, name, ranges)
                if (result == null) onDismiss() else error = result
            } catch (_: Exception) { error = "Не удалось сохранить сеть" }
            saving = false
        }
    }) { Text("Сохранить") } }, dismissButton = { TextButton(enabled = !saving, onClick = onDismiss) { Text("Отмена") } })
}

@Composable
private fun SettingsScreen(state: MonitorState, username: String, password: String,
    onUsername: (String) -> Unit, onPassword: (String) -> Unit, onSettings: (Int, String) -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text("Настройки", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        Text("Доступ к ASIC", style = MaterialTheme.typography.titleMedium)
        Text("Логин и пароль действуют в текущем сеансе. На диск и в отчёты они не записываются.", color = Muted)
        OutlinedTextField(username, onUsername, enabled = !state.busy, label = { Text("Логин") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(password, onPassword, enabled = !state.busy, label = { Text("Пароль") }, singleLine = true,
            visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth())
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("digest", "basic").forEach { auth -> FilterChip(selected = state.auth == auth,
                enabled = !state.busy, onClick = { onSettings(state.workers, auth) }, label = { Text(auth.replaceFirstChar { it.uppercase() }) }) }
        }
        HorizontalDivider()
        Text("Скорость сканирования", style = MaterialTheme.typography.titleMedium)
        Text("Число одновременных запросов к устройствам. Начните с 8; для слабой Wi-Fi сети выберите 4.", color = Muted)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(4, 8, 16, 32).forEach { workers -> FilterChip(selected = state.workers == workers,
                enabled = !state.busy, onClick = { onSettings(workers, state.auth) }, label = { Text("$workers") }) }
        }
        HorizontalDivider()
        Text("О приложении", style = MaterialTheme.typography.titleMedium)
        Text("Android ${BuildConfig.VERSION_NAME}\nОбщее ядро сканирования ASIC Monitor", color = Muted)
        Text("При уходе из приложения сканирование останавливается. Уже найденные устройства остаются до закрытия процесса. Для круглосуточного мониторинга используйте агент на ПК.", color = Muted)
        Text("Тестовая версия. Совместимость с конкретными ASIC нужно проверить в вашей сети. HTTP API оборудования может передавать данные без шифрования.", style = MaterialTheme.typography.bodySmall, color = Muted)
    }
}
