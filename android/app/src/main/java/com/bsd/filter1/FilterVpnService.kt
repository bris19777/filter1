package com.bsd.filter1

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * DNS-filtering VPN. Routes only the local VPN DNS address (10.0.0.2) through the
 * tunnel; every DNS query the device makes lands here. We parse the query, decide
 * with [Policy], answer blocked names with 0.0.0.0, and forward allowed ones to an
 * upstream resolver over a protected socket. This is the Android analog of the
 * Windows DNS agent; HTTPS content filtering (MITM) is not attempted on Android.
 */
class FilterVpnService : VpnService() {

    companion object {
        const val ACTION_STOP = "com.bsd.filter1.STOP"
        private const val VPN_DNS = "10.0.0.2"
        private const val UPSTREAM = "1.1.1.1"
        private const val CHANNEL = "filter1"
        private const val NOTIF_ID = 1
    }

    @Volatile private var running = false
    private var tun: ParcelFileDescriptor? = null
    private var worker: Thread? = null
    private val pool: ExecutorService = Executors.newFixedThreadPool(16)
    private val scope = CoroutineScope(SupervisorJob())

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopEverything()
            return START_NOT_STICKY
        }
        if (running) return START_STICKY
        startForeground(NOTIF_ID, notification("מסנן פעיל"))
        startTunnel()
        Poller(this).start(scope)
        return START_STICKY   // restart if killed
    }

    private fun startTunnel() {
        val b = Builder()
            .setSession("filter1")
            .addAddress(VPN_DNS, 32)
            .addDnsServer(VPN_DNS)
            .addRoute(VPN_DNS, 32)     // only DNS to our resolver goes through the tunnel
            .setBlocking(true)
        try {
            tun = b.establish() ?: return
        } catch (e: Exception) {
            return
        }
        running = true
        val fd = tun!!.fileDescriptor
        worker = Thread {
            val input = FileInputStream(fd)
            val output = FileOutputStream(fd)
            val buffer = ByteArray(32767)
            while (running) {
                val n = try { input.read(buffer) } catch (e: Exception) { -1 }
                if (n <= 0) continue
                val packet = buffer.copyOf(n)
                pool.execute { handlePacket(packet, output) }
            }
        }.also { it.start() }
    }

    private fun handlePacket(buf: ByteArray, out: FileOutputStream) {
        try {
            if (buf.size < 28) return
            if ((buf[0].toInt() and 0xf0) shr 4 != 4) return   // IPv4 only
            val ihl = (buf[0].toInt() and 0x0f) * 4
            if ((buf[9].toInt() and 0xff) != 17) return         // UDP only
            val udp = ihl
            val dport = ((buf[udp + 2].toInt() and 0xff) shl 8) or (buf[udp + 3].toInt() and 0xff)
            if (dport != 53) return
            val sport = ((buf[udp].toInt() and 0xff) shl 8) or (buf[udp + 1].toInt() and 0xff)
            val srcIp = buf.copyOfRange(12, 16)
            val dstIp = buf.copyOfRange(16, 20)
            val ulen = ((buf[udp + 4].toInt() and 0xff) shl 8) or (buf[udp + 5].toInt() and 0xff)
            val dnsOff = udp + 8
            val dnsLen = ulen - 8
            if (dnsLen < 12 || dnsOff + dnsLen > buf.size) return

            val (qname, qnameEnd) = readQName(buf, dnsOff + 12)
            if (qnameEnd + 4 > buf.size) return
            val qtype = ((buf[qnameEnd].toInt() and 0xff) shl 8) or (buf[qnameEnd + 1].toInt() and 0xff)

            val resp: ByteArray = if (Policy.decision(qname)) {
                forwardUpstream(buf.copyOfRange(dnsOff, dnsOff + dnsLen)) ?: return
            } else {
                buildBlockedDns(buf, dnsOff, qtype, qnameEnd)
            }
            // response: src = the address the app queried (VPN DNS), dst = the app
            val pkt = buildUdpPacket(dstIp, srcIp, 53, sport, resp)
            synchronized(out) { out.write(pkt) }
        } catch (e: Exception) {
            // drop malformed packet
        }
    }

    private fun forwardUpstream(query: ByteArray): ByteArray? {
        val sock = DatagramSocket()
        return try {
            protect(sock)
            sock.soTimeout = 4000
            val addr = InetAddress.getByName(UPSTREAM)
            sock.send(DatagramPacket(query, query.size, addr, 53))
            val buf = ByteArray(1500)
            val dp = DatagramPacket(buf, buf.size)
            sock.receive(dp)
            dp.data.copyOfRange(0, dp.length)
        } catch (e: Exception) {
            null
        } finally {
            sock.close()
        }
    }

    // ---- DNS / packet helpers ----

    private fun readQName(buf: ByteArray, start: Int): Pair<String, Int> {
        val sb = StringBuilder()
        var i = start
        while (i < buf.size) {
            val len = buf[i].toInt() and 0xff
            if (len == 0) { i += 1; break }
            if (len and 0xc0 != 0) { i += 2; break }   // compression pointer (not expected in question)
            i += 1
            for (j in 0 until len) sb.append((buf[i + j].toInt() and 0xff).toChar())
            sb.append('.')
            i += len
        }
        return Pair(sb.toString().trimEnd('.'), i)
    }

    private fun buildBlockedDns(query: ByteArray, dnsOff: Int, qtype: Int, qnameEnd: Int): ByteArray {
        val qStart = dnsOff + 12
        val qEnd = qnameEnd + 4                 // qname null + qtype(2) + qclass(2)
        val questionLen = qEnd - qStart
        val isA = qtype == 1
        val ansLen = if (isA) 16 else 0
        val out = ByteArray(12 + questionLen + ansLen)
        out[0] = query[dnsOff]; out[1] = query[dnsOff + 1]     // transaction id
        out[2] = 0x81.toByte(); out[3] = 0x80.toByte()          // response, RD, RA
        out[4] = 0; out[5] = 1                                   // QDCOUNT=1
        out[6] = 0; out[7] = (if (isA) 1 else 0).toByte()       // ANCOUNT
        System.arraycopy(query, qStart, out, 12, questionLen)
        if (isA) {
            val o = 12 + questionLen
            out[o] = 0xC0.toByte(); out[o + 1] = 0x0C            // name pointer -> offset 12
            out[o + 2] = 0; out[o + 3] = 1                       // type A
            out[o + 4] = 0; out[o + 5] = 1                       // class IN
            out[o + 6] = 0; out[o + 7] = 0; out[o + 8] = 0; out[o + 9] = 60   // TTL
            out[o + 10] = 0; out[o + 11] = 4                    // RDLENGTH
            out[o + 12] = 0; out[o + 13] = 0; out[o + 14] = 0; out[o + 15] = 0 // 0.0.0.0
        }
        return out
    }

    private fun buildUdpPacket(
        srcIp: ByteArray, dstIp: ByteArray, srcPort: Int, dstPort: Int, payload: ByteArray
    ): ByteArray {
        val total = 20 + 8 + payload.size
        val p = ByteArray(total)
        p[0] = 0x45; p[1] = 0
        p[2] = ((total shr 8) and 0xff).toByte(); p[3] = (total and 0xff).toByte()
        p[8] = 64                                   // TTL
        p[9] = 17                                   // UDP
        System.arraycopy(srcIp, 0, p, 12, 4)
        System.arraycopy(dstIp, 0, p, 16, 4)
        val ck = ipChecksum(p, 0, 20)
        p[10] = ((ck shr 8) and 0xff).toByte(); p[11] = (ck and 0xff).toByte()
        val u = 20
        p[u] = ((srcPort shr 8) and 0xff).toByte(); p[u + 1] = (srcPort and 0xff).toByte()
        p[u + 2] = ((dstPort shr 8) and 0xff).toByte(); p[u + 3] = (dstPort and 0xff).toByte()
        val ulen = 8 + payload.size
        p[u + 4] = ((ulen shr 8) and 0xff).toByte(); p[u + 5] = (ulen and 0xff).toByte()
        // UDP checksum left 0 (permitted for IPv4)
        System.arraycopy(payload, 0, p, u + 8, payload.size)
        return p
    }

    private fun ipChecksum(buf: ByteArray, off: Int, len: Int): Int {
        var sum = 0
        var i = off
        var rem = len
        while (rem > 1) {
            sum += ((buf[i].toInt() and 0xff) shl 8) or (buf[i + 1].toInt() and 0xff)
            i += 2; rem -= 2
        }
        if (rem > 0) sum += (buf[i].toInt() and 0xff) shl 8
        while (sum shr 16 != 0) sum = (sum and 0xffff) + (sum shr 16)
        return sum.inv() and 0xffff
    }

    // ---- lifecycle ----

    private fun stopEverything() {
        running = false
        try { worker?.interrupt() } catch (e: Exception) {}
        try { tun?.close() } catch (e: Exception) {}
        tun = null
        try { pool.shutdownNow() } catch (e: Exception) {}
        scope.cancel()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        stopEverything()
        super.onDestroy()
    }

    override fun onRevoke() {
        // user disabled the VPN from Settings
        stopEverything()
        super.onRevoke()
    }

    private fun notification(text: String): Notification {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "filter1", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("filter1")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }
}
