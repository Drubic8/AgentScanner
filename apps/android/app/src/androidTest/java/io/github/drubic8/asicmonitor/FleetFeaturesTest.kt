package io.github.drubic8.asicmonitor

import org.junit.Assert.*
import org.junit.Test

class FleetFeaturesTest {
    private fun device() = Device("demo", "192.0.2.3", "Antminer S21", "PitBit", "incm.C074",
        "200.00 TH/s", "55 °C", "running", false, "2026-10-03T10:00:00Z", "bitmain.pitbit",
        setOf("mining_stop"), "SHA-256", "198.00 TH/s", true, "")

    @Test fun combinedFiltersNeverTreatUnknownOrStaleLedAsOff() {
        val d = device()
        assertTrue(matchesDevice(d, "s21", "running", "on", d.model, d.firmware, false))
        assertFalse(matchesDevice(d.copy(led = null), "", "", "off", "", "", false))
        assertFalse(matchesDevice(d.copy(stale = true), "", "", "on", "", "", false))
        assertTrue(matchesDevice(d.copy(stale = true), "", "stale", "", "", "", false))
        assertFalse(matchesDevice(d, "", "stopped", "", "", "", false))
        assertTrue(matchesDevice(d.copy(errors = "FAN ERR"), "fan", "", "", "", "", true))
    }

    @Test fun folderCollapsePreservesSelectionAndUsesWholePath() {
        val groups = listOf(NetworkGroup("a", "Утро", "192.0.2.1", true, "Площадка 1/Сон"),
            NetworkGroup("b", "День", "192.0.2.2", true, "Площадка 2/Сон"),
            NetworkGroup("c", "Без папки", "192.0.2.3", false))
        val expanded = networkEntries(groups, emptySet())
        assertEquals(7, expanded.size)
        val collapsed = networkEntries(groups, setOf("Площадка 1"))
        assertFalse(collapsed.any { it.group?.id == "a" })
        assertTrue(collapsed.any { it.group?.id == "b" })
        assertTrue(collapsed.any { it.folder == "Площадка 1" && it.group == null })
        assertEquals(2, groups.count { it.selected })
        assertEquals(expanded.size, expanded.map { it.key }.distinct().size)
    }
}
