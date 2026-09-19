package io.github.drubic8.asicmonitor

import android.app.Application
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
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
    val ranges: String, val selected: Boolean = true)

data class Device(val id: String, val ip: String, val model: String, val firmware: String,
    val version: String, val rate: String, val temperature: String, val state: String,
    val stale: Boolean, val observed: String, val profile: String, val actions: Set<String>)

data class MonitorState(val ready: Boolean = false, val busy: Boolean = false,
    val stopping: Boolean = false, val processed: Int = 0, val total: Int = 0,
    val errors: Int = 0, val status: String = "Подготовка сканера…", val notice: String = "",
    val devices: List<Device> = emptyList(), val groups: List<NetworkGroup> = emptyList(),
    val workers: Int = 8, val auth: String = "digest")

class MonitorViewModel(application: Application) : AndroidViewModel(application) {
    private val preferences = application.getSharedPreferences("monitor", 0)
    private val mutable = MutableStateFlow(MonitorState(groups = loadGroups(),
        workers = preferences.getInt("workers", 8).takeIf { it in listOf(4, 8, 16, 32) } ?: 8,
        auth = preferences.getString("auth", "digest")?.takeIf { it in listOf("basic", "digest") } ?: "digest"))
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
                    notice("Соединение изменилось. Проверьте сеть и запустите сканирование заново.")
                }
            }
        }
        override fun onAvailable(network: Network) {
            viewModelScope.launch {
                if (state.value.busy && operationNetwork != null && operationNetwork != network) {
                    cancel()
                    notice("Сеть переключилась. Операция остановлена.")
                }
            }
        }
    }

    init {
        try {
            connectivity.registerDefaultNetworkCallback(networkCallback)
            callbackRegistered = true
        } catch (_: RuntimeException) { notice("Не удалось подключить наблюдение за сетью.") }
        viewModelScope.launch {
            try {
                bridge = withContext(Dispatchers.IO) {
                    Python.getInstance().getModule("android_bridge").callAttr("MobileScanner", application.filesDir.absolutePath)
                }
                mutable.update { it.copy(ready = true, status = "Готов к сканированию") }
            } catch (_: Exception) { mutable.update { it.copy(status = "Не удалось загрузить сканер. Перезапустите приложение.") } }
        }
    }

    fun setForeground(value: Boolean) { foreground = value; if (!value) cancel() }
    fun notice(message: String) { mutable.update { it.copy(notice = message) } }

    private fun loadGroups(): List<NetworkGroup> = try {
        val array = JSONArray(preferences.getString("groups", "[]"))
        List(array.length()) { index -> array.getJSONObject(index).let {
            NetworkGroup(it.getString("id"), it.getString("name"), it.getString("ranges"), it.optBoolean("selected", true))
        } }
    } catch (_: Exception) { emptyList() }

    private fun storeGroups(groups: List<NetworkGroup>) {
        val array = JSONArray()
        groups.forEach { array.put(JSONObject().put("id", it.id).put("name", it.name)
            .put("ranges", it.ranges).put("selected", it.selected)) }
        preferences.edit().putString("groups", array.toString()).apply()
        mutable.update { it.copy(groups = groups) }
    }
    fun selectGroup(id: String, selected: Boolean) = storeGroups(state.value.groups.map {
        if (it.id == id) it.copy(selected = selected) else it
    })
    fun deleteGroup(id: String) = storeGroups(state.value.groups.filterNot { it.id == id })
    suspend fun saveGroup(id: String?, name: String, ranges: String): String? {
        if (name.trim().isEmpty()) return "Укажите название сети"
        if (name.length > 80 || ranges.length > 16000) return "Слишком длинное название или список адресов"
        if (state.value.groups.any { it.id != id && it.name.equals(name.trim(), true) }) return "Такая сеть уже существует"
        val validation = withContext(Dispatchers.IO) {
            JSONObject(Python.getInstance().getModule("android_bridge").callAttr("validate_ranges", ranges).toString())
        }
        if (validation.getString("error").isNotEmpty()) return validation.getString("error")
        val existing = state.value.groups.firstOrNull { it.id == id }
        val group = NetworkGroup(id ?: UUID.randomUUID().toString(), name.trim(), ranges.trim(), existing?.selected ?: true)
        storeGroups(if (existing == null) state.value.groups + group else state.value.groups.map { if (it.id == id) group else it })
        return null
    }
    fun settings(workers: Int, auth: String) {
        preferences.edit().putInt("workers", workers).putString("auth", auth).apply()
        mutable.update { it.copy(workers = workers, auth = auth) }
    }
    private fun networkReady(): Boolean {
        val network = connectivity.activeNetwork
        val capabilities = connectivity.getNetworkCapabilities(network)
        val valid = capabilities != null && (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) || capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN))
        if (!valid) notice("Подключитесь к Wi-Fi ASIC, Ethernet или VPN. Доступ к интернету не обязателен.")
        operationNetwork = if (valid) network else null
        return valid
    }
    fun scan(username: String, password: String) {
        val current = state.value
        if (!current.ready || current.busy || !foreground || !networkReady()) return
        val ranges = current.groups.filter { it.selected }.joinToString("\n") { it.ranges }
        if (ranges.isBlank()) { notice("Добавьте и выберите сеть во вкладке «Сети»."); return }
        mutable.update { it.copy(busy = true, stopping = false, notice = "", status = "Сканирование…") }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { bridge.callAttr("start", ranges, username, password, current.auth, current.workers) }
                if (!foreground || state.value.stopping) withContext(Dispatchers.IO) { bridge.callAttr("cancel") }
                observe()
            } catch (_: Exception) {
                mutable.update { it.copy(busy = false, status = "Не удалось начать сканирование",
                    notice = "Проверьте адреса и общий лимит: 4096 IP.") }
            }
        }
    }
    fun command(device: Device, action: String) {
        if (!state.value.ready || state.value.busy || !foreground || !networkReady()) return
        mutable.update { it.copy(busy = true, stopping = false, notice = "", status = "Отправка команды…") }
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { bridge.callAttr("command", device.ip, device.id, action) }
                if (!foreground || state.value.stopping) withContext(Dispatchers.IO) { bridge.callAttr("cancel") }
                observe()
            } catch (_: Exception) {
                mutable.update { it.copy(busy = false, status = "Команда не выполнена",
                    notice = "Для этого устройства нет подтверждённой команды. Обновите сканирование.") }
            }
        }
    }
    private suspend fun observe() {
        do {
            val snapshot = withContext(Dispatchers.IO) { JSONObject(bridge.callAttr("snapshot").toString()) }
            val rows = snapshot.getJSONArray("rows")
            val devices = List(rows.length()) { parseDevice(rows.getJSONObject(it)) }
                .sortedWith(compareBy { device -> device.ip.split('.').fold(0L) { value, part -> value * 256 + part.toLong() } })
            val running = snapshot.getBoolean("running")
            mutable.update { it.copy(busy = running, devices = devices, processed = snapshot.getInt("processed"),
                total = snapshot.getInt("total"), errors = snapshot.getInt("errors"),
                status = if (running) it.status else if (snapshot.getBoolean("cancelled")) "Остановлено" else "Завершено",
                notice = snapshot.optJSONObject("command")?.let { result -> "${result.getString("status")}: ${result.getString("message")}" }
                    ?: snapshot.getString("error").ifBlank { it.notice }) }
            if (running) delay(500)
        } while (running)
        operationNetwork = null
    }
    fun cancel() {
        if (!state.value.ready || !state.value.busy) return
        mutable.update { it.copy(stopping = true, status = "Останавливаем…") }
        // Event.set is immediate; network calls finish within their existing timeout.
        bridge.callAttr("cancel")
    }
    fun export(uri: Uri) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    val csv = bridge.callAttr("export_csv").toString()
                    val stream = getApplication<Application>().contentResolver.openOutputStream(uri, "wt")
                        ?: error("No output stream")
                    stream.bufferedWriter(Charsets.UTF_8).use { it.write(csv) }
                }
                notice("CSV-отчёт сохранён")
            } catch (_: Exception) { notice("Не удалось сохранить отчёт. Выберите другой файл.") }
        }
    }
    override fun onCleared() {
        if (::bridge.isInitialized) bridge.callAttr("cancel")
        if (callbackRegistered) connectivity.unregisterNetworkCallback(networkCallback)
        super.onCleared()
    }
}

private fun JSONObject.text(key: String): String = if (isNull(key)) "—" else optString(key, "—")
private fun parseDevice(row: JSONObject): Device {
    val identity = row.getJSONObject("identity")
    val telemetry = row.getJSONObject("telemetry")
    val temperatures = telemetry.getJSONArray("temperatures_c")
    val maxTemperature = (0 until temperatures.length()).map { temperatures.getDouble(it) }.maxOrNull()
    val capabilities = row.getJSONObject("capabilities")
    return Device(identity.getString("device_id"), identity.getString("ip"), identity.text("model"),
        identity.text("firmware"), identity.text("firmware_version"),
        if (telemetry.isNull("rate")) "—" else "${telemetry.text("rate")} ${telemetry.text("rate_unit")}",
        maxTemperature?.let { "$it °C" } ?: "—", telemetry.text("mining_state"), telemetry.getBoolean("stale"),
        telemetry.text("observed_at"), identity.text("profile_id"),
        capabilities.keys().asSequence().filter { capabilities.optString(it) == "supported" }.toSet())
}
