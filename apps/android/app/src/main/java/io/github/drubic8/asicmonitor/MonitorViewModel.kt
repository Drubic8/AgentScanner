package io.github.drubic8.asicmonitor

import android.app.Application
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class NetworkGroup(val id: String = UUID.randomUUID().toString(), val name: String,
    val ranges: String, val selected: Boolean = true, val folder: String = "")

data class Device(val id: String, val ip: String, val model: String, val firmware: String,
    val version: String, val rate: String, val temperature: String, val state: String,
    val stale: Boolean, val observed: String, val profile: String, val actions: Set<String>,
    val algorithm: String = "—", val average: String = "—", val led: Boolean? = null, val errors: String = "")

data class MonitorState(val ready: Boolean = false, val busy: Boolean = false,
    val stopping: Boolean = false, val processed: Int = 0, val total: Int = 0,
    val errors: Int = 0, val status: String = tr("Подготовка сканера…"), val notice: String = "",
    val devices: List<Device> = emptyList(), val groups: List<NetworkGroup> = emptyList(),
    val workers: Int = 8, val auth: String = "digest", val compact: Boolean = true,
    val commanding: Boolean = false, val commandTotal: Int = 0, val results: List<DeviceCommandResult> = emptyList(),
    val history: List<DeviceCommandResult> = emptyList(), val historyDropped: Int = 0)

data class DeviceCommandResult(val ip: String, val action: String, val status: String, val message: String, val time: String = "")

class MonitorViewModel(application: Application) : AndroidViewModel(application) {
    private val preferences = application.getSharedPreferences("monitor", 0)
    private val mutable = MutableStateFlow(MonitorState(groups = loadGroups(),
        workers = preferences.getInt("workers", 8).takeIf { it in listOf(4, 8, 16, 32) } ?: 8,
        auth = preferences.getString("auth", "digest")?.takeIf { it in listOf("basic", "digest") } ?: "digest",
        compact = preferences.getBoolean("compact", true)))
    val state = mutable.asStateFlow()
    private lateinit var bridge: PyObject
    private val connectivity = application.getSystemService(ConnectivityManager::class.java)
    private var operationNetwork: Network? = null
    private var callbackRegistered = false
    private var foreground = true
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onLost(network: Network) {
            viewModelScope.launch {
                if (state.value.busy && operationNetwork == network) {
                    cancel()
                    notice(tr("Соединение изменилось. Проверьте сеть и запустите сканирование заново."))
                }
            }
        }
    }

    init {
        try {
            connectivity.registerNetworkCallback(NetworkRequest.Builder()
                .removeCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN).build(), networkCallback)
            callbackRegistered = true
        } catch (_: RuntimeException) { notice(tr("Не удалось подключить наблюдение за сетью.")) }
        viewModelScope.launch {
            try {
                bridge = withContext(Dispatchers.IO) {
                    Python.getInstance().getModule("android_bridge").callAttr("MobileScanner", application.filesDir.absolutePath)
                }
                mutable.update { it.copy(ready = true, status = tr("Готов к сканированию")) }
            } catch (_: Exception) { mutable.update { it.copy(status = tr("Не удалось загрузить сканер. Перезапустите приложение.")) } }
        }
    }

    fun setForeground(value: Boolean) { foreground = value; if (!value) cancel() }
    fun notice(message: String) { mutable.update { it.copy(notice = message) } }

    private fun loadGroups(): List<NetworkGroup> = try {
        val array = JSONArray(preferences.getString("groups", "[]"))
        List(array.length()) { index -> array.getJSONObject(index).let {
            NetworkGroup(it.getString("id"), it.getString("name"), it.getString("ranges"), it.optBoolean("selected", true), it.optString("folder"))
        } }
    } catch (_: Exception) { emptyList() }

    private fun storeGroups(groups: List<NetworkGroup>) {
        val array = JSONArray()
        groups.forEach { array.put(JSONObject().put("id", it.id).put("name", it.name)
            .put("ranges", it.ranges).put("selected", it.selected).put("folder", it.folder)) }
        preferences.edit().putString("groups", array.toString()).apply()
        mutable.update { it.copy(groups = groups) }
    }
    fun selectGroup(id: String, selected: Boolean) = storeGroups(state.value.groups.map {
        if (it.id == id) it.copy(selected = selected) else it
    })
    fun deleteGroup(id: String) = storeGroups(state.value.groups.filterNot { it.id == id })
    fun selectFolder(folder: String, selected: Boolean) = storeGroups(state.value.groups.map {
        if (it.folder == folder || it.folder.startsWith("$folder/")) it.copy(selected = selected) else it
    })
    suspend fun saveGroup(id: String?, name: String, ranges: String, folder: String = ""): String? {
        if (name.trim().isEmpty()) return tr("Укажите название сети")
        if (name.length > 80 || ranges.length > 16000) return tr("Слишком длинное название или список адресов")
        val folderPath = folder.split('/').map { it.trim() }.filter { it.isNotEmpty() }.joinToString("/")
        if (folderPath.length > 240 || folderPath.split('/').size > 4) return tr("Максимум 4 уровня папок и 240 символов")
        if (state.value.groups.any { it.id != id && it.folder == folderPath && it.name.equals(name.trim(), true) }) return tr("Такая сеть уже существует в папке")
        val validation = withContext(Dispatchers.IO) {
            JSONObject(Python.getInstance().getModule("android_bridge").callAttr("validate_ranges", ranges).toString())
        }
        if (validation.getString("error").isNotEmpty()) return validation.getString("error")
        val existing = state.value.groups.firstOrNull { it.id == id }
        val group = NetworkGroup(id ?: UUID.randomUUID().toString(), name.trim(), ranges.trim(), existing?.selected ?: true, folderPath)
        storeGroups(if (existing == null) state.value.groups + group else state.value.groups.map { if (it.id == id) group else it })
        return null
    }
    fun settings(workers: Int, auth: String) {
        preferences.edit().putInt("workers", workers).putString("auth", auth).apply()
        mutable.update { it.copy(workers = workers, auth = auth) }
    }
    fun language(value: String) {
        AppLanguage.set(value)
        preferences.edit().putString("language", AppLanguage.language).apply()
    }
    fun compact(value: Boolean) {
        preferences.edit().putBoolean("compact", value).apply()
        mutable.update { it.copy(compact = value) }
    }
    private fun networkReady(): Boolean {
        fun suitable(network: Network?): Boolean {
            val capabilities = connectivity.getNetworkCapabilities(network) ?: return false
            return capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) || capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
        }
        // A Wi-Fi LAN without Internet may not be the default network when cellular is enabled.
        // Preserve the active VPN where present; otherwise use an available LAN explicitly.
        val network = connectivity.activeNetwork?.takeIf { suitable(it) }
            ?: connectivity.allNetworks.firstOrNull { suitable(it) }
        if (network == null || !connectivity.bindProcessToNetwork(network)) {
            notice(tr("Подключитесь к Wi-Fi ASIC, Ethernet или VPN. Доступ к интернету не обязателен."))
            return false
        }
        operationNetwork = network
        return true
    }
    private fun releaseNetwork() {
        connectivity.bindProcessToNetwork(null)
        operationNetwork = null
    }
    fun scan(username: String, password: String) {
        val current = state.value
        if (!current.ready || current.busy || !foreground) return
        val ranges = current.groups.filter { it.selected }.joinToString("\n") { it.ranges }
        if (ranges.isBlank()) { notice(tr("Добавьте и выберите сеть во вкладке «Сети».")); return }
        if (!networkReady()) return
        mutable.update { it.copy(busy = true, stopping = false, commanding = false, results = emptyList(),
            notice = "", status = tr("Сканирование…")) }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { bridge.callAttr("start", ranges, username, password, current.auth, current.workers) }
                if (!foreground || state.value.stopping) withContext(Dispatchers.IO) { bridge.callAttr("cancel") }
                observe()
            } catch (cancelled: CancellationException) {
                bridge.callAttr("cancel")
                throw cancelled
            } catch (_: Exception) {
                bridge.callAttr("cancel")
                mutable.update { it.copy(busy = false, status = tr("Не удалось начать сканирование"),
                    notice = tr("Проверьте адреса и общий лимит: 4096 IP.")) }
            } finally {
                releaseNetwork()
            }
        }
    }
    fun command(devices: List<Device>, action: String, username: String, password: String) {
        if (!state.value.ready || state.value.busy || !foreground || !networkReady()) return
        val targets = JSONArray()
        devices.forEach { targets.put(JSONObject().put("ip", it.ip).put("device_id", it.id)) }
        mutable.update { it.copy(busy = true, stopping = false, commanding = true, commandTotal = devices.size,
            results = emptyList(), notice = "", status = tr("Отправка команды…")) }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { bridge.callAttr("command_many", targets.toString(), action,
                    username, password, state.value.auth) }
                if (!foreground || state.value.stopping) withContext(Dispatchers.IO) { bridge.callAttr("cancel") }
                observe()
            } catch (cancelled: CancellationException) {
                bridge.callAttr("cancel")
                throw cancelled
            } catch (_: Exception) {
                bridge.callAttr("cancel")
                mutable.update { it.copy(busy = false, status = tr("Команда не выполнена"),
                    notice = tr("Для этого устройства нет подтверждённой команды. Обновите сканирование.")) }
            } finally {
                releaseNetwork()
            }
        }
    }
    private suspend fun observe() {
        do {
            val snapshot = withContext(Dispatchers.IO) { JSONObject(bridge.callAttr("snapshot").toString()) }
            val rows = snapshot.getJSONArray("rows")
            val devices = withContext(Dispatchers.Default) {
                List(rows.length()) { parseDevice(rows.getJSONObject(it)) }
                    .sortedWith(compareBy { device -> device.ip.split('.').fold(0L) { value, part -> value * 256 + part.toLong() } })
            }
            val running = snapshot.getBoolean("running")
            val commanding = snapshot.optString("operation") == "command"
            val commands = snapshot.optJSONArray("commands") ?: JSONArray()
            val results = List(commands.length()) { index -> commands.getJSONObject(index).let {
                parseCommandResult(it)
            } }.sortedBy { it.ip }
            val historyRows = snapshot.optJSONArray("history") ?: JSONArray()
            val history = List(historyRows.length()) { parseCommandResult(historyRows.getJSONObject(it)) }
            mutable.update { it.copy(busy = running, devices = devices, processed = snapshot.getInt("processed"),
                total = snapshot.getInt("total"), errors = snapshot.getInt("errors"),
                commanding = commanding, commandTotal = snapshot.optInt("command_total"), results = results,
                history = history, historyDropped = snapshot.optInt("history_dropped"),
                status = if (running) it.status else if (snapshot.getBoolean("cancelled")) tr("Остановлено") else tr("Завершено"),
                notice = if (running) it.notice else snapshot.getString("error").ifBlank {
                    if (commanding) tr("Обработано устройств: {p0}. Результаты — в журнале команд.", "p0" to (results.size)) else it.notice
                }) }
            if (running) delay(500)
        } while (running)
    }
    fun cancel() {
        if (!state.value.ready || !state.value.busy) return
        mutable.update { it.copy(stopping = true, status = tr("Останавливаем…")) }
        // Event.set is immediate; network calls finish within their existing timeout.
        bridge.callAttr("cancel")
    }
    fun export(uri: Uri, journal: Boolean = false) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    val csv = bridge.callAttr(if (journal) "export_command_csv" else "export_csv").toString()
                    val stream = getApplication<Application>().contentResolver.openOutputStream(uri, "wt")
                        ?: error("No output stream")
                    stream.bufferedWriter(Charsets.UTF_8).use { it.write(csv) }
                }
                notice(tr("CSV-отчёт сохранён"))
            } catch (_: Exception) { notice(tr("Не удалось сохранить отчёт. Выберите другой файл.")) }
        }
    }
    override fun onCleared() {
        if (::bridge.isInitialized) bridge.callAttr("cancel")
        if (callbackRegistered) connectivity.unregisterNetworkCallback(networkCallback)
        super.onCleared()
    }
}

private fun JSONObject.text(key: String): String = if (isNull(key)) "—" else optString(key, "—")
internal fun parseDevice(row: JSONObject): Device {
    val identity = row.getJSONObject("identity")
    val telemetry = row.getJSONObject("telemetry")
    val temperatures = telemetry.getJSONArray("temperatures_c")
    val maxTemperature = (0 until temperatures.length()).map { temperatures.getDouble(it) }.maxOrNull()
    val capabilities = row.getJSONObject("capabilities")
    return Device(identity.getString("device_id"), identity.getString("ip"), identity.text("model"),
        identity.text("firmware"), identity.text("firmware_version"),
        row.optString("rate_display", "—"),
        maxTemperature?.let { "${String.format(java.util.Locale.ROOT, "%.0f", it)} °C" } ?: "—",
        telemetry.text("mining_state"), telemetry.getBoolean("stale"),
        telemetry.text("observed_at"), identity.text("profile_id"),
        capabilities.keys().asSequence().filter { capabilities.optString(it) == "supported" }.toSet(),
        telemetry.text("algorithm"), row.optString("average_display", "—"),
        if (telemetry.isNull("identify_enabled")) null else telemetry.optBoolean("identify_enabled"),
        row.optString("error_code"))
}

private fun parseCommandResult(row: JSONObject) = DeviceCommandResult(row.getString("ip"), row.getString("action"),
    row.getString("status"), row.getString("message"), row.optString("time"))
