package com.bsd.filter1

import android.content.Context
import java.util.UUID

/** Persistent local settings: control server, agent token, and a stable device id
 *  (mirrors the Windows agent's device_id in ProgramData). */
object Prefs {
    private const val FILE = "filter1"

    fun sp(ctx: Context) = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun server(ctx: Context): String =
        sp(ctx).getString("server", BuildConfig.DEFAULT_SERVER) ?: BuildConfig.DEFAULT_SERVER

    fun setServer(ctx: Context, v: String) = sp(ctx).edit().putString("server", v.trim()).apply()

    fun token(ctx: Context): String = sp(ctx).getString("token", "")?.trim() ?: ""

    fun setToken(ctx: Context, v: String) = sp(ctx).edit().putString("token", v.trim()).apply()

    /** Stable machine id, generated once and kept. */
    fun deviceId(ctx: Context): String {
        val cur = sp(ctx).getString("device_id", null)
        if (!cur.isNullOrBlank()) return cur
        val id = UUID.randomUUID().toString().replace("-", "").substring(0, 16)
        sp(ctx).edit().putString("device_id", id).apply()
        return id
    }

    fun setLastStatus(ctx: Context, v: String) = sp(ctx).edit().putString("status", v).apply()
    fun lastStatus(ctx: Context): String = sp(ctx).getString("status", "") ?: ""

    /** Whether the user turned protection on (used to auto-start after boot). */
    fun enabled(ctx: Context): Boolean = sp(ctx).getBoolean("enabled", false)
    fun setEnabled(ctx: Context, v: Boolean) = sp(ctx).edit().putBoolean("enabled", v).apply()
}
