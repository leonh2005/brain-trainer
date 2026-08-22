package com.steven.gesturetoolkit.actions

class ActionRegistry(val actions: List<Action>) {

    init {
        val ids = actions.map { it.id }
        require(ids.size == ids.toSet().size) { "Duplicate action ids: $ids" }
    }

    fun get(id: String): Action? = actions.find { it.id == id }
}
