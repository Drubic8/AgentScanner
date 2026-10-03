package io.github.drubic8.asicmonitor

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.After
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class AppLanguageTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()

    @After fun restoreRussian() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        context.getSharedPreferences("monitor", 0).edit().putString("language", "ru").commit()
        compose.runOnIdle { AppLanguage.set("ru") }
    }

    @Test fun settingsSwitchesWholeInterfaceAndPersistsAfterRecreation() {
        compose.onNodeWithText("Настройки").performClick()
        compose.onNodeWithText("Русский").performClick()
        compose.onNodeWithText("English").performClick()
        compose.onNodeWithText("Interface language").assertIsDisplayed()
        compose.onNodeWithText("ASIC access").assertIsDisplayed()
        compose.onNodeWithText("Devices").performClick()
        compose.onNodeWithText("Scan").assertIsDisplayed()
        compose.onNodeWithContentDescription("Compact list").assertIsDisplayed()
        compose.activityRule.scenario.recreate()
        compose.onNodeWithText("Scan").assertIsDisplayed()
        compose.onNodeWithText("Networks").performClick()
        compose.onNodeWithText("Add network").assertIsDisplayed()
        compose.onNodeWithText("Add network").performClick()
        compose.onNodeWithText("IP addresses and subnets").assertIsDisplayed()
        compose.onNodeWithText("Cancel").performClick()
    }

    @Test fun translatedCommandsKeepIdentifiersAndLiteralArguments() {
        compose.runOnIdle { AppLanguage.set("en") }
        assertEquals("LED on", commandNames["identify_on"])
        assertEquals("Wake up / resume mining", commandNames["mining_start"])
        val source = "Площадка {p1} \$secret"
        assertEquals("Delete $source", tr("Удалить {p0}", "p0" to source))
        compose.runOnIdle { AppLanguage.set("ru") }
        assertEquals("Включить подсветку", commandNames["identify_on"])
    }
}
