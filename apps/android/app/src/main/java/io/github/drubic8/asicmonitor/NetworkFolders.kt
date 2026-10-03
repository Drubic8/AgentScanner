package io.github.drubic8.asicmonitor

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.state.ToggleableState
import androidx.compose.ui.unit.dp

internal data class NetworkEntry(val folder: String, val group: NetworkGroup? = null) {
    val key: String get() = group?.id ?: "folder:$folder"
}

internal fun networkEntries(groups: List<NetworkGroup>, collapsed: Set<String>): List<NetworkEntry> = buildList {
    groups.filter { it.folder.isEmpty() }.forEach { add(NetworkEntry("", it)) }
    val folders = groups.flatMap { group ->
        val parts = group.folder.split('/').filter { it.isNotEmpty() }
        parts.indices.map { parts.take(it + 1).joinToString("/") }
    }.distinct().sorted()
    folders.forEach { folder ->
        if (collapsed.none { folder.startsWith("$it/") }) {
            add(NetworkEntry(folder))
            if (folder !in collapsed) groups.filter { it.folder == folder }.forEach { add(NetworkEntry(folder, it)) }
        }
    }
}

@Composable
internal fun FolderRow(entry: NetworkEntry, groups: List<NetworkGroup>, collapsed: Boolean, busy: Boolean,
    onToggle: () -> Unit, onSelect: (Boolean) -> Unit) {
    val members = groups.filter { it.folder == entry.folder || it.folder.startsWith("${entry.folder}/") }
    val selected = members.count { it.selected }
    Row(Modifier.fillMaxWidth().padding(start = (entry.folder.count { it == '/' } * 12).dp), verticalAlignment = Alignment.CenterVertically) {
        TriStateCheckbox(state = when { selected == 0 -> ToggleableState.Off; selected == members.size -> ToggleableState.On; else -> ToggleableState.Indeterminate },
            enabled = !busy, onClick = { onSelect(selected != members.size) })
        TextButton(onClick = onToggle, modifier = Modifier.weight(1f).heightIn(min = 48.dp)) {
            Icon(if (collapsed) Icons.Outlined.ChevronRight else Icons.Outlined.ExpandMore, null)
            Icon(Icons.Outlined.FolderOpen, null); Spacer(Modifier.width(6.dp))
            Text("${entry.folder.substringAfterLast('/')} · $selected/${members.size}", Modifier.weight(1f))
        }
    }
}
