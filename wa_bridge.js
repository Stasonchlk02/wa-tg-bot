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

function normalizePhone(phone) {
    if (!phone) return ''
    let p = phone.trim().replace(/\s/g, '')
    if (!p.startsWith('+')) p = '+' + p
    return p
}

function ensureAuthDir() {
    if (!fs.existsSync(AUTH_DIR)) {
        fs.mkdirSync(AUTH_DIR, { recursive: true })
        console.log('📁 Создана папка auth_info')
    }
}

async function connectToWhatsApp() {
    ensureAuthDir()

    const { version } = await fetchLatestBaileysVersion()
    console.log(`📱 Используем WA версию: ${version}`)

    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

    // Создаём сокет — БЕЗ автоматического запроса pairing code
    sock = makeWASocket({
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
        const { connection, lastDisconnect, pairingCode: pc } = update

        if (pc) {
            pairingCode = pc
            connectionStatus = 'waiting_pair'
            console.log(`\n${'='.repeat(40)}`)
            console.log(`📱 PAIRING CODE: ${pc}`)
            console.log(`${'='.repeat(40)}`)
            console.log('Откройте WhatsApp → Настройки → Связанные устройства')
            console.log('→ Привязать устройство → Привязать по номеру телефона')
            console.log(`→ Введите код: ${pc}`)
            console.log(`${'='.repeat(40)}\n`)
        }

        if (connection === 'open') {
            isConnected = true
            connectionStatus = 'connected'
            reconnectAttempts = 0
            pairingCode = null
            console.log('✅ WhatsApp подключён!')
        }

        if (connection === 'close') {
            isConnected = false
            connectionStatus = 'disconnected'
            const statusCode = lastDisconnect?.error?.output?.statusCode
            const reason = lastDisconnect?.error?.message || 'unknown'
            console.log(`❌ Соединение закрыто. Код: ${statusCode}, причина: ${reason}`)

            if (statusCode === DisconnectReason.loggedOut) {
                console.log('🚪 Разлогинен. Удаляю сессию...')
                try {
                    fs.rmSync(AUTH_DIR, { recursive: true, force: true })
                } catch (e) {
                    console.log('Ошибка удаления auth_info:', e.message)
                }
                ensureAuthDir()
                // После логаута НЕ переподключаемся автоматически
                // Пользователь должен сам запросить pairing через бота
                connectionStatus = 'logged_out'
            } else if (reconnectAttempts < MAX_RECONNECT) {
                reconnectAttempts++
                const delay = Math.min(reconnectAttempts * 5000, 30000)
                console.log(`🔄 Переподключение через ${delay/1000}с (попытка ${reconnectAttempts}/${MAX_RECONNECT})`)
                setTimeout(connectToWhatsApp, delay)
            } else {
                console.log('💀 Превышено число попыток')
                connectionStatus = 'failed'
            }
        }
    })

    sock.ev.on('creds.update', saveCreds)
}

async function startBridge() {
    ensureAuthDir()
    console.log('⏳ Подключение...')

    await connectToWhatsApp()

    // ── REST API ──────────────────────────────────────────────────────────────

    app.get('/status', (req, res) => {
        res.json({
            connected: isConnected,
            status: connectionStatus,
            hasPairingCode: !!pairingCode,
            pairingCode: pairingCode || null,
        })
    })

    // Pairing запрашивается ТОЛЬКО вручную через этот endpoint
    app.post('/pair', async (req, res) => {
        const rawPhone = req.body.phone || PHONE_NUMBER
        const phone = normalizePhone(rawPhone)

        if (!phone) {
            return res.status(400).json({
                success: false,
                error: 'Номер не передан и WA_PHONE не задан'
            })
        }
        if (!sock) {
            return res.status(500).json({
                success: false,
                error: 'Socket не инициализирован'
            })
        }
        if (isConnected) {
            return res.status(400).json({
                success: false,
                error: 'WhatsApp уже подключён'
            })
        }

        try {
            console.log(`📲 Запрашиваю pairing code для ${phone}...`)
            const code = await sock.requestPairingCode(phone)
            pairingCode = code
            connectionStatus = 'waiting_pair'
            console.log(`📱 Pairing code для ${phone}: ${code}`)
            res.json({ success: true, code, phone })
        } catch (err) {
            console.error('Ошибка pairing:', err.message)
            res.status(500).json({ success: false, error: err.message })
        }
    })

    app.post('/send', async (req, res) => {
        if (!isConnected) {
            return res.status(503).json({ success: false, error: 'WhatsApp не подключён' })
        }
        const { phone, message } = req.body
        if (!phone || !message) {
            return res.status(400).json({ success: false, error: 'Нужны phone и message' })
        }
        try {
            const digits = phone.replace(/\D/g, '')
            const jid = `${digits}@s.whatsapp.net`
            await sock.sendMessage(jid, { text: message })
            res.json({ success: true })
        } catch (err) {
            console.error('Ошибка отправки:', err.message)
            res.status(500).json({ success: false, error: err.message })
        }
    })

    app.post('/send_bulk', async (req, res) => {
        if (!isConnected) {
            return res.status(503).json({ success: false, error: 'WhatsApp не подключён' })
        }
        const { phones, message, delay = 30 } = req.body
        if (!phones || !message) {
            return res.status(400).json({ success: false, error: 'Нужны phones и message' })
        }

        let sent = 0
        const errors = []

        for (let i = 0; i < phones.length; i++) {
            const phone = phones[i]
            try {
                const digits = phone.replace(/\D/g, '')
                const jid = `${digits}@s.whatsapp.net`
                await sock.sendMessage(jid, { text: message })
                sent++
                console.log(`✅ [${i+1}/${phones.length}] Отправлено: ${phone}`)
            } catch (err) {
                console.error(`❌ Ошибка для ${phone}:`, err.message)
                errors.push({ phone, error: err.message })
            }
            if (delay > 0 && i < phones.length - 1) {
                await new Promise(r => setTimeout(r, delay * 1000))
            }
        }

        res.json({ success: true, total: phones.length, sent, errors })
    })

    app.post('/logout', async (req, res) => {
        try {
            if (sock) await sock.logout()
        } catch (e) {
            console.log('Logout error (ignored):', e.message)
        }

        isConnected = false
        connectionStatus = 'logged_out'
        pairingCode = null

        try {
            fs.rmSync(AUTH_DIR, { recursive: true, force: true })
        } catch (e) {
            console.log('Ошибка удаления auth_info:', e.message)
        }

        ensureAuthDir()
        res.json({ success: true })

        // Переподключаемся чтобы сокет был готов к новому pairing
        reconnectAttempts = 0
        setTimeout(connectToWhatsApp, 2000)
    })

    app.listen(PORT, () => {
        console.log(`🌐 WA Bridge API запущен на порту ${PORT}`)
    })
}

startBridge().catch(console.error)
