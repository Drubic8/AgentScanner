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

internal val Blue = Color(0xFF245EEA)
internal val Ink = Color(0xFF17243B)
internal val Muted = Color(0xFF526178)
internal val Positive = Color(0xFF12694F)
internal val Amber = Color(0xFF88520A)
internal val Palette = lightColorScheme(primary = Blue, onPrimary = Color.White,
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

@Composable
private fun MonitorApp(model: MonitorViewModel) {
    val state by model.state.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    // Intentionally not rememberSaveable: credentials never enter saved instance state.
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var detail by remember { mutableStateOf<Device?>(null) }
    var confirmation by remember { mutableStateOf<Pair<List<Device>, String>?>(null) }
    var commandTargets by remember { mutableStateOf<List<Device>?>(null) }
    var journalOpen by remember { mutableStateOf(false) }
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
                    onNetworks = { tab = 1 }, onExport = { exporter.launch("ASIC_Monitor.csv") }, onDevice = { detail = it },
                    onCompact = model::compact, onCommand = { commandTargets = it }, onJournal = { journalOpen = true })
                1 -> NetworksScreen(model, state)
                2 -> SettingsScreen(state, username, password, { username = it }, { password = it }, model::settings)
            }
        }
        }
    }
    detail?.let { original ->
        val device = state.devices.firstOrNull { it.selectionKey() == original.selectionKey() } ?: original.copy(stale = true)
        DeviceDetails(device, state.busy, onCommand = { action ->
            confirmation = listOf(device) to action; detail = null
        }, onDismiss = { detail = null })
    }
    commandTargets?.let { devices -> CommandPicker(devices,
        onCommand = { action -> confirmation = devices to action; commandTargets = null },
        onDismiss = { commandTargets = null }) }
    confirmation?.let { (devices, action) ->
        CommandConfirmation(devices, action, enabled = !state.busy,
            onConfirm = { confirmation = null; model.command(devices, action, username, password) },
            onDismiss = { confirmation = null })
    }
    if (journalOpen) CommandJournal(state.results, state.busy && state.commanding) { journalOpen = false }

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
        Text("Пустые поля: стандартные профили Antminer root/root, VNish admin или root, WhatsMiner super/super. Свой пароль укажите ниже.",
            style = MaterialTheme.typography.bodySmall, color = Muted)
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
