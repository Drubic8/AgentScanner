package io.github.drubic8.asicmonitor

import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import android.graphics.Bitmap
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class DeviceControlsTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    private fun device(ip: String, model: String, profile: String, actions: Set<String>, stale: Boolean = false) =
        Device(ip, ip, model, "Stock", "fixture", "110.00 TH/s", "65 °C", "stopped", stale,
            "2026-10-02T09:00:00Z", profile, actions)

    @Test fun compactPreferencePersistsAcrossActivityRecreation() {
        compose.waitUntil(15000) { compose.onAllNodes(hasText("Готов к сканированию")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithContentDescription("Компактный список").performClick()
        compose.onNodeWithContentDescription("Карточки устройств").assertIsOff()
        compose.activityRule.scenario.recreate()
        compose.onNodeWithContentDescription("Карточки устройств").assertIsOff()
        compose.onNodeWithContentDescription("Карточки устройств").performClick()
        compose.onNodeWithContentDescription("Компактный список").assertIsOn()
        compose.activityRule.scenario.recreate()
        compose.onNodeWithContentDescription("Компактный список").assertIsOn()
    }

    @Test fun compactSelectionOffersOnlyCompatibleCommandsAndWarnsAboutAvalonWakeup() {
        val common = setOf("identify_on", "identify_off", "mining_start", "mining_stop", "reboot")
        val devices = listOf(device("192.0.2.1", "Avalon 1346-110", "canaan.avalon", common),
            device("192.0.2.2", "Antminer T21", "bitmain.stock", common + "hem"),
            device("192.0.2.3", "Antminer S21", "bitmain.stock", common + "low", stale = true))
        var submitted: Pair<List<Device>, String>? = null
        compose.activity.runOnUiThread {
            compose.activity.setContent { MaterialTheme(colorScheme = Palette) {
                var compact by remember { mutableStateOf(true) }
                var targets by remember { mutableStateOf<List<Device>?>(null) }
                var confirmation by remember { mutableStateOf<Pair<List<Device>, String>?>(null) }
                DevicesScreen(MonitorState(ready = true, status = "Тестовая сеть", devices = devices, compact = compact),
                    onScan = {}, onCancel = {}, onNetworks = {}, onExport = {}, onDevice = {}, onCompact = { compact = it },
                    onCommand = { targets = it }, onJournal = {})
                targets?.let { selected -> CommandPicker(selected,
                    onCommand = { action -> confirmation = selected to action; targets = null }, onDismiss = { targets = null }) }
                confirmation?.let { (selected, action) -> CommandConfirmation(selected, action, enabled = true,
                    onConfirm = { submitted = selected to action; confirmation = null }, onDismiss = { confirmation = null }) }
            } }
        }
        compose.onNodeWithContentDescription("Компактный список").assertIsOn()
        compose.onNodeWithTag("device-list").performScrollToNode(hasContentDescription("Выбрать 192.0.2.1"))
        compose.onNodeWithContentDescription("Выбрать 192.0.2.1", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Команды · 1").assertIsEnabled()
        compose.onNodeWithTag("device-list").performScrollToNode(hasContentDescription("Выбрать 192.0.2.2"))
        compose.onNodeWithContentDescription("Выбрать 192.0.2.2", useUnmergedTree = true).performClick()
        screenshot("compact-devices")
        compose.onNodeWithText("Команды · 2").performClick()
        compose.onNodeWithText("Режим HEM · 1/2").assertIsDisplayed()
        compose.onNodeWithText("Режим Low Power", substring = true).assertDoesNotExist()
        compose.onNodeWithText("Пробудить / возобновить майнинг · 2/2").performClick()
        compose.onNodeWithText("Avalon пробуждается через перезагрузку", substring = true).assertIsDisplayed()
        screenshot("group-wakeup")
        assertNull(submitted) // Opening a command and confirmation never sends it.
        compose.onNodeWithText("Отправить").performClick()
        compose.runOnIdle {
            assertEquals("mining_start", submitted!!.second)
            assertEquals(listOf("192.0.2.1", "192.0.2.2"), submitted!!.first.map { it.ip })
        }
        compose.onNodeWithTag("device-list").performScrollToNode(hasContentDescription("Компактный список"))
        compose.onNodeWithContentDescription("Компактный список").performClick()
        compose.onNodeWithContentDescription("Карточки устройств").assertIsOff()
    }

    @Test fun staleDetailsDisableWritesAndJournalShowsIndividualOutcomes() {
        val stale = device("192.0.2.3", "Antminer S21", "bitmain.stock", setOf("mining_start", "low"), stale = true)
        var called = false
        compose.activity.runOnUiThread {
            compose.activity.setContent { MaterialTheme(colorScheme = Palette) {
                DeviceDetails(stale, busy = false, onCommand = { called = true }, onDismiss = {})
            } }
        }
        compose.onNodeWithText("Данные устарели. Повторите сканирование.").assertIsDisplayed()
        compose.onNodeWithText("Пробудить / возобновить майнинг").assertIsNotEnabled()
        compose.onNodeWithText("Режим Low Power").assertIsNotEnabled()
        assertFalse(called)
        compose.activity.runOnUiThread {
            compose.activity.setContent { MaterialTheme(colorScheme = Palette) {
                CommandJournal(listOf(DeviceCommandResult("192.0.2.1", "mining_start", "succeeded", "Проверено чтением API"),
                    DeviceCommandResult("192.0.2.2", "mining_start", "unsupported", "Нет совместимого API")), busy = false, onDismiss = {})
            } }
        }
        compose.onNodeWithText("192.0.2.1").assertIsDisplayed()
        compose.onNodeWithText("192.0.2.2").assertIsDisplayed()
        compose.onNodeWithText("Подтверждено", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Не поддерживается", substring = true).assertIsDisplayed()
        screenshot("command-journal")
    }

    @Test fun compactControlsRemainReachableOnSmallAndWideLayoutsWithLargeText() {
        val devices = listOf(device("192.0.2.1", "Antminer S21+ с длинным названием прошивки", "bitmain.stock", setOf("mining_start")))
        try {
        for ((width, height, scale) in listOf(Triple(375, 520, 1f), Triple(375, 520, 2f), Triple(740, 320, 1f))) {
            if (width > 375) {
                compose.activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
                compose.waitUntil(10000) { compose.activity.resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE }
            }
            compose.activity.runOnUiThread {
                compose.activity.setContent { MaterialTheme(colorScheme = Palette) {
                    val density = LocalDensity.current.density
                    CompositionLocalProvider(LocalDensity provides Density(density, scale)) {
                        key(width, height, scale) { Box(Modifier.width(width.dp).height(height.dp)) {
                            DevicesScreen(MonitorState(ready = true, status = "Тест", devices = devices),
                                onScan = {}, onCancel = {}, onNetworks = {}, onExport = {}, onDevice = {},
                                onCompact = {}, onCommand = {}, onJournal = {})
                        } }
                    }
                } }
            }
            compose.onNodeWithTag("device-list").performScrollToNode(hasContentDescription("Выбрать 192.0.2.1"))
            compose.onNodeWithContentDescription("Выбрать 192.0.2.1", useUnmergedTree = true).performClick()
            compose.onNodeWithText("Команды · 1").assertIsDisplayed().assertIsEnabled()
            // Large text must retain an accessible select-all control without a narrow text column.
            compose.onNodeWithContentDescription("Снять выбор", useUnmergedTree = true).assertIsDisplayed()
            screenshot("compact-${width}-${scale}")
        }
        } finally { compose.activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val output = InstrumentationRegistry.getArguments().getString("additionalTestOutputDir")
            ?: instrumentation.targetContext.getExternalFilesDir(null)!!.absolutePath
        val folder = File(output, "screenshots").apply { mkdirs() }
        val bitmap = instrumentation.uiAutomation.takeScreenshot()
        File(folder, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
        bitmap.recycle()
    }
}
