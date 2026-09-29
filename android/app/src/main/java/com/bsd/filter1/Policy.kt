package com.bsd.filter1

import java.net.URI

/** Thread-safe snapshot of the active filtering policy, mirroring the Windows
 *  agent's four modes. decision(host) returns true when the query is allowed. */
object Policy {
    @Volatile var mode: String = "open"
    @Volatile var layers: String = "both"
    @Volatile var haveConfig: Boolean = false

    // guarded by lock
    private val lock = Any()
    private var whitelist: Set<String> = emptySet()
    private var blocked: Set<String> = emptySet()     // manual (incl. categories) + downloaded lists
    private var controlHosts: Set<String> = emptySet()

    fun setControlServers(urls: List<String>) {
        val hosts = urls.mapNotNull {
            try { URI(it).host?.lowercase() } catch (e: Exception) { null }
        }.toSet()
        synchronized(lock) { controlHosts = hosts }
    }

    fun apply(newMode: String, newLayers: String, wl: Set<String>, blk: Set<String>) {
        synchronized(lock) {
            mode = newMode
            layers = newLayers
            whitelist = wl
            blocked = blk
            haveConfig = true
        }
    }

    /** True = allow (forward upstream); false = block. Fail-open until configured. */
    fun decision(qname: String): Boolean {
        val name = qname.trimEnd('.').lowercase()
        synchronized(lock) {
            if (!haveConfig) return true
            if (matches(name, controlHosts)) return true      // never block the control server
            if (layers == "proxy") return true                // DNS layer disabled
            return when (mode) {
                "open" -> true
                "lockdown", "whitelist" -> matches(name, whitelist)
                "blacklist" -> !matches(name, blocked)
                else -> true
            }
        }
    }

    /** Match a host against a set including its parent domains (so a base domain
     *  entry blocks/allows all of its subdomains). */
    private fun matches(name: String, set: Set<String>): Boolean {
        if (set.isEmpty()) return false
        val parts = name.split(".")
        for (i in parts.indices) {
            if (set.contains(parts.subList(i, parts.size).joinToString("."))) return true
        }
        return false
    }
}
