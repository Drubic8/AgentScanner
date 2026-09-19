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
import android.graphics.Bitmap
import java.io.File

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
        val script = InstrumentationRegistry.getInstrumentation().context.assets
            .open("protocol_smoke.py").bufferedReader().use { it.readText() }
        val builtins = python.getModule("builtins")
        builtins.callAttr("exec", script, builtins.callAttr("dict"))
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
        screenshot("networks")
        compose.activityRule.scenario.recreate()
        compose.onNodeWithText("Тестовая сеть").assertIsDisplayed()
        compose.onNodeWithText("Настройки", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Доступ к ASIC").assertIsDisplayed()
        screenshot("settings")
        compose.onNodeWithText("Устройства", useUnmergedTree = true).performClick()
        screenshot("devices")
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val folder = File(instrumentation.targetContext.getExternalFilesDir(null), "screenshots").apply { mkdirs() }
        val bitmap = instrumentation.uiAutomation.takeScreenshot()
        File(folder, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
    }
}
