package com.bsd.filter1

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** Pulls policy from the control server every minute and applies it, mirroring the
 *  Windows agent's poll loop. Public blocklists are downloaded off the hot path and
 *  cached until the URL set changes. */
class Poller(private val ctx: Context) {

    private val client = ServerClient(ctx)
    private var lastBlocklistSig: List<String> = emptyList()
    private var downloaded: Set<String> = emptySet()

    fun start(scope: CoroutineScope) {
        scope.launch(Dispatchers.IO) {
            while (isActive) {
                pollOnce()
                delay(60_000)
            }
        }
    }

    fun pollOnce() {
        val cfg = client.fetchConfig()
        if (cfg == null) {
            Prefs.setLastStatus(ctx, "no server (running ${Policy.mode})")
            return
        }
        if (cfg.mode == "blacklist" && cfg.blocklistUrls != lastBlocklistSig) {
            val dl = client.downloadBlocklists(cfg.blocklistUrls)
            if (dl.isNotEmpty()) {
                downloaded = dl
                lastBlocklistSig = cfg.blocklistUrls
            }
        }
        val blocked = HashSet(cfg.manual).apply { addAll(downloaded) }
        Policy.apply(cfg.mode, cfg.layers, cfg.whitelist, blocked)
        Prefs.setLastStatus(ctx, "mode=${cfg.mode} blocked=${blocked.size}")
    }
}
