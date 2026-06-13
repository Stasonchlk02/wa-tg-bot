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
const MAX_RECONNECT = 10

// Убедиться что папка существует
function ensureAuthDir() {
    if (!fs.existsSync(AUTH_DIR)) {
        fs.mkdirSync(AUTH_DIR, { recursive: true })
        console.log('📁 Создана папка auth_info')
    }
}

async function connectToWhatsApp() {
    ensureAuthDir()

    const { version, isLatest } = await fetchLatestBaileysVersion()
    console.log(`📱 Используем WA версию: ${version}`)

    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)

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

    // Запрашиваем pairing code если номер задан и нет сессии
    if (PHONE_NUMBER && !state.creds.registered) {
        setTimeout(async () => {
            try {
                console.log(`📲 Запрашиваю pairing code для ${PHONE_NUMBER}...`)
                const code = await sock.requestPairingCode(PHONE_NUMBER)
                pairingCode = code
                console.log(`\n${'='.repeat(40)}`)
                console.log(`📱 PAIRING CODE: ${code}`)
                console.log(`${'='.repeat(40)}`)
                console.log('Откройте WhatsApp → Настройки → Связанные устройства')
                console.log('→ Привязать устройство → Привязать по номеру телефона')
                console.log(`→ Введите код: ${code}`)
                console.log(`${'='.repeat(40)}\n`)
                connectionStatus = 'waiting_pair'
            } catch (err) {
                console.error('❌ Ошибка запроса pairing code:', err.message)
            }
        }, 3000)
    }

    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, pairingCode: pc } = update

        if (pc) {
            pairingCode = pc
            connectionStatus = 'waiting_pair'
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
            console.log(`❌ Соединение закрыто. Код: ${statusCode}`)

            if (statusCode === DisconnectReason.loggedOut) {
                console.log('🚪 Разлогинен. Удаляю сессию...')
                try {
                    fs.rmSync(AUTH_DIR, { recursive: true, force: true })
                } catch (e) {
                    console.log('Ошибка удаления auth_info:', e.message)
                }
                // Пересоздаём папку сразу
                ensureAuthDir()
                // Переподключаемся заново
                reconnectAttempts = 0
                setTimeout(connectToWhatsApp, 3000)
            } else if (reconnectAttempts < MAX_RECONNECT) {
                reconnectAttempts++
                const delay = Math.min(reconnectAttempts * 5000, 30000)
                console.log(`🔄 Переподключение через ${delay/1000}с (попытка ${reconnectAttempts}/${MAX_RECONNECT})`)
                setTimeout(connectToWhatsApp, delay)
            } else {
                console.log('💀 Превышено число попыток переподключения')
                connectionStatus = 'failed'
            }
        }
    })

    sock.ev.on('creds.update', saveCreds)
    return sock
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

    // Запросить pairing code для произвольного номера
    app.post('/pair', async (req, res) => {
        const { phone } = req.body
        if (!phone) {
            return res.status(400).json({ success: false, error: 'Не передан номер телефона' })
        }
        if (!sock) {
            return res.status(500).json({ success: false, error: 'Socket не инициализирован' })
        }
        try {
            const code = await sock.requestPairingCode(phone)
            pairingCode = code
            console.log(`📱 Pairing code для ${phone}: ${code}`)
            res.json({ success: true, code })
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
            const jid = phone.includes('@s.whatsapp.net')
                ? phone
                : `${phone}@s.whatsapp.net`
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
                const jid = phone.includes('@s.whatsapp.net')
                    ? phone
                    : `${phone}@s.whatsapp.net`
                await sock.sendMessage(jid, { text: message })
                sent++
                console.log(`✅ [${i+1}/${phones.length}] Отправлено: ${phone}`)
                if (delay > 0 && i < phones.length - 1) {
                    await new Promise(r => setTimeout(r, delay * 1000))
                }
            } catch (err) {
                console.error(`❌ Ошибка для ${phone}:`, err.message)
                errors.push({ phone, error: err.message })
            }
        }

        res.json({ success: true, total: phones.length, sent, errors })
    })

    app.post('/logout', async (req, res) => {
        try {
            if (sock) {
                await sock.logout()
            }
        } catch (e) {
            console.log('Logout error (ignored):', e.message)
        }

        isConnected = false
        connectionStatus = 'disconnected'

        try {
            fs.rmSync(AUTH_DIR, { recursive: true, force: true })
        } catch (e) {
            console.log('Ошибка удаления auth_info:', e.message)
        }

        ensureAuthDir()
        res.json({ success: true })

        // Переподключаемся после logout
        setTimeout(connectToWhatsApp, 2000)
    })

    app.listen(PORT, () => {
        console.log(`🌐 WA Bridge API запущен на порту ${PORT}`)
    })
}

startBridge().catch(console.error)
