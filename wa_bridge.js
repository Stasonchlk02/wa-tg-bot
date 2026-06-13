import makeWASocket, { useMultiFileAuthState, DisconnectReason,
    makeCacheableSignalKeyStore, fetchLatestBaileysVersion } from '@whiskeysockets/baileys'
import express from 'express'
import pino from 'pino'
import fs from 'fs'

const logger = pino({ level: 'silent' })
const app = express()
app.use(express.json())
const AUTH_DIR = './auth_info'
const PORT = process.env.WA_PORT || 3001
const PHONE_NUMBER = process.env.WA_PHONE || ''
let sock = null
let isConnected = false
let connectionStatus = 'disconnected'
let pairingCode = null
let reconnectAttempts = 0
const MAX_RECONNECT = 5

async function connectToWhatsApp() {
    const { version, isLatest } = await fetchLatestBaileysVersion()
    console.log(`Using Baileys version ${version}, isLatest: ${isLatest}`)
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
    const sock = makeWASocket({
        version,
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, logger),
        },
        printQRInTerminal: false,
        logger,
        browser: ['WA Bridge', 'Chrome', '120.0.0'],
        defaultQueryTimeoutMs: 10000,
        generateHighQualityLinkPreview: false,
        syncFullHistory: false,
        markOnlineOnConnect: false,
    })

    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr, pairingCode: pc } = update
        if (pc) {
            pairingCode = pc
            console.log(`Pairing code: ${pc}`)
            connectionStatus = 'waiting_pair'
        }
        if (qr) {
            connectionStatus = 'waiting_qr'
            console.log('QR Code received (but we use pairing)')
        }
        if (connection === 'open') {
            isConnected = true
            connectionStatus = 'connected'
            reconnectAttempts = 0
            console.log('WhatsApp connected!')
        }
        if (connection === 'close') {
            isConnected = false
            connectionStatus = 'disconnected'
            const statusCode = lastDisconnect?.error?.output?.statusCode
            const reason = lastDisconnect?.error?.message
            console.log(`Connection closed: ${reason} (code ${statusCode})`)
            if (statusCode === DisconnectReason.loggedOut) {
                console.log('Logged out, deleting auth')
                fs.rmSync(AUTH_DIR, { recursive: true, force: true })
            } else if (reconnectAttempts < MAX_RECONNECT) {
                reconnectAttempts++
                console.log(`Reconnecting in 5s (attempt ${reconnectAttempts})`)
                setTimeout(connectToWhatsApp, 5000)
            } else {
                console.log('Max reconnects reached, give up')
            }
        }
    })

    sock.ev.on('creds.update', saveCreds)
    return sock
}

async function startBridge() {
    sock = await connectToWhatsApp()
    // REST API
    app.get('/status', (req, res) => {
        res.json({
            connected: isConnected,
            status: connectionStatus,
            hasPairingCode: !!pairingCode
        })
    })
    app.post('/pair', async (req, res) => {
        if (!sock) {
            return res.status(500).json({ error: 'Socket not initialized' })
        }
        try {
            const code = await sock.requestPairingCode(PHONE_NUMBER || '')
            pairingCode = code
            res.json({ success: true, code })
        } catch (err) {
            console.error(err)
            res.status(500).json({ success: false, error: err.message })
        }
    })
    app.post('/send', async (req, res) => {
        if (!isConnected) return res.status(503).json({ error: 'Not connected' })
        const { phone, message } = req.body
        if (!phone || !message) return res.status(400).json({ error: 'Missing phone or message' })
        try {
            let jid = phone.includes('@s.whatsapp.net') ? phone : `${phone}@s.whatsapp.net`
            await sock.sendMessage(jid, { text: message })
            res.json({ success: true })
        } catch (err) {
            console.error(err)
            res.status(500).json({ success: false, error: err.message })
        }
    })
    app.post('/send_bulk', async (req, res) => {
        if (!isConnected) return res.status(503).json({ error: 'Not connected' })
        const { phones, message, delay = 30 } = req.body
        if (!phones || !message) return res.status(400).json({ error: 'Missing phones or message' })
        let sent = 0
        const errors = []
        for (let i = 0; i < phones.length; i++) {
            const phone = phones[i]
            try {
                let jid = phone.includes('@s.whatsapp.net') ? phone : `${phone}@s.whatsapp.net`
                await sock.sendMessage(jid, { text: message })
                sent++
                if (delay > 0 && i < phones.length - 1) await new Promise(r => setTimeout(r, delay * 1000))
            } catch (err) {
                errors.push({ phone, error: err.message })
            }
        }
        res.json({ success: true, total: phones.length, sent, errors })
    })
    app.post('/logout', async (req, res) => {
        if (sock) {
            await sock.logout()
            isConnected = false
            connectionStatus = 'disconnected'
            fs.rmSync(AUTH_DIR, { recursive: true, force: true })
            res.json({ success: true })
        } else {
            res.status(500).json({ success: false, error: 'No socket' })
        }
    })
    app.listen(PORT, () => {
        console.log(`WA Bridge listening on port ${PORT}`)
    })
}

startBridge().catch(console.error)
