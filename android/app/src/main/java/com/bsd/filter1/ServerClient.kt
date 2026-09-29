package com.bsd.filter1

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Talks to the same filter1 control server as the Windows agent. Reuses the
 *  /api/config contract (mode, whitelist, blacklist_manual, blocklists, layers). */
class ServerClient(private val ctx: Context) {

    data class Config(
        val mode: String,
        val layers: String,
        val whitelist: Set<String>,
        val manual: Set<String>,
        val blocklistUrls: List<String>,
    )

    /** Split a possibly multi-value server string (comma/semicolon/space). */
    fun servers(): List<String> =
        Prefs.server(ctx).split(Regex("[,;\\s]+"))
            .map { it.trim().trim('<', '>', '"', '\'').trimEnd('/') }
            .filter { it.isNotEmpty() }

    /** Fetch config from the first reachable server (fail-over). Returns null on failure. */
    fun fetchConfig(): Config? {
        val token = Prefs.token(ctx)
        val deviceId = Prefs.deviceId(ctx)
        val name = android.os.Build.MODEL ?: "android"
        val q = "token=${enc(token)}&device_id=${enc(deviceId)}&name=${enc(name)}"
        for (base in servers()) {
            try {
                val txt = httpGet("$base/api/config?$q") ?: continue
                val j = JSONObject(txt)
                Policy.setControlServers(servers())
                return Config(
                    mode = j.optString("mode", "open"),
                    layers = j.optString("layers", "both"),
                    whitelist = toSet(j.optJSONArray("whitelist")),
                    manual = toSet(j.optJSONArray("blacklist_manual")),
                    blocklistUrls = toList(j.optJSONArray("blocklists")),
                )
            } catch (e: Exception) {
                // try next server
            }
        }
        return null
    }

    /** Download hosts-format public blocklists and return the blocked domain set. */
    fun downloadBlocklists(urls: List<String>): Set<String> {
        val out = HashSet<String>()
        for (u in urls) {
            val text = try { httpGet(u) } catch (e: Exception) { null } ?: continue
            for (raw in text.lineSequence()) {
                val line = raw.trim()
                if (line.isEmpty() || line.startsWith("#")) continue
                val parts = line.split(Regex("\\s+"))
                val domain = (if (parts.size >= 2) parts[1] else parts[0])
                    .lowercase().trim('.')
                if (domain.isNotEmpty() &&
                    domain != "localhost" && domain != "0.0.0.0" && domain != "127.0.0.1"
                ) out.add(domain)
            }
        }
        return out
    }

    private fun httpGet(urlStr: String): String? {
        val c = URL(urlStr).openConnection() as HttpURLConnection
        return try {
            c.connectTimeout = 15000
            c.readTimeout = 30000
            c.setRequestProperty("User-Agent", "filter1")
            if (c.responseCode != 200) return null
            c.inputStream.bufferedReader().use { it.readText() }
        } finally {
            c.disconnect()
        }
    }

    private fun toSet(a: org.json.JSONArray?): Set<String> {
        val s = HashSet<String>()
        if (a != null) for (i in 0 until a.length()) s.add(a.optString(i).lowercase())
        return s
    }

    private fun toList(a: org.json.JSONArray?): List<String> {
        val l = ArrayList<String>()
        if (a != null) for (i in 0 until a.length()) l.add(a.optString(i))
        return l
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")
}
