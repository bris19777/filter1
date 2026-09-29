package com.bsd.filter1

import android.app.Activity
import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/** Minimal control screen: enter the control server + agent token, then start or
 *  stop the DNS-filtering VPN. On a Device-Owner deployment this screen is mostly
 *  irrelevant because the OS starts the always-on VPN automatically. */
class MainActivity : Activity() {

    private lateinit var status: TextView
    private lateinit var serverField: EditText
    private lateinit var tokenField: EditText

    private val vpnConsent = 1001

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 64, 48, 48)
        }

        root.addView(TextView(this).apply {
            text = "filter1 — בקרת הורים"
            textSize = 20f
            gravity = Gravity.CENTER
        })

        serverField = EditText(this).apply {
            hint = "כתובת שרת"
            setText(Prefs.server(this@MainActivity))
        }
        root.addView(serverField)

        tokenField = EditText(this).apply {
            hint = "טוקן"
            setText(Prefs.token(this@MainActivity))
        }
        root.addView(tokenField)

        root.addView(Button(this).apply {
            text = "הפעל הגנה"
            setOnClickListener { onStart() }
        })
        root.addView(Button(this).apply {
            text = "כבה הגנה"
            setOnClickListener { onStopProtection() }
        })

        status = TextView(this).apply { setPadding(0, 32, 0, 0) }
        root.addView(status)

        setContentView(root)
        refreshStatus()
    }

    private fun onStart() {
        Prefs.setServer(this, serverField.text.toString())
        Prefs.setToken(this, tokenField.text.toString())
        val prepare = VpnService.prepare(this)
        if (prepare != null) {
            startActivityForResult(prepare, vpnConsent)   // ask user to allow the VPN
        } else {
            onActivityResult(vpnConsent, Activity.RESULT_OK, null)
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == vpnConsent && resultCode == Activity.RESULT_OK) {
            Prefs.setEnabled(this, true)
            val svc = Intent(this, FilterVpnService::class.java)
            startForegroundService(svc)
            status.text = "הגנה מופעלת"
        }
    }

    private fun onStopProtection() {
        Prefs.setEnabled(this, false)
        startService(Intent(this, FilterVpnService::class.java).apply {
            action = FilterVpnService.ACTION_STOP
        })
        status.text = "הגנה כבויה"
    }

    private fun refreshStatus() {
        val s = Prefs.lastStatus(this)
        status.text = if (Prefs.enabled(this)) "מופעל · $s" else "כבוי"
    }
}
