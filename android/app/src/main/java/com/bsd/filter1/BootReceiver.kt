package com.bsd.filter1

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.VpnService

/** Restart the filter after reboot if the parent had turned it on and VPN consent
 *  was already granted. (With a Device-Owner always-on VPN the OS handles this and
 *  the receiver is redundant.) */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        val action = intent?.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != Intent.ACTION_LOCKED_BOOT_COMPLETED
        ) return
        if (!Prefs.enabled(context)) return
        // establish() succeeds only if consent was granted before; prepare()==null means granted.
        if (VpnService.prepare(context) != null) return
        context.startForegroundService(Intent(context, FilterVpnService::class.java))
    }
}
