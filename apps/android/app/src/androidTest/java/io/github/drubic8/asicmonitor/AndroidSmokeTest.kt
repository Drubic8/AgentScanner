package io.github.drubic8.asicmonitor

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class AndroidSmokeTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @Test fun packagedPythonCatalogSqliteAndCsvWork() {
        val python = Python.getInstance()
        val module = python.getModule("android_bridge")
        val validation = JSONObject(module.callAttr("validate_ranges", "192.0.2.1-3").toString())
        assertEquals(3, validation.getInt("count"))
        val registry = python.getModule("miner_scanner.profiles").callAttr("ProfileRegistry")
        assertTrue(registry.get("profiles")!!.asList().size >= 10)
        val repository = python.getModule("miner_scanner.repository").callAttr("DeviceRepository", ":memory:")
        assertNull(repository.callAttr("get", "192.0.2.1"))
        repository.callAttr("close")
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val scanner = module.callAttr("MobileScanner", context.cacheDir.absolutePath)
        assertTrue(scanner.callAttr("export_csv").toString().startsWith("\uFEFFIP;"))
        // Direct Python TCP round-trip against loopback; no access to any ASIC or LAN.
        val result = python.getModule("builtins").callAttr("exec", """
import socket, threading
from miner_scanner.transports import Transport
from miner_scanner.runtime import Operation, ScanOptions
server = socket.socket()
server.bind(('127.0.0.1', 0))
server.listen(1)
def reply():
    connection, _ = server.accept()
    with connection:
        connection.recv(4096)
        connection.sendall(b'{"STATUS":[{"STATUS":"S"}]}\x00')
    server.close()
thread = threading.Thread(target=reply, daemon=True)
thread.start()
with Transport('127.0.0.1', Operation(ScanOptions())) as transport:
    response = transport.cgminer('version', port=server.getsockname()[1])
    assert response['STATUS'][0]['STATUS'] == 'S'
thread.join(3)
assert not thread.is_alive()
        """.trimIndent())
        assertNull(result)
    }

    @Test fun networkEditorValidatesAndPersistsWithoutScanning() {
        compose.waitUntil(15000) { compose.onAllNodes(androidx.compose.ui.test.hasText("Готов к сканированию")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Сети", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Добавить сеть").performClick()
        compose.onNodeWithText("Название").performTextInput("Тестовая сеть")
        compose.onNodeWithText("IP-адреса и подсети").performTextInput("192.0.2.1-3")
        compose.onNodeWithText("Сохранить").performClick()
        compose.waitUntil(5000) { compose.onAllNodes(androidx.compose.ui.test.hasText("Тестовая сеть")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Тестовая сеть").assertIsDisplayed()
        compose.activityRule.scenario.recreate()
        compose.onNodeWithText("Тестовая сеть").assertIsDisplayed()
        compose.onNodeWithText("Настройки", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Доступ к ASIC").assertIsDisplayed()
    }
}
