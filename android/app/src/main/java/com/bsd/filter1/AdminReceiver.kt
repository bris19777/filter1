package com.bsd.filter1

import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context

/**
 * Device admin / Device Owner controller. When the device is provisioned as Device
 * Owner (one-time, via adb on a factory-reset device or QR enrollment), this makes
 * the filter tamper-resistant, the Android analog of the Windows SYSTEM service:
 *   - always-on VPN with lockdown (no traffic without the filter),
 *   - the app cannot be uninstalled.
 * Without Device Owner these calls are no-ops; the app still works, just removable.
 */
class AdminReceiver : DeviceAdminReceiver() {

    override fun onEnabled(context: Context, intent: android.content.Intent) {
        applyOwnerPolicies(context)
    }

    companion object {
        fun component(context: Context) = ComponentName(context, AdminReceiver::class.java)

        /** Apply the strong policies. Safe to call repeatedly; only effective when the
         *  app is Device Owner. */
        fun applyOwnerPolicies(context: Context) {
            val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
            val admin = component(context)
            if (!dpm.isDeviceOwnerApp(context.packageName)) return
            try {
                // Force all traffic through our VPN and forbid disabling it.
                dpm.setAlwaysOnVpnPackage(admin, context.packageName, /* lockdownEnabled = */ true)
                // Block uninstalling the filter.
                dpm.setUninstallBlocked(admin, context.packageName, true)
                // Forbid the user from configuring/adding other VPNs.
                dpm.addUserRestriction(admin, android.os.UserManager.DISALLOW_CONFIG_VPN)
                // Optional hardening: block safe boot and factory reset.
                dpm.addUserRestriction(admin, android.os.UserManager.DISALLOW_SAFE_BOOT)
                dpm.addUserRestriction(admin, android.os.UserManager.DISALLOW_FACTORY_RESET)
            } catch (e: Exception) {
                // ignore; partial policy application is acceptable
            }
        }
    }
}
