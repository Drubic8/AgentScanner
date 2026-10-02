package io.github.drubic8.asicmonitor

import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
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
        compose.onNodeWithText("Карточки").performClick().assertIsSelected()
        compose.activityRule.scenario.recreate()
        compose.onNodeWithText("Карточки").assertIsSelected()
        compose.onNodeWithText("Компактно").performClick().assertIsSelected()
        compose.activityRule.scenario.recreate()
        compose.onNodeWithText("Компактно").assertIsSelected()
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
        compose.onNodeWithText("Компактно").assertIsSelected()
        compose.onNodeWithTag("device-list").performScrollToNode(hasContentDescription("Выбрать 192.0.2.1"))
        compose.onNodeWithContentDescription("Выбрать 192.0.2.1", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Команды · 1").assertIsEnabled()
        compose.onNodeWithTag("device-list").performScrollToNode(hasContentDescription("Выбрать 192.0.2.2"))
        compose.onNodeWithContentDescription("Выбрать 192.0.2.2", useUnmergedTree = true).performClick()
        compose.onNodeWithText("Команды · 2").performClick()
        compose.onNodeWithText("Режим HEM · 1/2").assertIsDisplayed()
        compose.onNodeWithText("Режим Low Power", substring = true).assertDoesNotExist()
        compose.onNodeWithText("Пробудить / возобновить майнинг · 2/2").performClick()
        compose.onNodeWithText("Avalon пробуждается через перезагрузку", substring = true).assertIsDisplayed()
        assertNull(submitted) // Opening a command and confirmation never sends it.
        compose.onNodeWithText("Отправить").performClick()
        compose.runOnIdle {
            assertEquals("mining_start", submitted!!.second)
            assertEquals(listOf("192.0.2.1", "192.0.2.2"), submitted!!.first.map { it.ip })
        }
        compose.onNodeWithTag("device-list").performScrollToNode(hasText("Карточки"))
        compose.onNodeWithText("Карточки").performClick().assertIsSelected()
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
    }
}
